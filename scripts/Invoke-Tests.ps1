# Invoke-Tests.ps1 — run backend tests + lint exactly as CI does.
#
# Why this exists: CI runs `pytest -o addopts=` followed by
# `ruff check app tests` and `ruff format --check app tests`. Reproducing that
# locally on Windows has two traps this script removes:
#
#   1. pytest's default temp handling creates a `pytest-current` symlink under
#      %TEMP% that cannot be cleaned up on a network share and aborts the run
#      with PermissionError [WinError 5]. We pin --basetemp instead.
#   2. A non-UTF-8 console mangles the Chinese path in tracebacks. We force UTF-8.
#
# Usage:
#   pwsh -NoProfile -File scripts\Invoke-Tests.ps1
#   pwsh -NoProfile -File scripts\Invoke-Tests.ps1 -Path tests/test_backtest.py
#   pwsh -NoProfile -File scripts\Invoke-Tests.ps1 -Keyword lookahead -Verbose
#   pwsh -NoProfile -File scripts\Invoke-Tests.ps1 -SkipLint
param(
    [string[]]$Path = @(),
    [string]$Keyword,
    [switch]$Verbose,
    [switch]$SkipLint
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $py)) { throw "venv python not found at $py" }

# Hermetic environment: the suite must not depend on the shell or a local .env.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:DATABASE_URL = 'sqlite+pysqlite:///:memory:'
$env:MARKET_DATA_PROVIDER = 'synthetic'
$env:SECRET_KEY = 'test-secret-key-0123456789abcdef'
$env:APP_ENVIRONMENT = 'test'

$basetemp = Join-Path ([System.IO.Path]::GetTempPath()) ("mql-pytest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $basetemp -Force | Out-Null

$failures = @()

Push-Location $backend
try {
    $pytestArgs = @('-m', 'pytest', '-o', 'addopts=', '-p', 'no:cacheprovider',
                    "--basetemp=$basetemp", '-q')
    $pytestArgs += if ($Verbose) { @('-vv', '-rA') } else { @('-ra') }
    if ($Keyword) { $pytestArgs += @('-k', $Keyword) }
    if ($Path.Count -gt 0) { $pytestArgs += $Path }

    Write-Output "== pytest $($pytestArgs -join ' ') =="
    $out = & $py @pytestArgs 2>&1
    $code = $LASTEXITCODE
    $lines = ($out | Out-String) -split "`r?`n"
    # The tail holds the failure details and the summary line.
    Write-Output (($lines | Select-Object -Last $(if ($Verbose) { 400 } else { 120 })) -join "`n")
    Write-Output "== pytest exit code: $code =="
    if ($code -ne 0) { $failures += 'pytest' }

    if (-not $SkipLint) {
        Write-Output "`n== ruff check app tests =="
        & $py -m ruff check app tests 2>&1 | Select-Object -Last 20
        if ($LASTEXITCODE -ne 0) { $failures += 'ruff-check' }

        Write-Output "`n== ruff format --check app tests =="
        & $py -m ruff format --check app tests 2>&1 | Select-Object -Last 20
        if ($LASTEXITCODE -ne 0) { $failures += 'ruff-format' }
    }
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $basetemp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output ""
if ($failures.Count -eq 0) {
    Write-Output '== BACKEND OK =='
    exit 0
}
Write-Output "== BACKEND FAILED: $($failures -join ', ') =="
exit 1
