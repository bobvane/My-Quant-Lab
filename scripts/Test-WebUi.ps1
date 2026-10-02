# Test-WebUi.ps1 — verify the SPA actually mounts and renders in a real browser.
#
# Serving index.html proves nothing about a Vue app: the shell is static and the
# views only exist after the bundle runs. This drives headless Chrome (--dump-dom)
# so the assertions are made against the *executed* DOM, which catches a bundle
# that boots but throws during mount, a router that fails to resolve a view, or a
# panel that silently renders nothing.
#
# Usage:
#   pwsh -NoProfile -File scripts\Test-WebUi.ps1
#   pwsh -NoProfile -File scripts\Test-WebUi.ps1 -Base http://192.168.2.2:8081
#   pwsh -NoProfile -File scripts\Test-WebUi.ps1 -Base http://localhost:5173 -ExpectSensitivity
param(
    [string]$Base = 'http://192.168.2.2:8081',
    # Assert the v1.1 parameter-sensitivity panel is present (needs >= v1.1.0).
    [switch]$ExpectSensitivity,
    [int]$RenderWaitMs = 10000,
    [string]$Chrome = ''
)

$ErrorActionPreference = 'Continue'

function Resolve-Chrome {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path -LiteralPath $Explicit)) { return $Explicit }
    foreach ($p in @(
            "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
        )) {
        if (Test-Path -LiteralPath $p) { return $p }
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

Write-Output "My Quant Lab — Web UI render check"
Write-Output "target: $Base   time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Output "chrome: $(Resolve-Chrome -Explicit $Chrome)"

$root = Get-RenderedDom -Url "$Base/" -WaitMs $RenderWaitMs
$dom = $root.Html

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
    $bt = Get-RenderedDom -Url "$Base/backtest" -WaitMs $RenderWaitMs
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
