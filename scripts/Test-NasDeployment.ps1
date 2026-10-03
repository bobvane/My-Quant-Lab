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
#   pwsh -NoProfile -File scripts\Test-NasDeployment.ps1 -ExpectVersion 1.5.10
param(
    [string]$Base = 'http://192.168.2.2:8081',
    [string]$Token = '',
    [string]$Symbol = 'DEMO-AAPL',
    [string]$Provider = 'synthetic',
    [string]$ExpectVersion = '',
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

# Every step must be able to fail. A step that only prints what it received turns a
# broken deployment into an "OK" line, which is how the /health step spent several
# releases claiming to check the migration state while checking nothing (ADR-071).
function Assert-Value {
    param([string]$Label, $Value, [string]$Pattern = '')
    if ($null -eq $Value) { throw "$Label 缺失（响应里没有这个字段）" }
    $text = "$Value".Trim()
    if ($text -eq '') { throw "$Label 为空" }
    if ($Pattern -and $text -notmatch $Pattern) { throw "$Label='$text' 不符合 $Pattern" }
    return $text
}

# Immutability is only proven by being refused, so "expect this to fail" needs its own
# helper rather than a try/catch that swallows whatever it gets.
function Assert-Rejected {
    # The scriptblock parameter must NOT be named `Body`: a step body reads its own
    # `$body` payload from inside this scriptblock, and `$body`/`$Body` are one and the
    # same variable, so the probe posted this helper instead of the version — the run
    # reported "An item with the same key has already been added" instead of looking for
    # the 422 it was written to find.
    param([string]$What, [scriptblock]$Action)
    try {
        & $Action | Out-Null
    }
    catch {
        if ($_.Exception.Message -match '\b422\b|\b409\b|\b400\b') { return $_.Exception.Message }
        throw "$What 被拒绝，但理由不是校验失败（期望 4xx 校验类错误）：$($_.Exception.Message)"
    }
    throw "$What 竟然成功了 —— 期望它被拒绝"
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

# The status code of a request that is *expected* to fail, read from the response
# itself. Matching a status against the English prose of the error made the
# unknown-id check unable to fail: its own success message ("期望 404，却返回了
# 200") contains "404", so a 200 was caught by the catch and reported as 符合预期
# (ADR-090).
function Get-ApiStatus {
    param([string]$Path)
    $headers = @{}
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    try {
        Invoke-WebRequest -Uri "$api$Path" -Method GET -Headers $headers -TimeoutSec 60 -UseBasicParsing -ErrorAction Stop | Out-Null
        return 200
    }
    catch {
        $resp = $_.Exception.Response
        if ($resp -and $resp.StatusCode) { return [int]$resp.StatusCode }
        throw
    }
}

Write-Output "My Quant Lab — NAS 端到端检查"
Write-Output "target: $Base   time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# ---- 1. The web container serves the UI and proxies /healthz ---------------
Step 'Web 容器 /healthz' {
    $r = Invoke-WebRequest -Uri "$Base/healthz" -TimeoutSec 20 -UseBasicParsing
    # nginx proxies this path to the API (docker/web.nginx.conf). A 200 on its own is
    # not evidence of that: an nginx default page, a stale proxy target or a captive
    # portal all answer 200 as well, so the body has to say what it is.
    $body = "$($r.Content)"
    if ($body -notmatch '"status"\s*:\s*"alive"') {
        throw "HTTP $($r.StatusCode) 但响应体不是 API 存活探针：$($body.Substring(0, [Math]::Min(200, $body.Length)))"
    }
    "HTTP $($r.StatusCode), status=alive"
}

Step 'Web 容器首页 (/)' {
    $r = Invoke-WebRequest -Uri "$Base/" -TimeoutSec 20 -UseBasicParsing
    if ($r.Content -notmatch '<div id="app"') { throw '首页不含 <div id="app"> 挂载点，可能是 nginx 默认页或前端构建产物缺失' }
    "HTTP $($r.StatusCode), html=$($r.RawContentLength)B"
}

# ---- 2. API liveness + readiness -----------------------------------------
Step 'API /healthz (存活探针)' {
    $r = Invoke-Api -Path '/healthz'
    # The step was called a liveness probe while accepting any 200, including one
    # with no body: the old code printed a note and passed. It now reads the answer
    # (ADR-072).
    $status = Assert-Value -Label 'status' -Value $r.status
    if ($status -ne 'alive') { throw "status='$status'（期望 alive）" }
    ($r | ConvertTo-Json -Compress -Depth 5)
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
    # This step used to format whatever came back, so a response with no version and
    # no modules printed "version= env= modules=0" and passed. It asserts now, and
    # `-ExpectVersion` turns it into "the release I meant to deploy is the one
    # answering" instead of "some version answered" (ADR-072).
    $version = Assert-Value -Label 'version' -Value $r.version
    $modules = @($r.modules)
    if ($modules.Count -eq 0) { throw 'modules 为空：这个部署没有报告任何模块' }
    if ($ExpectVersion -and $version -ne $ExpectVersion) {
        throw "version=$version，期望 $ExpectVersion（用 -ExpectVersion 指定应该在线上的版本）"
    }
    "version=$version env=$($r.environment) provider=$($r.market_data_provider) modules=$($modules.Count)"
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
        # every repeat check, so assert the response carries the sync report and the
        # series actually holds data instead. (The line that used to stand here
        # compared rows inserted against zero — a count can never be negative, so it
        # could not fail.)
        if ($r.PSObject.Properties.Name -notcontains 'inserted') {
            throw "响应里没有 inserted 字段：$($r | ConvertTo-Json -Compress)"
        }
        if (-not $r.series_id) { throw "no series_id returned: $($r | ConvertTo-Json -Compress)" }
        $bars = Invoke-Api -Path "/market-data/series/$($r.series_id)/bars?limit=5"
        $count = @($bars).Count
        if ($count -eq 0) { throw "series $($r.series_id) has no bars" }
        "inserted=$($r.inserted) total=$($r.closed_bars_in_fetch) quality=$($r.quality_status) readback=$count bars"
    }

    $script:strategyId = $null
    Step '创建策略' {
        $r = Invoke-Api -Method POST -Path '/strategies' -Body @{ name = "NAS 检查 $(Get-Date -Format 'HHmmss')" }
        $script:strategyId = Assert-Value -Label 'id' -Value $r.id -Pattern '^\d+$'
        "id=$($script:strategyId)"
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
        $body = @{ version = '1.0.0'; dsl = $dsl }
        $r = Invoke-Api -Method POST -Path "/strategies/$($script:strategyId)/versions" -Body $body
        $script:versionId = $r.id
        $hash = Assert-Value -Label 'immutable_hash' -Value $r.immutable_hash -Pattern '^[0-9a-f]{64}$'
        # The step was called "校验不可变触发器" while it only created a version and
        # printed its hash — the name promised a check nobody made (ADR-071's lesson,
        # applied here). Two things make a version immutable: the stored hash still
        # matches a recomputation, and the same version number cannot be written
        # twice. Both are asserted now (ADR-072).
        $verify = Invoke-Api -Path "/strategies/versions/$($r.id)/verify"
        if ("$($verify.stored_hash)" -ne $hash -or "$($verify.recomputed_hash)" -ne $hash) {
            throw "哈希对不上：created=$hash stored=$($verify.stored_hash) recomputed=$($verify.recomputed_hash)"
        }
        if (-not $verify.intact) { throw "verify 说这个版本已被改动：$($verify | ConvertTo-Json -Compress)" }
        $refusal = Assert-Rejected -What "重复创建同一个版本号 '1.0.0'" -Action {
            Invoke-Api -Method POST -Path "/strategies/$($script:strategyId)/versions" -Body $body
        }
        "version id=$($r.id) hash=$hash intact=$($verify.intact) 重复创建被拒（$($refusal.Substring(0, [Math]::Min(60, $refusal.Length)))）"
    }

    Step '运行回测（result_hash 可复现）' {
        if (-not $script:versionId) { throw '上一步未拿到 version id' }
        $body = @{
            strategy_version_id = $script:versionId; symbol = $Symbol; timeframe = '1d'
        }
        $first = Invoke-Api -Method POST -Path '/backtests' -Body $body
        if ($first.status -ne 'completed') { throw "status=$($first.status)" }
        $hash = Assert-Value -Label 'result_hash' -Value $first.result_hash -Pattern '^[0-9a-f]{64}$'
        # "Backtests are reproducible" is one of this project's red lines, and a hash
        # that merely exists does not show it: the same strategy version over the same
        # series has to produce the same hash twice, on the deployed engine (ADR-072).
        $second = Invoke-Api -Method POST -Path '/backtests' -Body $body
        if ("$($second.result_hash)" -ne $hash) {
            throw "同一个回测跑两次得到不同的 result_hash：$hash vs $($second.result_hash)"
        }
        "status=$($first.status) trades=$(@($first.trades).Count) hash=$hash (两次一致)"
    }

    Step '扫描信号' {
        $r = Invoke-Api -Method POST -Path '/signals/scan'
        $evaluated = Assert-Value -Label 'evaluated' -Value $r.evaluated
        "evaluated=$evaluated"
    }

    Step 'AI 任务详情端点 (v0.9.9 修复的接口)' {
        # Regression guard for the v0.9.9 breakage. A missing route ALSO answers
        # 404, so assert the path is really registered before probing it.
        #
        # The schema lives at the app root, NOT under the API prefix: the routers
        # are mounted at /api/v1 but openapi_url stays "/openapi.json". Probing
        # $api/openapi.json returns 404, which would make this check pass or fail
        # for entirely the wrong reason (it did exactly that on the first run).
        #
        # /docs and /openapi.json are gated by the same token as /api/v1 (ADR-103),
        # so a deployment that sets API_AUTH_TOKEN must present it here too —
        # otherwise this step "fails" for a reason that has nothing to do with the
        # routes it is checking.
        $schemaUrl = "$Base/openapi.json"
        $schemaHeaders = @{}
        if ($Token) { $schemaHeaders['Authorization'] = "Bearer $Token" }
        try {
            $schema = Invoke-RestMethod -Uri $schemaUrl -Headers $schemaHeaders -TimeoutSec 30 -ErrorAction Stop
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
        # lookup is broken. The status code is read from the response, never from
        # the message text: the failure message below used to be caught by its own
        # `-match '404'` and printed as success (ADR-090).
        $unknown = Get-ApiStatus -Path '/ai/tasks/999999'
        if ($unknown -ne 404) { throw "unknown id 期望 404，实际 $unknown" }
        '两个路由均已注册；unknown id -> 404 (符合预期)'
    }

    Step '模拟盘账户列表' {
        $r = Invoke-Api -Path '/paper/accounts'
        if ($null -eq $r) { throw '账户列表返回了空响应（期望一个列表，空列表也算）' }
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
