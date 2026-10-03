# Test-EnsembleAttribution.ps1 — check the ensemble consensus attribution over real HTTP.
#
# The pytest suite drives the app in-process through TestClient, which never exercises
# the wire. This script syncs market data, creates two strategy versions, runs each of
# them once, then calls POST /research/ensemble and asserts the consistency invariants
# the frontend relies on (union >= voted >= 0, per-member agreed/solo <= own signals,
# dataset identity present, engine_version not silently dropped).
#
# The verdict is an exit code, not a word in the console (ADR-073): every check records a
# failure and the script exits 1 when any of them failed. A printed "FAILED" that leaves
# exit code 0 gates nothing — which is exactly what this probe used to do.
#
# Usage:
#   pwsh -NoProfile -File scripts\Test-EnsembleAttribution.ps1
#   pwsh -NoProfile -File scripts\Test-EnsembleAttribution.ps1 -Base http://127.0.0.1:8080/api/v1
#
# Exit codes: 0 = every invariant held, 1 = at least one did not (all of them are listed).
param([string]$Base = 'http://127.0.0.1:8080/api/v1')

$ErrorActionPreference = 'Stop'
$Symbol = 'DEMO-AAPL'
$Timeframe = '1d'

$failures = [System.Collections.Generic.List[string]]::new()
function Fail([string]$Message) {
  $failures.Add($Message)
  Write-Output "FAIL: $Message"
}

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

# The sync report is evidence, not noise: it says whether this stack has anything to verify
# at all. `inserted: 0` is legitimate — syncing is idempotent and the bars may already be
# there — but a report with no series means the provider returned nothing, and a probe over
# an empty dataset silently verifies nothing.
$sync = Invoke-RestMethod "$Base/market-data/sync" -Method Post -ContentType 'application/json' `
  -Body (@{ symbol = $Symbol; timeframe = $Timeframe } | ConvertTo-Json)
"market data: symbol=$($sync.symbol) timeframe=$($sync.timeframe) provider=$($sync.provider) inserted=$($sync.inserted) series_id=$($sync.series_id)"
if ($sync.PSObject.Properties.Name -notcontains 'inserted') {
  Fail "the sync report has no 'inserted' field, so a fresh fetch cannot be told from an empty one"
}
if (-not $sync.series_id) {
  $why = if ($sync.message) { $sync.message } else { 'no message' }
  Fail "the sync established no series ($why) -- there are no bars to verify against"
}

# Strategy slugs are unique, so a fixed name makes the second run of this script fail with
# "strategy slug already exists" instead of verifying anything. Suffix them per run so the
# script stays re-runnable against a long-lived server (the NAS deployment is one).
$run = Get-Date -Format 'MMddHHmmss'
$a = New-Version "ens-a-$run" 5 20
$b = New-Version "ens-b-$run" 10 30
"member versions: a=$a b=$b (run $run)"

# A member whose own run is on the SAME symbol/timeframe, so the frontend comparison is
# comparable rather than flagged. Each member run is asserted as evidence in its own right:
# a run that did not complete, or whose result_hash is not a sha256, is not reproducible
# evidence and has no business being the basis of an ensemble comparison.
$memberRuns = @()
foreach ($v in @($a, $b)) {
  $r = Invoke-RestMethod "$Base/backtests" -Method Post -ContentType 'application/json' `
    -Body (@{ strategy_version_id = $v; symbol = $Symbol; timeframe = $Timeframe } | ConvertTo-Json)
  $memberRuns += $r
  if ([int]$r.strategy_version_id -ne $v) {
    Fail "member run for version $v came back as version $($r.strategy_version_id)"
  }
  if ($r.status -ne 'completed') {
    Fail "member run for version $v has status '$($r.status)', not completed"
  }
  if ($r.result_hash -notmatch '^[0-9a-f]{64}$') {
    Fail "member run for version $v has result_hash '$($r.result_hash)', which is not a sha256"
  }
  if ($r.symbol -ne $Symbol -or $r.timeframe -ne $Timeframe) {
    Fail "member run for version $v ran on $($r.symbol)/$($r.timeframe), not $Symbol/$Timeframe"
  }
}
"member runs recorded: $($memberRuns.Count)"

# NB: assign first, wrap second. `@(Invoke-RestMethod ...)` around a JSON array produces a
# one-element array *whose element is the array*, and then every `.dataset_version_id` below
# resolves through member enumeration: the checks all "pass" while the table they print is
# empty. That is how a probe reports OK over rows it never looked at.
$summaryResponse = Invoke-RestMethod "$Base/backtests"
$summary = @()
if ($null -ne $summaryResponse) { $summary = @($summaryResponse) }
"`n-- backtest summary identity --"
$summary | Select-Object id, strategy_version_id, symbol, timeframe, dataset_version_id, total_return |
  Format-Table -AutoSize | Out-String | Write-Output

