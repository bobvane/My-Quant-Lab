# Test-WebUi.ps1 — verify the SPA actually mounts and renders in a real browser.
#
# Serving index.html proves nothing about a Vue app: the shell is static and the
# views only exist after the bundle runs. This drives headless Chrome (--dump-dom
# by default) so the assertions are made against the *executed* DOM, which catches
# a bundle that boots but throws during mount, a router that fails to resolve a
# view, or a panel that silently renders nothing.
#
# Usage:
#   pwsh -NoProfile -File scripts\Test-WebUi.ps1
#   pwsh -NoProfile -File scripts\Test-WebUi.ps1 -Base http://192.168.2.2:8081
#   pwsh -NoProfile -File scripts\Test-WebUi.ps1 -Base http://localhost:5173 -ExpectSensitivity
#
# Lab 导航 / 乱序竞态冒烟（零新增依赖）：
#   pwsh -NoProfile -File scripts\Test-WebUi.ps1 -LabRace -Base http://localhost:5173
#
#   -LabRace 复用同一 headless Chrome 调试端口：先用 CDP 取「执行后」的 DOM 跑首页/
#   回测检查（绕过 vite-dev 的 ESM 懒加载，--dump-dom 抓到空壳的问题），再交给
#   scripts/LabRaceDriver.mjs（Node 内置全局 WebSocket + Chrome DevTools Protocol）驱动：
#     1) 普通导航：打开研究 A，再经 history.pushState + popstate 切到 B，断言详情
#        面板显示 B 且地址栏 URL 含 run=B；
#     2) 乱序竞态：用 Fetch 拦截在网络层“确定性”地让 A 的详情响应晚于 B 回到前端
#        （真实乱序，不是 sleep 伪造计时），断言 runRequestSeq 守卫丢弃迟到的 A，
#        详情面板最终只有 B、URL 仍含 run=B。
#   不引入 Playwright 等任何新增包；Node 已是项目依赖，全局 WebSocket 自 Node 18 起内置。
param(
    [string]$Base = 'http://192.168.2.2:8081',
    # Assert the v1.1 parameter-sensitivity panel is present (needs >= v1.1.0).
    [switch]$ExpectSensitivity,
    [int]$RenderWaitMs = 10000,
    [string]$Chrome = '',
    # 运行 Lab 真实浏览器导航/乱序竞态冒烟（依赖本地已拉起的 vite 前端 + 后端）。
    [switch]$LabRace,
    [string]$LabFrontend = 'http://localhost:5173',
    [string]$LabBackend = 'http://localhost:8080',
    [int]$LabRunA = 1,
    [int]$LabRunB = 2,
    [int]$LabDebugPort = 9342
)

$ErrorActionPreference = 'Continue'
$script:DomMode = 'dump'
$script:LabDebugPort = $LabDebugPort
$script:NodeBin = $null
$script:ChromeProc = $null
$script:LabProfile = $null
$script:ChromeExe = $null

# -LabRace 时默认把首页检查也指向本地 vite 前端（若未显式传 -Base）。
if ($LabRace -and $Base -eq 'http://192.168.2.2:8081') { $Base = $LabFrontend }

function Resolve-Chrome {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path -LiteralPath $Explicit)) { return $Explicit }
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    foreach ($p in @(
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            (Join-Path $pf86 'Google\Chrome\Application\chrome.exe'),
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            (Join-Path $pf86 'Microsoft\Edge\Application\msedge.exe')
        )) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    throw "no Chrome/Edge binary found; pass -Chrome <path>"
}

