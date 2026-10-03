# Start-LocalStack.ps1 — boot a local backend + frontend to verify the UI in a real
# browser without touching the NAS deployment.
#
# Backed by a file SQLite database (no Postgres/Redis needed) and the synthetic
# market data provider, so the whole thing is offline and deterministic. Intended to
# pair with scripts\Test-WebUi.ps1 -Base http://localhost:5173.
#
# Usage:
#   pwsh -NoProfile -File scripts\Start-LocalStack.ps1              # start both
#   pwsh -NoProfile -File scripts\Start-LocalStack.ps1 -ApiOnly
param(
    [string]$BackendPort = 8080,
    [string]$WebPort = 5173,
    [switch]$ApiOnly,
    [switch]$NoMigrate
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw "venv python not found at $py" }

$dbFile = Join-Path $backend '.local-verify.sqlite3'
$env:DATABASE_URL = "sqlite+pysqlite:///$dbFile"
$env:MARKET_DATA_PROVIDER = 'synthetic'
$env:SECRET_KEY = 'local-verify-secret-key-0123456789'
$env:APP_ENVIRONMENT = 'development'
$env:REDIS_URL = ''
$env:CELERY_BROKER_URL = ''
$env:CELERY_RESULT_BACKEND = ''
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

Write-Output "== sqlite db: $dbFile =="

if (-not $NoMigrate) {
    Write-Output "== alembic upgrade head =="
    Push-Location $backend
    try {
        & $py -m alembic upgrade head 2>&1 | Select-Object -Last 6
        Write-Output "   alembic exit=$LASTEXITCODE"
    }
    finally { Pop-Location }
}

Write-Output "== starting uvicorn on $BackendPort =="
$api = Start-Process -FilePath $py -ArgumentList @(
    '-m', 'uvicorn', 'app.api.main:app', '--host', '127.0.0.1', '--port', "$BackendPort"
) -WorkingDirectory $backend -PassThru -RedirectStandardOutput (Join-Path $env:TEMP 'mql-api.out') `
    -RedirectStandardError (Join-Path $env:TEMP 'mql-api.err')
Write-Output "api pid=$($api.Id)"

$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/v1/healthz" -TimeoutSec 3
        if ($h.status -eq 'alive') { $ok = $true; break }
    }
    catch { }
}
if (-not $ok) {
    Write-Output "API did not become alive; tail of stderr:"
    Get-Content -LiteralPath (Join-Path $env:TEMP 'mql-api.err') -ErrorAction SilentlyContinue | Select-Object -Last 30
    exit 1
}
Write-Output "API alive after ${i}s"

if ($ApiOnly) {
    Write-Output "api pid $($api.Id) — stop with: Stop-Process -Id $($api.Id)"
    return
}

Write-Output "== starting vite dev server on $WebPort =="
$frontend = Join-Path $root 'frontend'
# npm is a .cmd shim; run it via cmd.exe from a local copy or the working dir must
# be reachable by cmd.exe. Under a UNC workspace address the repo through a mapped
# drive if one exists.
$fePath = $frontend
if ($frontend -match '^\\\\([^\\]+)\\(.+)') {
    $share = "\\$($Matches[1])\$(($Matches[2] -split '\\')[0])"
    $mapped = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayRoot -and $share -like "$($_.DisplayRoot)*" } | Select-Object -First 1
    if ($mapped) {
        $rel = $Matches[2].Substring(($Matches[2] -split '\\')[0].Length).TrimStart('\')
        $fePath = "$($mapped.Name):\$rel"
        Write-Output "   (addressed via $($mapped.Name): for cmd.exe)"
    }
    else {
        Write-Output "   WARNING: UNC path and no mapped drive found; vite dev may fail."
    }
}

$web = Start-Process -FilePath 'cmd.exe' -ArgumentList @(
    '/c', "npm run dev -- --port $WebPort --strictPort"
) -WorkingDirectory $fePath -PassThru -RedirectStandardOutput (Join-Path $env:TEMP 'mql-web.out') `
    -RedirectStandardError (Join-Path $env:TEMP 'mql-web.err')
Write-Output "web pid=$($web.Id)"

$webOk = $false
foreach ($i in 1..60) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$WebPort/" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $webOk = $true; break }
    }
    catch { }
}
if ($webOk) {
    Write-Output "vite ready after ${i}s -> http://localhost:$WebPort"
}
else {
    Write-Output "vite did not come up; tail of stderr:"
    Get-Content -LiteralPath (Join-Path $env:TEMP 'mql-web.err') -ErrorAction SilentlyContinue | Select-Object -Last 30
    # Same shape as the API half above: a self-check that cannot fail is decoration
    # (ADR-073). The caller has to be able to tell "the stack is up" from "it is not".
    Write-Output "START_LOCAL_STACK_FAILED (web did not answer on :$WebPort)"
    exit 1
}

Write-Output ""
Write-Output "api pid=$($api.Id)  web pid=$($web.Id)"
Write-Output "stop with: Stop-Process -Id $($api.Id),$($web.Id) -Force"