if ($summary.Count -gt 0 -and $summary[0] -is [array]) {
  Fail "the backtest list came back nested, so the identity rows are not rows at all"
}
# The rows the ensemble will weight have to be findable in the summary, filed under this
# probe's own series, with a dataset identity attached — that is what makes the comparison
# table the frontend renders comparable instead of merely present.
foreach ($memberRun in $memberRuns) {
  $rows = @($summary | Where-Object { $_.id -eq $memberRun.id })
  if ($rows.Count -ne 1) {
    Fail "the backtest summary has $($rows.Count) rows for member run $($memberRun.id)"
    continue
  }
  $row = $rows[0]
  if ([int]$row.strategy_version_id -ne [int]$memberRun.strategy_version_id) {
    Fail "summary row $($row.id) claims version $($row.strategy_version_id), not $($memberRun.strategy_version_id)"
  }
  if (-not $row.dataset_version_id) {
    Fail "member run $($row.id) has no dataset_version_id in the summary"
  }
  if ($row.dataset_hash -notmatch '^[0-9a-f]{64}$') {
    Fail "member run $($row.id) has dataset_hash '$($row.dataset_hash)', which is not a sha256"
  }
  if ($row.symbol -ne $Symbol -or $row.timeframe -ne $Timeframe) {
    Fail "member run $($row.id) is filed under $($row.symbol)/$($row.timeframe), not $Symbol/$Timeframe"
  }
  if ($row.status -ne 'completed') {
    Fail "member run $($row.id) has summary status '$($row.status)'"
  }
  if ($row.dataset_hash -ne $memberRun.dataset_hash) {
    Fail "member run $($row.id) reports dataset_hash in the summary that differs from the run response"
  }
}

$ens = Invoke-RestMethod "$Base/research/ensemble" -Method Post -ContentType 'application/json' `
  -Body (@{
    members         = @(
      @{ strategy_version_id = $a; weight = 1.0 },
      @{ strategy_version_id = $b; weight = 1.0 }
    )
    symbol          = $Symbol
    timeframe       = $Timeframe
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

# An ensemble over zero bars agrees with itself vacuously; every invariant below would hold.
if ([int]$ens.bars_evaluated -le 0) {
  Fail "the ensemble evaluated $($ens.bars_evaluated) bars -- there is nothing to compare"
}

if ($ens.agreement.signalled_bars -lt $ens.agreement.entry_bars) {
  Fail "union < votes: signalled_bars $($ens.agreement.signalled_bars) < entry_bars $($ens.agreement.entry_bars)"
}
if ($ens.agreement.solo_signalled_bars -gt $ens.agreement.signalled_bars) {
  Fail "solo > union: solo_signalled_bars $($ens.agreement.solo_signalled_bars) > signalled_bars $($ens.agreement.signalled_bars)"
}
foreach ($m in $ens.members) {
  if ($m.entry_agreed -gt $m.entry_bars) {
    Fail "$($m.label) agreed > bars: entry_agreed $($m.entry_agreed) > entry_bars $($m.entry_bars)"
  }
  if ($m.solo_entries -gt $m.entry_bars) {
    Fail "$($m.label) solo > bars: solo_entries $($m.solo_entries) > entry_bars $($m.entry_bars)"
  }
}
if (-not $ens.dataset_version_id) {
  Fail "the ensemble reports no dataset_version_id"
}
# Derived, not hardcoded: engine_version is `ensemble-{ENSEMBLE_VERSION}`, and a literal
# here just goes stale every release (ADR-052 decision 6).
if ($ens.engine_version -ne "ensemble-$($ens.ensemble_version)") {
  Fail "engine_version '$($ens.engine_version)' != derived from ensemble_version '$($ens.ensemble_version)'"
}

# The same-bar member runs are what make the comparison honest: one per member, same bar
# count as the vote, funded by weight share, and self-consistent with their own metrics.
if ($ens.member_runs.Count -ne $ens.members.Count) {
  Fail "member_runs count $($ens.member_runs.Count) != members $($ens.members.Count)"
}
$funded = 0.0
foreach ($run in $ens.member_runs) {
  $funded += [double]$run.initial_capital
  if ($run.equity_curve.Count -ne $ens.bars_evaluated) {
    Fail "$($run.label) curve $($run.equity_curve.Count) != bars $($ens.bars_evaluated)"
  }
  if ($run.entries_taken -ne $run.metrics.number_of_trades) {
    Fail "$($run.label) entries_taken != number_of_trades"
  }
  if ([math]::Abs([double]$run.initial_capital - [double]$ens.initial_capital * [double]$run.weight) -gt 0.01) {
    Fail "$($run.label) not funded by weight share"
  }
}
if ([math]::Abs($funded - [double]$ens.initial_capital) -gt 0.01) {
  Fail "member funding $funded != ensemble capital $($ens.initial_capital)"
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
    timeframe = $Timeframe
  } | ConvertTo-Json -Depth 6)

"`n-- threshold sweep --"
$sweep | Select-Object ensemble_version, engine_version, bars_evaluated, initial_capital, possible_votes |
  Format-List | Out-String | Write-Output
