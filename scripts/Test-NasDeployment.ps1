# Test-NasDeployment.ps1 — end-to-end health check of a deployed My Quant Lab stack.
#
# Exercises the real deployment over HTTP, in the same order the product's own
# workflow does, so a broken release shows up as a specific failing step rather
# than a vague "page doesn't load".
#
# Usage:
#   pwsh -NoProfile -File scripts\Test-NasDeployment.ps1
#   pwsh -NoProfile -File scripts\Test-NasDeployment.ps1 -Base http://192.168.2.2:8081
#   pwsh -NoProfile -File scripts\Test-NasDeployment.ps1 -Token <api-token> -SkipMutations
param(
    [string]$Base = 'http://192.168.2.2:8081',
    [string]$Token = '',
    [string]$Symbol = 'DEMO-AAPL',
    [string]$Provider = 'synthetic',
    [switch]$SkipMutations
)

$ErrorActionPreference = 'Continue'
$api = "$Base/api/v1"
$results = [System.Collections.Generic.List[object]]::new()

function Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Output ""
    Write-Output "── $Name"
    try {
        $value = & $Body
        $results.Add([pscustomobject]@{ Step = $Name; Ok = $true; Detail = "$value" })
        Write-Output "   OK   $value"
    }
    catch {
        $results.Add([pscustomobject]@{ Step = $Name; Ok = $false; Detail = $_.Exception.Message })
        Write-Output "   FAIL $($_.Exception.Message)"
    }
}

function Invoke-Api {
    param([string]$Method = 'GET', [string]$Path, $Body)
    $headers = @{}
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    $params = @{
        Uri         = "$api$Path"
        Method      = $Method
        Headers     = $headers
        TimeoutSec  = 60
        ErrorAction = 'Stop'
    }
    if ($null -ne $Body) {
        $params['Body'] = ($Body | ConvertTo-Json -Depth 12)
        $params['ContentType'] = 'application/json'
    }
    try {
        return Invoke-RestMethod @params
    }
    catch {
        $detail = $_.Exception.Message
        $resp = $_.Exception.Response
        if ($resp) {
            try {
                $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
                $payload = $reader.ReadToEnd()
                if ($payload) { $detail = "$detail | body: $($payload.Substring(0, [Math]::Min(400, $payload.Length)))" }
            }
            catch { }
        }
        throw $detail
    }
}

Write-Output "My Quant Lab — NAS 端到端检查"
Write-Output "target: $Base   time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ---- 1. The web container serves the UI and proxies /healthz ---------------
Step 'Web 容器 /healthz' {
    $r = Invoke-WebRequest -Uri "$Base/healthz" -TimeoutSec 20 -UseBasicParsing
    "HTTP $($r.StatusCode)"
}

Step 'Web 容器首页 (/)' {
    $r = Invoke-WebRequest -Uri "$Base/" -TimeoutSec 20 -UseBasicParsing
    if ($r.Content -notmatch '<div id="app"') { throw '首页不含 <div id="app"> 挂载点，可能是 nginx 默认页或前端构建产物缺失' }
    "HTTP $($r.StatusCode), html=$($r.RawContentLength)B"
}

# ---- 2. API liveness + readiness -----------------------------------------
Step 'API /healthz (存活探针)' {
    $r = Invoke-Api -Path '/healthz'
    if ($null -eq $r) { 'empty body' } else { ($r | ConvertTo-Json -Compress -Depth 5) }
}

Step 'API /health (依赖 + 库结构版本)' {
    $r = Invoke-Api -Path '/health'
    $json = $r | ConvertTo-Json -Compress -Depth 6
    $status = "$($r.status)"
    $migration = "$($r.migration)"
    if ($status -ne 'healthy') { throw "status=$status (期望 healthy): $json" }
    # The step used to be called "含依赖与迁移状态" while /health reported no schema
    # version at all: the name promised a check nobody made (ADR-071). It does now.
    if (-not $migration -or $migration -eq 'unknown' -or $migration -eq 'none') {
        throw "migration='$migration' —— 这个部署说不出自己跑在哪一版库结构上（unknown=读不到版本表，none=迁移从未运行）: $json"
    }
    "status=$status migration=$migration database=$($r.database) redis=$($r.redis) workers=$($r.workers)"
}

Step 'API /system/info (运行时版本与模块)' {
    $r = Invoke-Api -Path '/system/info'
    "version=$($r.version) env=$($r.environment) provider=$($r.market_data_provider) modules=$(@($r.modules).Count)"
}