function Get-RenderedDom {
    param([string]$Url, [int]$WaitMs)

    $profile = Join-Path ([System.IO.Path]::GetTempPath()) ("mql-chrome-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $profile -Force | Out-Null
    $out = Join-Path $profile 'dom.html'
    $err = Join-Path $profile 'dom.err'
    $exe = Resolve-Chrome -Explicit $Chrome

    # Start-Process + -Wait is required: piping chrome.exe directly returns before
    # the browser has written anything, so the DOM comes back empty.
    $proc = Start-Process -FilePath $exe -ArgumentList @(
        '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        '--disable-extensions', '--disable-background-networking',
        "--user-data-dir=$profile",
        "--virtual-time-budget=$WaitMs",
        '--dump-dom', $Url
    ) -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -Wait

    $dom = if (Test-Path -LiteralPath $out) { Get-Content -LiteralPath $out -Raw } else { '' }
    Remove-Item -LiteralPath $profile -Recurse -Force -ErrorAction SilentlyContinue
    return @{ Html = $dom; ExitCode = $proc.ExitCode }
}

# 启动一个开启 DevTools 调试端口的 headless Chrome（供 -LabRace 全程复用）。
function Start-LabChrome {
    # 先清理任何仍占用调试端口的旧 headless chrome（历史调试会堆积大量
    # --headless=new 进程，若端口被旧实例占用，新实例无法绑定，CDP 会连到
    # 没有正确 user-data-dir 的旧实例，进而拿到空 DOM / 驱动失败）。
    $script:ChromeExe = Resolve-Chrome -Explicit $Chrome
    try {
        $pids = (Get-NetTCPConnection -LocalPort $LabDebugPort -ErrorAction SilentlyContinue).OwningProcess
        foreach ($p in ($pids | Sort-Object -Unique)) {
            try { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } catch { }
        }
    } catch { }
    Start-Sleep -Milliseconds 500
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCmd) { throw 'PATH 中未找到 node（需要 Node >=18 提供全局 WebSocket）' }
    $script:NodeBin = $nodeCmd.Source

    $port = $LabDebugPort
    $script:LabDebugPort = $port
    $profile = Join-Path ([System.IO.Path]::GetTempPath()) ("mql-race-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $profile -Force | Out-Null
    $script:LabProfile = $profile
    $clog = Join-Path $profile 'chrome.log'

    $script:ChromeProc = Start-Process -FilePath $script:ChromeExe -ArgumentList @(
        '--headless=new', '--no-sandbox', '--disable-gpu', '--no-first-run',
        '--no-default-browser-check', '--disable-extensions', '--disable-background-networking',
        '--remote-allow-origins=*',
        "--remote-debugging-port=$port", "--user-data-dir=$profile"
    ) -RedirectStandardError $clog -PassThru -WindowStyle Hidden

    $up = $false
    for ($i = 0; $i -lt 80; $i++) {
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/json/version" -TimeoutSec 1
            if ($r.webSocketDebuggerUrl) { $up = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    if (-not $up) { throw 'headless Chrome 调试端口在超时内未就绪' }
}

function Stop-LabChrome {
    try {
        if ($script:ChromeProc) {
            # 杀掉整个进程树（chrome 会派生多个渲染/网络子进程），避免残留占端口。
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.ParentProcessId -eq $script:ChromeProc.Id } |
                ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch { } }
            Stop-Process -Id $script:ChromeProc.Id -Force -ErrorAction SilentlyContinue
        }
    } catch { }
    Start-Sleep -Milliseconds 400
    if ($script:LabProfile) { Remove-Item -LiteralPath $script:LabProfile -Recurse -Force -ErrorAction SilentlyContinue }
}

# 用 Node 驱动 CDP 取「执行后」的 DOM（绕过 vite-dev 的 ESM 懒加载）。
function Invoke-CdpDom {
    param([string]$Url, [int]$WaitMs)
    $render = Join-Path $PSScriptRoot 'LabRenderDom.mjs'
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("mql-dom-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".html")
    & $script:NodeBin @($render, '--debugPort', "$script:LabDebugPort", '--url', $Url, '--waitMs', "$WaitMs", '--waitFor', 'My Quant Lab') 2>$null > $tmp
    $html = if (Test-Path -LiteralPath $tmp) { Get-Content -LiteralPath $tmp -Raw } else { '' }
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    return $html
}

# dump-dom（默认）或 CDP（LabRace）两种取 DOM 方式。
function Get-PageDom {
    param([string]$Url, [int]$WaitMs)
    if ($script:DomMode -eq 'cdp') { return (Invoke-CdpDom -Url $Url -WaitMs $WaitMs) }
    return (Get-RenderedDom -Url $Url -WaitMs $WaitMs).Html
}

function Invoke-LabRaceDriver {
    param()
    $driver = Join-Path $PSScriptRoot 'LabRaceDriver.mjs'
    if (-not (Test-Path -LiteralPath $driver)) { throw "缺少驱动脚本：$driver" }
    $out = & $script:NodeBin @(
        $driver,
        '--debugPort', "$script:LabDebugPort",
        '--frontend', $LabFrontend,
        '--backend', $LabBackend,
        '--runA', "$LabRunA",
        '--runB', "$LabRunB"
    ) 2>&1
    $out | ForEach-Object { Write-Output ("    [driver] " + $_) }
    if ($LASTEXITCODE -ne 0) { throw ("LabRaceDriver 返回非零退出码 " + $LASTEXITCODE) }
    'PASS'
}

$results = [System.Collections.Generic.List[object]]::new()
function Check {
    param([string]$Name, [scriptblock]$Body)
    try {
        $value = & $Body
        $results.Add([pscustomobject]@{ Check = $Name; Ok = $true; Detail = "$value" })
        Write-Output "   OK   $Name — $value"
    }
    catch {
        $results.Add([pscustomobject]@{ Check = $Name; Ok = $false; Detail = $_.Exception.Message })
        Write-Output "   FAIL $Name — $($_.Exception.Message)"
    }
}

if ($LabRace) { Start-LabChrome; $script:DomMode = 'cdp' }

Write-Output "My Quant Lab — Web UI render check"
Write-Output "target: $Base   time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "chrome: $(Resolve-Chrome -Explicit $Chrome)"

$dom = Get-PageDom -Url "$Base/" -WaitMs $RenderWaitMs

Write-Output ""
Write-Output "── 首页（/）"
Check '拿到渲染后的 DOM' {
    if (-not $dom) { throw 'empty DOM' }
    "$([Math]::Round($dom.Length / 1KB, 1))KB"
}
Check 'Vue 已挂载（渲染出真实视图）' {
    # NB: do NOT assert that <div id="app"> exists. Vue 3 *replaces* the mount
    # element, so in a successfully rendered DOM that div is gone — its presence
    # would mean the bundle never ran. Assert on rendered content instead.
    $elements = ([regex]::Matches($dom, '<(div|section|nav|table|h1|h2|h3|button|a|span|p)\b')).Count
    if ($elements -lt 20) {
        throw "only $elements elements in the DOM — the bundle likely threw during mount"
    }
    "$elements elements rendered"
}
Check '页脚带版本号（证明读到了后端 /system/info）' {
    $m = [regex]::Match($dom, 'My Quant Lab\s+(v?[\d.]+)')
    if (-not $m.Success) { throw 'footer version not found' }
    $m.Groups[1].Value
}
Check '没有渲染期报错痕迹' {
    if ($dom -match 'Failed to resolve component|Cannot read propert(y|ies) of (undefined|null)|Uncaught') {
        throw 'DOM contains a Vue/runtime error message'
    }
    'clean'
}
Check '导航已渲染' {
    if ($dom -notmatch '行情|策略|回测|信号|模拟') { throw 'no nav labels found' }
    'ok'
}

# The v1.1 parameter-sensitivity panel lives in the backtest view.
if ($ExpectSensitivity) {
    Write-Output ""
    Write-Output "── 回测实验室（/backtest）"
    $bt = @{ Html = (Get-PageDom -Url "$Base/backtest" -WaitMs $RenderWaitMs) }
    Check '路由 /backtest 渲染出回测视图' {
        if ($bt.Html -notmatch '权益曲线|回测|Walk-Forward') { throw 'backtest view did not render' }
        'ok'
    }
    Check '参数敏感性分析面板存在' {
        if ($bt.Html -notmatch '参数敏感性') { throw '未找到「参数敏感性分析」面板' }
        'ok'
    }
    Check '面板包含网格输入框与指标选择' {
        if ($bt.Html -notmatch 'trend_period:5,10,20|运行敏感性分析') {
            throw '未找到网格输入框或运行按钮'
        }
        if ($bt.Html -notmatch 'sharpe') { throw '未找到指标选择项' }
        'ok'
    }
}

# Lab 真实浏览器导航 / 乱序竞态冒烟（零新增依赖，CDP Fetch 确定性拦截）。
if ($LabRace) {
    Write-Output ""
    Write-Output "── Lab 导航 / 乱序竞态冒烟（-LabRace）"
    Check 'Lab 真实浏览器导航与乱序竞态（CDP Fetch 确定性拦截）' {
        Invoke-LabRaceDriver
    }
    Stop-LabChrome
}

Write-Output ""
Write-Output "================ 汇总 ================"
$failed = @($results | Where-Object { -not $_.Ok })
Write-Output "通过 $($results.Count - $failed.Count)/$($results.Count)"
if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Write-Output "  - $($_.Check): $($_.Detail)" }
    exit 1
}
Write-Output '全部通过。'
exit 0