$sweep.points |
  Select-Object vote_threshold, effective_vote, entries_taken, entry_bars, signalled_bars,
    total_return, max_drawdown, sharpe, number_of_trades |
  Format-Table -AutoSize | Out-String | Write-Output

if ($sweep.thresholds.Count -ne $sweep.points.Count) {
  Fail "thresholds $($sweep.thresholds.Count) != points $($sweep.points.Count)"
}
if ($sweep.engine_version -ne "ensemble-$($sweep.ensemble_version)") {
  Fail "sweep engine_version '$($sweep.engine_version)' != derived from ensemble_version '$($sweep.ensemble_version)'"
}
# Weighted votes are sums of member weights, so every swept threshold and every
# reported effective vote must be an attainable coalition total.
foreach ($t in $sweep.thresholds) {
  if ($sweep.possible_votes -notcontains $t) { Fail "threshold $t is not a coalition total" }
}
foreach ($p in $sweep.points) {
  if ($sweep.possible_votes -notcontains $p.effective_vote) {
    Fail "effective_vote $($p.effective_vote) is not a coalition total"
  }
  if ($p.effective_vote -le $p.vote_threshold) {
    Fail "effective_vote $($p.effective_vote) does not exceed threshold $($p.vote_threshold)"
  }
}
# Monotone: raising the bar can never open more positions.
$counts = @($sweep.points | ForEach-Object { [int]$_.entries_taken })
for ($i = 1; $i -lt $counts.Count; $i++) {
  if ($counts[$i] -gt $counts[$i - 1]) {
    Fail "entries_taken increased from threshold $($sweep.thresholds[$i-1]) to $($sweep.thresholds[$i])"
  }
}
# No curves in this contract.
if ($null -ne $sweep.equity_curve -or $null -ne $sweep.member_runs) {
  Fail "the sweep returned curves, which are outside its contract"
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
    timeframe      = $Timeframe
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

if ([int]$last.entries_taken -ne [int]$direct.agreement.entries_taken) { Fail "sweep entries_taken != direct" }
if ([int]$last.entry_bars -ne [int]$direct.agreement.entry_bars) { Fail "sweep entry_bars != direct" }
if ([int]$last.signalled_bars -ne [int]$direct.agreement.signalled_bars) { Fail "sweep signalled_bars != direct" }
if ([int]$last.number_of_trades -ne [int]$direct.metrics.number_of_trades) { Fail "sweep number_of_trades != direct" }
if ([math]::Abs([double]$last.final_equity - [double]$direct.final_equity) -gt 0.01) { Fail "sweep final_equity != direct" }
if ([math]::Abs([double]$last.total_return - [double]$direct.metrics.total_return) -gt 1e-9) { Fail "sweep total_return != direct" }

# Too many thresholds must be rejected rather than silently truncated, and the cap has to
# come from the response rather than a number baked into this script -- a hard-coded cap is
# exactly the duplicated fact that goes stale (it was 12 when the widest ensemble needed
# 13 points for its own default grid).
$cap = [int]$sweep.max_thresholds
if ($cap -le 0) { Fail "the sweep did not report max_thresholds" }
$rejected = $false
try {
  Invoke-RestMethod "$Base/research/ensemble/sweep" -Method Post -ContentType 'application/json' `
    -Body (@{
      members    = @(
        @{ strategy_version_id = $a; weight = 1.0 },
        @{ strategy_version_id = $b; weight = 1.0 }
      )
      symbol     = $Symbol
      timeframe  = $Timeframe
      thresholds = @(0..$cap | ForEach-Object { ($_ + 1) / ($cap + 2) })
    } | ConvertTo-Json -Depth 6) | Out-Null
} catch { $rejected = $true }
if (-not $rejected) { Fail "$($cap + 1) thresholds was accepted (cap is $cap)" }

if ($failures.Count -gt 0) {
  "`nINVARIANTS: FAILED ($($failures.Count))"
  foreach ($failure in $failures) { "  - $failure" }
  exit 1
}
"`nINVARIANTS: OK"
exit 0