# ---- 3. Core domain: data -> strategy -> backtest -> signal --------------
if ($SkipMutations) {
    Write-Output "`n(--SkipMutations: 跳过写入类步骤)"
}
else {
    Step "行情同步 ($Symbol)" {
        # The deployment's default provider may be `yahoo_finance`, for which the
        # synthetic demo tickers have no data at all. Pin the provider explicitly so
        # this step tests the pipeline rather than the operator's provider choice.
        $body = @{ symbol = $Symbol; timeframe = '1d'; lookback_days = 400 }
        if ($Provider) { $body['provider'] = $Provider }
        $r = Invoke-Api -Method POST -Path '/market-data/sync' -Body $body

        # Syncing is idempotent (docs/12 + test_market_data_sync_and_series): a
        # second run legitimately inserts 0 rows. Demanding inserted > 0 would fail
        # every repeat check, so assert the series actually holds data instead.
        if ($r.inserted -lt 0) { throw "inserted=$($r.inserted)" }
        if (-not $r.series_id) { throw "no series_id returned: $($r | ConvertTo-Json -Compress)" }
        $bars = Invoke-Api -Path "/market-data/series/$($r.series_id)/bars?limit=5"
        $count = @($bars).Count
        if ($count -eq 0) { throw "series $($r.series_id) has no bars" }
        "inserted=$($r.inserted) total=$($r.closed_bars_in_fetch) quality=$($r.quality_status) readback=$count bars"
    }

    $script:strategyId = $null
    Step '创建策略' {
        $r = Invoke-Api -Method POST -Path '/strategies' -Body @{ name = "NAS 检查 $(Get-Date -Format 'HHmmss')" }
        $script:strategyId = $r.id
        "id=$($r.id)"
    }

    Step '创建策略版本（校验不可变触发器）' {
        if (-not $script:strategyId) { throw '上一步未拿到 strategy id' }
        $dsl = @{
            schema_version = '1.0'
            strategy = @{ id = 'nas-check'; name = 'NAS Check EMA Cross'; version = '1.0.0' }
            market   = @{ asset_classes = @('stock'); timeframes = @('1d') }
            entry    = @{ long = @{ all = @(@{ op = 'crosses_above'; left = 'ema20'; right = 'ema50' }) } }
            exit     = @{ long = @{ any = @(@{ op = 'crosses_below'; left = 'ema20'; right = 'ema50' }) } }
            risk     = @{ stop_loss_atr_multiple = 2.0; take_profit_r_multiple = 2.0 }
            execution = @{ fill_model = 'next_bar_open'; fee_bps = 10; slippage_bps = 5; initial_capital = 10000 }
        }
        $r = Invoke-Api -Method POST -Path "/strategies/$($script:strategyId)/versions" -Body @{ version = '1.0.0'; dsl = $dsl }
        $script:versionId = $r.id
        "version id=$($r.id) hash=$($r.immutable_hash)"
    }

    Step '运行回测' {
        if (-not $script:versionId) { throw '上一步未拿到 version id' }
        $r = Invoke-Api -Method POST -Path '/backtests' -Body @{
            strategy_version_id = $script:versionId; symbol = $Symbol; timeframe = '1d'
        }
        if ($r.status -ne 'completed') { throw "status=$($r.status)" }
        "status=$($r.status) trades=$(@($r.trades).Count)"
    }

    Step '扫描信号' {
        $r = Invoke-Api -Method POST -Path '/signals/scan'
        "evaluated=$($r.evaluated)"
    }

    Step 'AI 任务详情端点 (v0.9.9 修复的接口)' {
        # Regression guard for the v0.9.9 breakage. A missing route ALSO answers
        # 404, so assert the path is really registered before probing it.
        #
        # The schema lives at the app root, NOT under the API prefix: the routers
        # are mounted at /api/v1 but openapi_url stays "/openapi.json". Probing
        # $api/openapi.json returns 404, which would make this check pass or fail
        # for entirely the wrong reason (it did exactly that on the first run).
        $schemaUrl = "$Base/openapi.json"
        try {
            $schema = Invoke-RestMethod -Uri $schemaUrl -TimeoutSec 30 -ErrorAction Stop
        }
        catch {
            throw "无法读取 OpenAPI schema（$schemaUrl）：$($_.Exception.Message)"
        }
        $paths = @($schema.paths.PSObject.Properties.Name)
        if ($paths.Count -eq 0) { throw "OpenAPI schema 为空：$schemaUrl" }
        $wanted = @('/api/v1/ai/tasks/{task_id}', '/api/v1/ai/tasks/{task_id}/status')
        $missing = @($wanted | Where-Object { $paths -notcontains $_ })
        if ($missing.Count -gt 0) { throw "OpenAPI 未注册路由: $($missing -join ', ')" }

        # An unknown id must be 404 (not 500/405). A 200 here would mean the id
        # lookup is broken.
        try {
            Invoke-Api -Path '/ai/tasks/999999' | Out-Null
            throw '期望 404，却返回了 200'
        }
        catch {
            if ($_.Exception.Message -match '404') { '两个路由均已注册；unknown id -> 404 (符合预期)' }
            else { throw $_ }
        }
    }

    Step '模拟盘账户列表' {
        $r = Invoke-Api -Path '/paper/accounts'
        "accounts=$(@($r).Count)"
    }
}

# ---- summary -------------------------------------------------------------
Write-Output ""
Write-Output "================ 汇总 ================"
$results | Format-Table -AutoSize Step, Ok, Detail | Out-String | Write-Output
$failed = @($results | Where-Object { -not $_.Ok })
Write-Output "通过 $($results.Count - $failed.Count)/$($results.Count)"
if ($failed.Count -gt 0) {
    Write-Output "失败步骤："
    $failed | ForEach-Object { Write-Output "  - $($_.Step): $($_.Detail)" }
    exit 1
}
Write-Output "全部通过。"
exit 0
