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

# Strategy slugs are unique, so a fixed name makes the second run of this script fail with
# "strategy slug already exists" instead of verifying anything. Suffix them per run so the
# script stays re-runnable against a long-lived server (the NAS deployment is one).
$run = Get-Date -Format 'MMddHHmmss'
$a = New-Version "ens-a-$run" 5 20
$b = New-Version "ens-b-$run" 10 30
"member versions: a=$a b=$b (run $run)"

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

"`n-- same-bar member runs (ADR-050) --"
$ens.member_runs |
  Select-Object label, weight, initial_capital, final_equity, entries_taken,
    @{ n = 'total_return'; e = { $_.metrics.total_return } },
    @{ n = 'max_drawdown'; e = { $_.metrics.max_drawdown } },
    @{ n = 'curve_len'; e = { $_.equity_curve.Count } } |
  Format-Table -AutoSize | Out-String | Write-Output

$ok = $true
if ($ens.agreement.signalled_bars -lt $ens.agreement.entry_bars) { $ok = $false; "FAIL: union < votes" }
if ($ens.agreement.solo_signalled_bars -gt $ens.agreement.signalled_bars) { $ok = $false; "FAIL: solo > union" }
foreach ($m in $ens.members) {
  if ($m.entry_agreed -gt $m.entry_bars) { $ok = $false; "FAIL: $($m.label) agreed > bars" }
  if ($m.solo_entries -gt $m.entry_bars) { $ok = $false; "FAIL: $($m.label) solo > bars" }
}
if (-not $ens.dataset_version_id) { $ok = $false; "FAIL: no dataset_version_id" }
# Derived, not hardcoded: engine_version is `ensemble-{ENSEMBLE_VERSION}`, and a literal
# here just goes stale every release (ADR-052 decision 6).
if ($ens.engine_version -ne "ensemble-$($ens.ensemble_version)") {
  $ok = $false; "FAIL: engine_version '$($ens.engine_version)' != derived from '$($ens.ensemble_version)'"
}

# The same-bar member runs are what make the comparison honest: one per member, same bar
# count as the vote, funded by weight share, and self-consistent with their own metrics.
if ($ens.member_runs.Count -ne $ens.members.Count) {
  $ok = $false; "FAIL: member_runs count $($ens.member_runs.Count) != members $($ens.members.Count)"
}
$funded = 0.0
foreach ($run in $ens.member_runs) {
  $funded += [double]$run.initial_capital
  if ($run.equity_curve.Count -ne $ens.bars_evaluated) {
    $ok = $false; "FAIL: $($run.label) curve $($run.equity_curve.Count) != bars $($ens.bars_evaluated)"
  }
  if ($run.entries_taken -ne $run.metrics.number_of_trades) {
    $ok = $false; "FAIL: $($run.label) entries_taken != number_of_trades"
  }
  if ([math]::Abs([double]$run.initial_capital - [double]$ens.initial_capital * [double]$run.weight) -gt 0.01) {
    $ok = $false; "FAIL: $($run.label) not funded by weight share"
  }
}
if ([math]::Abs($funded - [double]$ens.initial_capital) -gt 0.01) {
  $ok = $false; "FAIL: member funding $funded != ensemble capital $($ens.initial_capital)"
}

# --- vote-threshold sweep (docs/24 §7, ADR-052) ------------------------------
# The sweep is only useful if a sweep point equals the single-threshold endpoint at the
# same value. Re-run the vote directly at one of the swept thresholds and compare.
$sweep = Invoke-RestMethod "$Base/research/ensemble/sweep" -Method Post -ContentType 'application/json' `
  -Body (@{
    members   = @(
      @{ strategy_version_id = $a; weight = 1.0 },
      @{ strategy_version_id = $b; weight = 1.0 }
    )
    symbol    = $Symbol
    timeframe = '1d'
  } | ConvertTo-Json -Depth 6)

"`n-- threshold sweep --"
$sweep | Select-Object ensemble_version, engine_version, bars_evaluated, initial_capital, possible_votes |
  Format-List | Out-String | Write-Output
$sweep.points |
  Select-Object vote_threshold, effective_vote, entries_taken, entry_bars, signalled_bars,
    total_return, max_drawdown, sharpe, number_of_trades |
  Format-Table -AutoSize | Out-String | Write-Output

