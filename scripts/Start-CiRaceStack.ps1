# Start-CiRaceStack.ps1 — 在 CI 里拉起一个「离线、隔离」的 Lab 浏览器竞态冒烟测试栈。
#
# 与 scripts/Start-LocalStack.ps1 同构（Start-Process 后台启动 + 捕获 pid + 就绪轮询
# + 按 pid 精准停止），但使用「与 scripts/seed_lab_race_runs.py 完全一致」的文件型
# SQLite 与密钥，且不依赖 Postgres / Redis / 任何网络。仅用于 scripts/Test-WebUi.ps1
# -LabRace 的真实浏览器竞态冒烟。
#
# 用法（CI 内）：
#   pwsh scripts/Start-CiRaceStack.ps1
#
# 行为：后台启动 uvicorn(127.0.0.1:8080) 与 vite(:5173)，轮询 /api/v1/healthz 与首页
# 200 就绪后退出；pid 写入 $env:TEMP/ci-race-*.pid，供清理步骤精准停止「本作业启动的
# 进程」（不会误伤其它进程）。数据库必须已由 seed 脚本创建，否则直接失败退出。
param(
    [string]$BackendPort = 8080,
    [string]$WebPort = 5173
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

# 优先 backend/.venv（CI），回退根 .venv（本地复用现有虚拟环境）。
$py = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $py)) { throw "venv python not found (tried backend/.venv and ./.venv)" }

# DATABASE_URL 优先沿用调用方（CI）传入的值，保证与 seed 步骤指向同一文件型 SQLite；
# 未传时回退到与 seed_lab_race_runs.py 默认 setdefault 完全一致的相对路径。
$dataUrl = $env:DATABASE_URL
if ([string]::IsNullOrEmpty($dataUrl)) {
    $DbFile = Join-Path $backend '.ci-race.sqlite3'
    $dataUrl = "sqlite+pysqlite:///$DbFile"
} else {
    $DbFile = $dataUrl -replace '^sqlite\+pysqlite:///',''
}
$env:DATABASE_URL = $dataUrl
if (-not (Test-Path -LiteralPath $DbFile)) {
    throw "seed database not found at $DbFile — run scripts/seed_lab_race_runs.py first"
}

$env:MARKET_DATA_PROVIDER = 'synthetic'
$env:SECRET_KEY = 'ci-race-secret-key-0123456789'
$env:APP_ENVIRONMENT = 'development'
$env:REDIS_URL = ''
$env:CELERY_BROKER_URL = ''
$env:CELERY_RESULT_BACKEND = ''
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:VITE_API_TARGET = "http://localhost:$BackendPort"

Write-Output "== sqlite db: $DbFile =="

$apiOut = Join-Path $env:TEMP 'ci-race-api.out'
$apiErr = Join-Path $env:TEMP 'ci-race-api.err'
$api = Start-Process -FilePath $py -ArgumentList @(
    '-m', 'uvicorn', 'app.api.main:app', '--host', '127.0.0.1', '--port', "$BackendPort"
) -WorkingDirectory $backend -PassThru -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr
Set-Content -Path (Join-Path $env:TEMP 'ci-race-api.pid') -Value $api.Id
Write-Output "api pid=$($api.Id)"

$ok = $false
for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/v1/healthz" -TimeoutSec 3
        if ($h.status -eq 'alive') { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Output "API did not become alive; tail of stderr:"
    Get-Content -LiteralPath $apiErr -ErrorAction SilentlyContinue | Select-Object -Last 30
    exit 1
}
Write-Output "API alive after ${i}s"

$webOut = Join-Path $env:TEMP 'ci-race-web.out'
$webErr = Join-Path $env:TEMP 'ci-race-web.err'
# npm 是 .cmd 包装；经 cmd.exe 启动以便 taskkill /T 能连带停止其 node 子进程。
$web = Start-Process -FilePath 'cmd.exe' -ArgumentList @(
    '/c', "npm run dev -- --port $WebPort --strictPort"
) -WorkingDirectory $frontend -PassThru -RedirectStandardOutput $webOut -RedirectStandardError $webErr
Set-Content -Path (Join-Path $env:TEMP 'ci-race-web.pid') -Value $web.Id
Write-Output "web pid=$($web.Id)"

$webOk = $false
for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$WebPort/" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $webOk = $true; break }
    } catch { }
}
if ($webOk) {
    Write-Output "vite ready after ${i}s -> http://localhost:$WebPort"
}
else {
    Write-Output "vite did not come up; tail of stderr:"
    Get-Content -LiteralPath $webErr -ErrorAction SilentlyContinue | Select-Object -Last 30
    Write-Output "START_CI_RACE_STACK_FAILED (web did not answer on :$WebPort)"
    exit 1
}

Write-Output ""
Write-Output "api pid=$($api.Id)  web pid=$($web.Id)"
Write-Output "stop with: taskkill /PID $($api.Id) /T /F ; taskkill /PID $($web.Id) /T /F"