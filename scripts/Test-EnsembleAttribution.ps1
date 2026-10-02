# Test-EnsembleAttribution.ps1 — check the ensemble consensus attribution over real HTTP.
#
# The pytest suite drives the app in-process through TestClient, which never exercises
# the wire. This script syncs market data, creates two strategy versions, runs each of
# them once, then calls POST /research/ensemble and asserts the consistency invariants
# the frontend relies on (union >= voted >= 0, per-member agreed/solo <= own signals,
# dataset identity present, engine_version not silently dropped).
#
# Usage:
#   pwsh -NoProfile -File scripts\Test-EnsembleAttribution.ps1
#   pwsh -NoProfile -File scripts\Test-EnsembleAttribution.ps1 -Base http://127.0.0.1:8080/api/v1
param([string]$Base = 'http://127.0.0.1:8080/api/v1')

$ErrorActionPreference = 'Stop'
$Symbol = 'DEMO-AAPL'

function New-Dsl([string]$id, [int]$fast, [int]$slow) {
  @{
    schema_version = '1.0'
    strategy       = @{ id = $id; name = $id; version = '1.0.0' }
    market         = @{ asset_classes = @('stock'); timeframes = @('1d') }
    indicators     = @(
      @{ id = 'ema_fast'; type = 'EMA'; period_ref = 'fast_period' },
      @{ id = 'ema_slow'; type = 'EMA'; period_ref = 'slow_period' }
    )
    parameters     = @{ fast_period = $fast; slow_period = $slow }
    entry          = @{ long = @{ all = @(@{ op = 'crosses_above'; left = 'ema_fast'; right = 'ema_slow' }) } }
    exit           = @{ long = @{ any = @(@{ op = 'crosses_below'; left = 'ema_fast'; right = 'ema_slow' }) } }
    risk           = @{ stop_loss_atr_multiple = 2.0; max_position_pct = 0.5 }
    execution      = @{ fee_bps = 10; slippage_bps = 5; initial_capital = 10000.0 }
  }
}

function New-Version([string]$id, [int]$fast, [int]$slow) {
  $strategy = Invoke-RestMethod "$Base/strategies" -Method Post -ContentType 'application/json' `
    -Body (@{ name = $id } | ConvertTo-Json)
  $body = @{ version = '1.0.0'; dsl = (New-Dsl $id $fast $slow) } | ConvertTo-Json -Depth 12
  $version = Invoke-RestMethod "$Base/strategies/$($strategy.id)/versions" -Method Post `
    -ContentType 'application/json' -Body $body
  return [int]$version.id
}

Invoke-RestMethod "$Base/market-data/sync" -Method Post -ContentType 'application/json' `
  -Body (@{ symbol = $Symbol } | ConvertTo-Json) | Out-Null

$a = New-Version 'ens-a' 5 20
$b = New-Version 'ens-b' 10 30
"member versions: a=$a b=$b"

# A member whose own run is on the SAME symbol/timeframe, so the frontend comparison is
# comparable rather than flagged.
foreach ($v in @($a, $b)) {
  Invoke-RestMethod "$Base/backtests" -Method Post -ContentType 'application/json' `
    -Body (@{ strategy_version_id = $v; symbol = $Symbol; timeframe = '1d' } | ConvertTo-Json) | Out-Null
}

$summary = Invoke-RestMethod "$Base/backtests"
"`n-- backtest summary identity --"
$summary | Select-Object id, strategy_version_id, symbol, timeframe, dataset_version_id, total_return |
  Format-Table -AutoSize | Out-String | Write-Output

$ens = Invoke-RestMethod "$Base/research/ensemble" -Method Post -ContentType 'application/json' `
  -Body (@{
    members         = @(
      @{ strategy_version_id = $a; weight = 1.0 },
      @{ strategy_version_id = $b; weight = 1.0 }
    )
    symbol          = $Symbol
    timeframe       = '1d'
    vote_threshold  = 0.5
  } | ConvertTo-Json -Depth 6)

"`n-- ensemble identity --"
$ens | Select-Object ensemble_version, symbol, timeframe, dataset_version_id, engine_version, feature_version, bars_evaluated |
  Format-List | Out-String | Write-Output

"`n-- agreement --"
$ens.agreement | Format-List | Out-String | Write-Output

"`n-- per-member attribution --"
$ens.members | Format-Table label, weight, entry_bars, entry_agreed, solo_entries, entry_support_rate, vote_agreement_rate -AutoSize |
  Out-String | Write-Output

# Same members, deliberately different dataset should be impossible here (one dataset per
# symbol), but the incomparable case is exercised by the API test suite; here we assert
# the consistency invariants the UI relies on.
$ok = $true
if ($ens.agreement.signalled_bars -lt $ens.agreement.entry_bars) { $ok = $false; "FAIL: union < votes" }
if ($ens.agreement.solo_signalled_bars -gt $ens.agreement.signalled_bars) { $ok = $false; "FAIL: solo > union" }
foreach ($m in $ens.members) {
  if ($m.entry_agreed -gt $m.entry_bars) { $ok = $false; "FAIL: $($m.label) agreed > bars" }
  if ($m.solo_entries -gt $m.entry_bars) { $ok = $false; "FAIL: $($m.label) solo > bars" }
}
if (-not $ens.dataset_version_id) { $ok = $false; "FAIL: no dataset_version_id" }
if ($ens.engine_version -ne 'ensemble-1.0.0') { $ok = $false; "FAIL: engine_version dropped" }
"`nINVARIANTS: $(if ($ok) { 'OK' } else { 'FAILED' })"