if ($sweep.thresholds.Count -ne $sweep.points.Count) {
  $ok = $false; "FAIL: thresholds $($sweep.thresholds.Count) != points $($sweep.points.Count)"
}
if ($sweep.engine_version -ne "ensemble-$($sweep.ensemble_version)") {
  $ok = $false; "FAIL: sweep engine_version not derived from ensemble_version"
}
# Weighted votes are sums of member weights, so every swept threshold and every
# reported effective vote must be an attainable coalition total.
foreach ($t in $sweep.thresholds) {
  if ($sweep.possible_votes -notcontains $t) { $ok = $false; "FAIL: threshold $t is not a coalition total" }
}
foreach ($p in $sweep.points) {
  if ($sweep.possible_votes -notcontains $p.effective_vote) {
    $ok = $false; "FAIL: effective_vote $($p.effective_vote) is not a coalition total"
  }
  if ($p.effective_vote -le $p.vote_threshold) {
    $ok = $false; "FAIL: effective_vote $($p.effective_vote) does not exceed threshold $($p.vote_threshold)"
  }
}
# Monotone: raising the bar can never open more positions.
$counts = @($sweep.points | ForEach-Object { [int]$_.entries_taken })
for ($i = 1; $i -lt $counts.Count; $i++) {
  if ($counts[$i] -gt $counts[$i - 1]) { $ok = $false; "FAIL: entries_taken increased from threshold $($sweep.thresholds[$i-1]) to $($sweep.thresholds[$i])" }
}
# No curves in this contract.
if ($null -ne $sweep.equity_curve -or $null -ne $sweep.member_runs) {
  $ok = $false; "FAIL: sweep returned curves, which are outside its contract"
}

# Point == direct run at the same threshold.
$probe = [double]$sweep.thresholds[-1]
$direct = Invoke-RestMethod "$Base/research/ensemble" -Method Post -ContentType 'application/json' `
  -Body (@{
    members        = @(
      @{ strategy_version_id = $a; weight = 1.0 },
      @{ strategy_version_id = $b; weight = 1.0 }
    )
    symbol         = $Symbol
    timeframe      = '1d'
    vote_threshold = $probe
  } | ConvertTo-Json -Depth 6)
$last = $sweep.points[-1]
"`n-- sweep point @$probe vs direct run --"
@(
  [pscustomobject]@{ field = 'entries_taken'; sweep = $last.entries_taken; direct = $direct.agreement.entries_taken }
  [pscustomobject]@{ field = 'entry_bars'; sweep = $last.entry_bars; direct = $direct.agreement.entry_bars }
  [pscustomobject]@{ field = 'signalled_bars'; sweep = $last.signalled_bars; direct = $direct.agreement.signalled_bars }
  [pscustomobject]@{ field = 'final_equity'; sweep = $last.final_equity; direct = $direct.final_equity }
  [pscustomobject]@{ field = 'total_return'; sweep = $last.total_return; direct = $direct.metrics.total_return }
  [pscustomobject]@{ field = 'number_of_trades'; sweep = $last.number_of_trades; direct = $direct.metrics.number_of_trades }
) | Format-Table -AutoSize | Out-String | Write-Output

if ([int]$last.entries_taken -ne [int]$direct.agreement.entries_taken) { $ok = $false; "FAIL: sweep entries_taken != direct" }
if ([int]$last.entry_bars -ne [int]$direct.agreement.entry_bars) { $ok = $false; "FAIL: sweep entry_bars != direct" }
if ([int]$last.signalled_bars -ne [int]$direct.agreement.signalled_bars) { $ok = $false; "FAIL: sweep signalled_bars != direct" }
if ([int]$last.number_of_trades -ne [int]$direct.metrics.number_of_trades) { $ok = $false; "FAIL: sweep number_of_trades != direct" }
if ([math]::Abs([double]$last.final_equity - [double]$direct.final_equity) -gt 0.01) { $ok = $false; "FAIL: sweep final_equity != direct" }
if ([math]::Abs([double]$last.total_return - [double]$direct.metrics.total_return) -gt 1e-9) { $ok = $false; "FAIL: sweep total_return != direct" }

# Too many thresholds must be rejected rather than silently truncated, and the cap has to
# come from the response rather than a number baked into this script -- a hard-coded cap is
# exactly the duplicated fact that goes stale (it was 12 when the widest ensemble needed
# 13 points for its own default grid).
$cap = [int]$sweep.max_thresholds
if ($cap -le 0) { $ok = $false; "FAIL: sweep did not report max_thresholds" }
$rejected = $false
try {
  Invoke-RestMethod "$Base/research/ensemble/sweep" -Method Post -ContentType 'application/json' `
    -Body (@{
      members    = @(
        @{ strategy_version_id = $a; weight = 1.0 },
        @{ strategy_version_id = $b; weight = 1.0 }
      )
      symbol     = $Symbol
      timeframe  = '1d'
      thresholds = @(0..$cap | ForEach-Object { ($_ + 1) / ($cap + 2) })
    } | ConvertTo-Json -Depth 6) | Out-Null
} catch { $rejected = $true }
if (-not $rejected) { $ok = $false; "FAIL: $($cap + 1) thresholds was accepted (cap is $cap)" }

"`nINVARIANTS: $(if ($ok) { 'OK' } else { 'FAILED' })"
