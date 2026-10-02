"""Strategy ensemble: combine several strategies into one portfolio (docs/24, ADR-046).

The question is not "which strategy is best" but "do strategies that *agree* behave
better than any one of them". Members vote per bar and the combined decision drives a
single portfolio.

Two design constraints shaped this module:

* **Votes must actually be executed.** ``run_backtest`` recomputes its own entry/exit
  flags from the spec, so handing it a "combined spec" would silently discard the
  vote and return a member's result. The ensemble therefore executes the combined
  decision series directly, sharing the engine's fill/cost/exit helpers
  (``_resolve_exit`` / ``_trade_record`` / ``_position_quantity``) and
  ``compute_metrics`` so its semantics match a single-strategy run.
* **One portfolio, not a pile of trade lists.** Concatenating members' trades would
  imply holding several positions at once, which this engine (one position, one cash
  balance) cannot represent honestly.

Members must share a dataset and timeframe; the caller resolves the bars once.
Weights are normalised, so ``[2, 1]`` and ``[0.67, 0.33]`` behave identically.
Members are evaluated on their own feature frame — a spec may declare its own
indicators — and the portfolio trades on the timestamps they have in common.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.features.engine import build_features
from app.research.engine import (
    _cost_multipliers,
    _position_quantity,
    _resolve_exit,
    _trade_record,
    direction_sign,
)
from app.research.metrics import compute_metrics
from app.strategies.dsl import StrategySpec, merge_spec_overrides
from app.strategies.executor import run_strategy

__all__ = ["ENSEMBLE_VERSION", "EnsembleMember", "run_ensemble", "run_ensemble_sweep"]

ENSEMBLE_VERSION = "1.2.0"

# A readable report is the point; beyond a dozen members the per-member attribution
# stops being useful. This also bounds the work (one feature build per member).
MAX_MEMBERS = 12

# ``vote_sweep`` re-simulates the portfolio once per threshold. Features and member
# decisions are evaluated once and reused, so a point costs one simulation rather than
# one member-by-member re-evaluation. The bound is a work bound, not a taste: the exact
# grid is the coalition totals, which is 12 points for the widest equal-weight ensemble
# (``MAX_MEMBERS``) but grows with the number of distinct subset sums once weights differ
# (five members weighted 1..5 are 19 boundaries). 64 covers every realistic member set
# while keeping a sweep to at most 64 simulations.
MAX_SWEEP_THRESHOLDS = 64

# A vote total is a sum of normalised weights, so it is a float, and the API publishes
# thresholds and coalition totals rounded to a fixed precision. Comparing a raw float sum
# against a rounded threshold is what makes a report lie: a member carrying exactly 1/12
# clears a displayed threshold of 0.083333, while ``possible_votes`` says the next coalition
# up was needed. Both the single-threshold run and the sweep therefore compare at the
# precision they publish, so the reported ``effective_vote`` and the simulation that
# produced the numbers cannot disagree.
_TOTAL_DECIMALS = 6


@dataclass
class EnsembleMember:
    """One strategy version taking part in the vote."""

    label: str
    spec: StrategySpec
    weight: float = 1.0


@dataclass
class _Evaluated:
    label: str
    weight: float
    frame: pd.DataFrame
    entry_long: pd.Series
    exit_long: pd.Series
    entry_short: pd.Series | None = None
    exit_short: pd.Series | None = None
    risk_stop: pd.Series | None = None
    risk_target: pd.Series | None = None
    risk_stop_short: pd.Series | None = None
    risk_target_short: pd.Series | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class _EnsembleInputs:
    """Everything the vote needs, evaluated once and reusable across thresholds.

    Members' features and rules do not depend on ``vote_threshold`` — only the
    comparison and the simulation do — so the sweep evaluates them once and only
    re-runs the simulation. The weights here are already normalised.
    """

    members: list[EnsembleMember]
    evaluated: list[_Evaluated]
    index: pd.Index
    warnings: list[str]
    execution: Any
    fee_rate: float
    slippage_rate: float
    max_position_pct: float
    capital: float
    stop_long: np.ndarray
    target_long: np.ndarray
    stop_short: np.ndarray
    target_short: np.ndarray
    entry_votes_long: np.ndarray
    entry_votes_short: np.ndarray | None
    exit_votes_short: np.ndarray | None
    any_short: bool


def _prepare_ensemble(
    members: list[EnsembleMember],
    bars: pd.DataFrame,
    *,
    spec_overrides: dict[str, Any] | None = None,
) -> _EnsembleInputs:
    """Evaluate every member once and collect the inputs the vote needs.

    Threshold-independent by construction: ``run_ensemble`` and
    ``run_ensemble_sweep`` share this so a sweep point can never disagree with a
    single-threshold run of the same members.
    """

    if not members:
        raise ValueError("an ensemble needs at least one member")
    if len(members) > MAX_MEMBERS:
        raise ValueError(f"an ensemble supports at most {MAX_MEMBERS} members")

    raw_weights = [float(m.weight) for m in members]
    if any(w < 0 for w in raw_weights):
        raise ValueError("member weights must be >= 0")
    total_weight = sum(raw_weights)
    if total_weight <= 0:
        raise ValueError("member weights must not all be zero")
    weights = [w / total_weight for w in raw_weights]

    warnings: list[str] = []
    evaluated: list[_Evaluated] = []
    for member, weight in zip(members, weights, strict=True):
        feature_frame = build_features(bars, spec=member.spec)
        frame = feature_frame.frame
        out, _ = run_strategy(member.spec, frame)
        evaluated.append(
            _Evaluated(
                label=member.label,
                weight=weight,
                frame=frame,
                entry_long=out["entry_long"].astype(bool),
                exit_long=out["exit_long"].astype(bool),
                entry_short=(out["entry_short"].astype(bool) if "entry_short" in out else None),
                exit_short=(out["exit_short"].astype(bool) if "exit_short" in out else None),
                risk_stop=out.get("risk_stop"),
                risk_target=out.get("risk_target"),
                risk_stop_short=out.get("risk_stop_short"),
                risk_target_short=out.get("risk_target_short"),
            )
        )
        if feature_frame.warmup_bars:
            warnings.append(
                f"member '{member.label}' has a {feature_frame.warmup_bars}-bar warm-up"
            )

    # Every vote is cast on the same bar, so trade on the intersection of timestamps.
    index = evaluated[0].frame.index
    for item in evaluated[1:]:
        index = index.intersection(item.frame.index)
    if len(index) == 0:
        raise ValueError("members share no common bars; check their indicators and warm-up")
    if len(index) < len(evaluated[0].frame.index):
        warnings.append(
            f"ensemble evaluates {len(index)} of {len(evaluated[0].frame.index)} bars "
            "(members' warm-ups overlap only partially)"
        )

    def votes(attr: str) -> pd.Series:
        total = pd.Series(0.0, index=index)
        for item in evaluated:
            series = getattr(item, attr)
            if series is None:
                continue
            total = total + series.reindex(index, fill_value=False).astype(float) * item.weight
        return total

    # The portfolio needs one cost model and one stop rule. Inheriting the first
    # member's (overridable) is documented behaviour; inventing them would be worse.
    # merge_spec_overrides re-validates, so a nested override such as
    # {"execution": {"sizing": {...}}} actually takes effect instead of being kept as
    # an unvalidated dict (which the engine would silently ignore).
    portfolio_spec = merge_spec_overrides(members[0].spec, spec_overrides)

    execution = portfolio_spec.execution
    risk = portfolio_spec.risk
    max_position_pct = risk.max_position_pct if risk and risk.max_position_pct else 1.0
    fee_rate, slippage_rate = _cost_multipliers(execution.fee_bps, execution.slippage_bps)

    # Risk lines come from the first member that defines them: a single portfolio can
    # carry one stop, and mixing members' stops bar-by-bar would be arbitrary.
    stop_long = pd.Series(np.nan, index=index)
    target_long = pd.Series(np.nan, index=index)
    stop_short = pd.Series(np.nan, index=index)
    target_short = pd.Series(np.nan, index=index)
    for item in evaluated:
        for target_series, source in (
            (stop_long, item.risk_stop),
            (target_long, item.risk_target),
            (stop_short, item.risk_stop_short),
            (target_short, item.risk_target_short),
        ):
            if source is None:
                continue
            line = source.reindex(index)
            target_series.loc[:] = target_series.where(target_series.notna(), line)

    any_short = all(item.entry_short is not None for item in evaluated)

    return _EnsembleInputs(
        members=members,
        evaluated=evaluated,
        index=index,
        warnings=warnings,
        execution=execution,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        max_position_pct=max_position_pct,
        capital=float(execution.initial_capital),
        stop_long=stop_long.to_numpy(dtype=float),
        target_long=target_long.to_numpy(dtype=float),
        stop_short=stop_short.to_numpy(dtype=float),
        target_short=target_short.to_numpy(dtype=float),
        entry_votes_long=votes("entry_long").to_numpy(dtype=float),
        entry_votes_short=(votes("entry_short").to_numpy(dtype=float) if any_short else None),
        exit_votes_short=(votes("exit_short").to_numpy(dtype=float) if any_short else None),
        any_short=any_short,
    )


@dataclass
class _SimResult:
    """Outcome of simulating one decision series over one bar index."""

    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    metrics: dict[str, Any]
    final_equity: float
    entries_taken: int
    in_position: list[bool]


def _run_vote(
    inputs: _EnsembleInputs,
    *,
    vote_threshold: float,
    strategy_version: str,
    timeframe: str,
    with_curves: bool,
    with_member_runs: bool,
) -> dict[str, Any]:
    """Merge the members' decisions at one threshold and simulate the portfolio.

    Strictly greater than the threshold, up to ``_VOTE_EPSILON``. With equal weights each
    member carries exactly 0.5, so an inclusive ``>= 0.5`` would let ONE member alone
    clear a "strict majority" and the ensemble would degenerate into a union of members
    (probe: AND=0 bars but the vote fired 11 — the union). Majority means more than.
    """

    evaluated = inputs.evaluated
    index = inputs.index
    entry_votes_long = inputs.entry_votes_long
    agreed_long = _clears(entry_votes_long, vote_threshold)
    exit_votes_long = None
    for item in evaluated:
        line = item.exit_long.reindex(index, fill_value=False).astype(float).to_numpy()
        exit_votes_long = (
            line * item.weight if exit_votes_long is None else exit_votes_long + line * item.weight
        )
    if exit_votes_long is None:  # pragma: no cover - members is never empty here
        exit_votes_long = np.zeros(len(index), dtype=float)
    agreed_exit = _clears(exit_votes_long, vote_threshold)

    entry_short = (
        _clears(inputs.entry_votes_short, vote_threshold)
        if inputs.entry_votes_short is not None
        else None
    )
    exit_short = (
        # Strictly greater here too: a short exit must clear the same bar the long
        # exit does. This was the one `>=` left behind when the entry/exit votes were
        # tightened, and it only fires when every member allows shorts.
        _clears(inputs.exit_votes_short, vote_threshold)
        if inputs.entry_votes_short is not None and inputs.exit_votes_short is not None
        else None
    )

    result = _simulate(
        frame=evaluated[0].frame,
        index=index,
        entry_long=agreed_long,
        exit_long=agreed_exit,
        entry_short=entry_short,
        exit_short=exit_short,
        stop_long=inputs.stop_long,
        target_long=inputs.target_long,
        stop_short=inputs.stop_short,
        target_short=inputs.target_short,
        execution=inputs.execution,
        fee_rate=inputs.fee_rate,
        slippage_rate=inputs.slippage_rate,
        max_position_pct=inputs.max_position_pct,
        capital=inputs.capital,
        timeframe=timeframe,
        strategy_version=strategy_version,
    )

    # Consensus attribution. The ensemble's headline numbers say whether the *combined*
    # decision paid; they do not say who was being voted down. A member's support rate
    # (how often the portfolio ended up doing what it proposed) is the number that makes
    # the comparison table honest: it is computed on the ensemble's own common bars and
    # cost model, not read from some other stored run.
    def vote_masks(item: _Evaluated, attr: str) -> pd.Series:
        series = getattr(item, attr)
        if series is None:
            return pd.Series(False, index=index)
        return series.reindex(index, fill_value=False).astype(bool)

    # Bars where exactly one member asked to enter: the vote was split, so nothing
    # happened even though a member "signalled". Without this number a member whose
    # signals are nearly all solo looks active while contributing nothing.
    solo_entries = (entry_votes_long > 0) & (entry_votes_long < 1.0)

    member_summary: list[dict[str, Any]] = []
    for item in evaluated:
        entry_mask = vote_masks(item, "entry_long")
        exit_mask = vote_masks(item, "exit_long")
        entry_proposed = int(entry_mask.sum())
        exit_proposed = int(exit_mask.sum())
        entry_agreed = int((entry_mask & agreed_long).sum())
        exit_agreed = int((exit_mask & agreed_exit).sum())
        member_summary.append(
            {
                "label": item.label,
                "weight": item.weight,
                "entry_bars": entry_proposed,
                "exit_bars": exit_proposed,
                "entry_votes": entry_proposed,
                "exit_votes": exit_proposed,
                "entry_agreed": entry_agreed,
                "exit_agreed": exit_agreed,
                "solo_entries": int((entry_mask & solo_entries).sum()),
                # How often the vote went the member's way *on bars it signalled*: the
                # ratio of "my proposals that survived" to "my proposals".
                "entry_support_rate": (entry_agreed / entry_proposed) if entry_proposed else None,
                # How often the member's vote agreed with the majority, counting every
                # bar. A member can score 0 here while still clearing the threshold
                # sometimes; the entry rate is the one to read next to `entry_bars`.
                "vote_agreement_rate": (
                    float((entry_mask == agreed_long).mean()) if len(index) else None
                ),
            }
        )

    proposal_union = int((entry_votes_long > 0).sum())
    exit_proposal_union = int((exit_votes_long > 0).sum())
    agreement = {
        "entry_bars": int(agreed_long.sum()),
        "exit_bars": int(agreed_exit.sum()),
        "short_entry_bars": int(entry_short.sum()) if entry_short is not None else 0,
        # Bars where the vote fired AND the portfolio was flat, i.e. positions
        # actually opened. This is the number comparable to a member's entries.
        "entries_taken": result.entries_taken,
        "signalled_bars": proposal_union,
        "solo_signalled_bars": int(solo_entries.sum()),
        # Consensus among the members only: an ensemble can open zero positions because
        # nobody agreed, which looks identical to "no signals" unless these are reported.
        "entry_support_rate": (int(agreed_long.sum()) / proposal_union if proposal_union else None),
        "exit_support_rate": (
            int(agreed_exit.sum()) / exit_proposal_union if exit_proposal_union else None
        ),
    }

    report: dict[str, Any] = {
        "vote_threshold": vote_threshold,
        "members": member_summary,
        "bars_evaluated": len(index),
        "agreement": agreement,
        "metrics": result.metrics.as_dict(),
        "final_equity": result.final_equity,
        "initial_capital": inputs.capital,
        "warnings": list(inputs.warnings),
    }

    if with_curves:
        report["trades"] = result.trades
        report["equity_curve"] = result.equity_curve

    if with_member_runs:
        # Each member, run on the *same* bars with the *same* cost model. The comparison
        # table needs this: a member's most recent stored backtest may have been run on a
        # different window or with different fees, and then the columns would silently be
        # apples to oranges. Each member gets its own cash account funded with its weight's
        # share of the capital, so the solo curves sum to the same starting line — weights
        # are already normalised, so the shares sum to exactly 1.
        member_runs: list[dict[str, Any]] = []
        for item in evaluated:
            solo = _simulate(
                frame=item.frame,
                index=index,
                entry_long=item.entry_long.reindex(index, fill_value=False).astype(bool).to_numpy(),
                exit_long=item.exit_long.reindex(index, fill_value=False).astype(bool).to_numpy(),
                entry_short=(
                    item.entry_short.reindex(index, fill_value=False).astype(bool).to_numpy()
                    if item.entry_short is not None
                    else None
                ),
                exit_short=(
                    item.exit_short.reindex(index, fill_value=False).astype(bool).to_numpy()
                    if item.exit_short is not None
                    else None
                ),
                stop_long=(
                    item.risk_stop.reindex(index).to_numpy(dtype=float)
                    if item.risk_stop is not None
                    else np.full(len(index), np.nan)
                ),
                target_long=(
                    item.risk_target.reindex(index).to_numpy(dtype=float)
                    if item.risk_target is not None
                    else np.full(len(index), np.nan)
                ),
                stop_short=(
                    item.risk_stop_short.reindex(index).to_numpy(dtype=float)
                    if item.risk_stop_short is not None
                    else np.full(len(index), np.nan)
                ),
                target_short=(
                    item.risk_target_short.reindex(index).to_numpy(dtype=float)
                    if item.risk_target_short is not None
                    else np.full(len(index), np.nan)
                ),
                execution=inputs.execution,
                fee_rate=inputs.fee_rate,
                slippage_rate=inputs.slippage_rate,
                max_position_pct=inputs.max_position_pct,
                capital=inputs.capital * item.weight,
                timeframe=timeframe,
                strategy_version=item.label,
            )
            member_runs.append(
                {
                    "label": item.label,
                    "weight": item.weight,
                    "initial_capital": inputs.capital * item.weight,
                    "final_equity": solo.final_equity,
                    "entries_taken": solo.entries_taken,
                    "metrics": solo.metrics.as_dict(),
                    "equity_curve": solo.equity_curve,
                }
            )
        report["member_runs"] = member_runs

    return report


def _simulate(
    *,
    frame: pd.DataFrame,
    index: pd.Index,
    entry_long: np.ndarray,
    exit_long: np.ndarray,
    entry_short: np.ndarray | None,
    exit_short: np.ndarray | None,
    stop_long: np.ndarray,
    target_long: np.ndarray,
    stop_short: np.ndarray,
    target_short: np.ndarray,
    execution: Any,
    fee_rate: float,
    slippage_rate: float,
    max_position_pct: float,
    capital: float,
    timeframe: str,
    strategy_version: str,
) -> _SimResult:
    """Simulate one decision series over ``index`` and return its outcome.

    Both the voted portfolio and each member's own (solo) run go through here, which is
    the point: the comparison table must be produced by the same arithmetic, on the same
    bars, with the same cost model. Anything else makes the columns incomparable.

    ``stop_*``/``target_*`` are per-bar arrays for the entry the decision series asks
    for: the voted portfolio passes the risk lines inherited from its members' specs,
    while a solo run passes that member's own.

    A still-open position is closed at the last bar's close and the last equity point is
    overwritten with the resulting cash, so the curve ends flat rather than marking to
    market a position the run is no longer holding.
    """

    frame = frame.reindex(index)
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    symbol = str(frame["symbol"].iloc[0]) if "symbol" in frame.columns else ""

    if len(frame) == 0:
        return _SimResult(
            equity_curve=[],
            trades=[],
            metrics=compute_metrics(np.array([capital]), [], timeframe=timeframe),
            final_equity=capital,
            entries_taken=0,
            in_position=[],
        )

    cash = capital
    quantity = 0.0
    entry_price = 0.0
    entry_index = -1
    entry_fee = 0.0
    entry_slippage = 0.0
    direction = "LONG"
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    in_position: list[bool] = []
    trade_high = 0.0
    trade_low = 0.0
    entry_stop: float | None = None
    # Count entries the engine actually *took*, not bars where the decision fired: while
    # a position is open a new decision is ignored, so signal bars can exceed positions
    # and would overstate agreement relative to a member's own entry count.
    entries_taken = 0

    for i in range(len(frame)):
        bar_time = index[i]
        close = float(closes[i])

        if quantity > 0:
            trade_high = max(trade_high, float(highs[i]))
            trade_low = min(trade_low, float(lows[i]))
            is_long = direction == "LONG"
            stop = stop_long[i] if is_long else stop_short[i]
            target = target_long[i] if is_long else target_short[i]
            rule_exit = bool(exit_long[i] if is_long else exit_short[i])
            exit_price, reason, ambiguous = _resolve_exit(
                bar_high=float(highs[i]),
                bar_low=float(lows[i]),
                stop=stop,
                target=target,
                close=close,
                rule_exit=rule_exit,
                is_long=is_long,
            )
            if exit_price is not None:
                slip = exit_price * slippage_rate
                fill = exit_price - slip if is_long else exit_price + slip
                fee = abs(fill * quantity) * fee_rate
                cash += direction_sign(direction) * (fill * quantity) - fee
                pnl = (fill - entry_price) * quantity * direction_sign(direction) - fee - entry_fee
                trades.append(
                    _trade_record(
                        direction=direction,
                        symbol=symbol,
                        entry_time=index[entry_index],
                        entry_price=entry_price,
                        exit_time=bar_time,
                        exit_price=fill,
                        quantity=quantity,
                        fees=fee + entry_fee,
                        slippage=abs(slip) + abs(entry_slippage),
                        pnl=pnl,
                        holding_bars=i - entry_index,
                        exit_reason=reason,
                        ambiguous_fill=ambiguous,
                        strategy_version=strategy_version,
                        trade_high=trade_high,
                        trade_low=trade_low,
                        entry_stop=entry_stop,
                    )
                )
                quantity = 0.0
                entry_price = 0.0
                entry_fee = 0.0
                entry_slippage = 0.0
                entry_stop = None
                direction = "LONG"

        # Entry fills at the *next* bar open, matching the single-strategy engine.
        if quantity == 0 and i + 1 < len(frame):
            want_long = bool(entry_long[i])
            want_short = bool(entry_short[i]) if entry_short is not None else False
            if want_long or want_short:
                fill_ref = float(opens[i + 1])
                slip = fill_ref * slippage_rate
                fill = fill_ref + slip if want_long else fill_ref - slip
                raw_stop = stop_long[i] if want_long else stop_short[i]
                stop_distance = (
                    abs(fill - float(raw_stop)) if not np.isnan(float(raw_stop)) else None
                )
                qty = _position_quantity(
                    sizing=execution.sizing,
                    cash=cash,
                    fill=fill,
                    max_position_pct=float(max_position_pct),
                    stop_distance=stop_distance,
                    allow_fractional=execution.allow_fractional,
                )
                if qty > 0:
                    fee = abs(fill * qty) * fee_rate
                    entries_taken += 1
                    if want_long:
                        cash -= fill * qty + fee
                        direction = "LONG"
                    else:
                        cash += fill * qty - fee
                        direction = "SHORT"
                    quantity = qty
                    entry_price = fill
                    entry_fee = fee
                    entry_slippage = abs(slip)
                    entry_index = i + 1
                    trade_high = fill
                    trade_low = fill
                    entry_stop = None if np.isnan(float(raw_stop)) else float(raw_stop)

        position_value = quantity * close * direction_sign(direction)
        in_position.append(quantity > 0)
        equity_curve.append(
            {
                "timestamp": bar_time.isoformat(),
                "equity": cash + position_value,
                "cash": cash,
                "position_value": position_value,
                "close": close,
            }
        )

    if quantity > 0:
        last = len(frame) - 1
        fill = float(closes[last])
        fee = abs(fill * quantity) * fee_rate
        cash += direction_sign(direction) * (fill * quantity) - fee
        pnl = (fill - entry_price) * quantity * direction_sign(direction) - fee - entry_fee
        trades.append(
            _trade_record(
                direction=direction,
                symbol=symbol,
                entry_time=index[entry_index],
                entry_price=entry_price,
                exit_time=index[last],
                exit_price=fill,
                quantity=quantity,
                fees=fee + entry_fee,
                slippage=entry_slippage,
                pnl=pnl,
                holding_bars=last - entry_index,
                exit_reason="end_of_data",
                ambiguous_fill=False,
                strategy_version=strategy_version,
                trade_high=trade_high,
                trade_low=trade_low,
                entry_stop=entry_stop,
            )
        )
        equity_curve[-1]["equity"] = cash
        equity_curve[-1]["cash"] = cash
        equity_curve[-1]["position_value"] = 0.0

    equity_values = np.array([point["equity"] for point in equity_curve], dtype=float)
    metrics = compute_metrics(
        equity_values if len(equity_values) else np.array([capital]),
        trades,
        timeframe=timeframe,
    )
    return _SimResult(
        equity_curve=equity_curve,
        trades=trades,
        metrics=metrics,
        final_equity=float(equity_values[-1]) if len(equity_values) else capital,
        entries_taken=entries_taken,
        in_position=in_position,
    )


def run_ensemble(
    members: list[EnsembleMember],
    bars: pd.DataFrame,
    *,
    vote_threshold: float = 0.5,
    spec_overrides: dict[str, Any] | None = None,
    strategy_version: str = "ensemble",
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Vote members' decisions per bar and backtest the combined portfolio.

    ``spec_overrides`` is merged into the first member's spec (top-level keys, e.g.
    ``{"execution": {...}, "risk": {...}}``) to define how the *portfolio* trades:
    cost model, capital and sizing belong to the portfolio, not to a single member.
    """

    if not 0.0 <= vote_threshold < 1.0:
        raise ValueError("vote_threshold must be in [0, 1): the vote must exceed it")

    inputs = _prepare_ensemble(members, bars, spec_overrides=spec_overrides)
    report = _run_vote(
        inputs,
        vote_threshold=vote_threshold,
        strategy_version=strategy_version,
        timeframe=timeframe,
        with_curves=True,
        with_member_runs=True,
    )
    return {
        "ensemble_version": ENSEMBLE_VERSION,
        "engine_version": f"ensemble-{ENSEMBLE_VERSION}",
        "feature_version": "ensemble",
        **report,
    }


def run_ensemble_sweep(
    members: list[EnsembleMember],
    bars: pd.DataFrame,
    *,
    thresholds: list[float] | None = None,
    spec_overrides: dict[str, Any] | None = None,
    strategy_version: str = "ensemble",
    timeframe: str = "1d",
) -> dict[str, Any]:
    """Vote the same members at several thresholds and report the shape (docs/24 §7).

    ``vote_threshold`` is the only knob an ensemble has, and its effect is
    discontinuous: equal weights make a threshold a coalition requirement (0.5 means
    "both members"), so the surface is a staircase, not a curve. Evaluating one
    threshold at a time leaves the reader guessing which coalitions they skipped.

    Not a recommendation: this reports how the metrics move, exactly like the
    parameter sensitivity sweep (docs/21). Features and member rules are evaluated
    once and only the simulation repeats, so N thresholds cost one member evaluation
    plus N simulations.
    """

    if thresholds is None:
        # Half a vote per member is the natural grid: with equal weights these are the
        # only values that change anything, because the weighted vote is a sum of
        # weights and can only land on coalition totals.
        thresholds = _default_thresholds_for(members)
        thresholds = _within_sweep_budget(thresholds)
    thresholds = [float(t) for t in thresholds]
    if not thresholds:
        raise ValueError("thresholds must not be empty")
    if len(thresholds) > MAX_SWEEP_THRESHOLDS:
        raise ValueError(
            f"a sweep evaluates at most {MAX_SWEEP_THRESHOLDS} thresholds, got {len(thresholds)}"
        )
    for threshold in thresholds:
        if not 0.0 <= threshold < 1.0:
            raise ValueError(f"vote threshold {threshold} must be in [0, 1)")
    if len(set(thresholds)) != len(thresholds):
        raise ValueError("thresholds must not contain duplicates")

    inputs = _prepare_ensemble(members, bars, spec_overrides=spec_overrides)

    points: list[dict[str, Any]] = []
    for threshold in sorted(thresholds):
        report = _run_vote(
            inputs,
            vote_threshold=threshold,
            strategy_version=strategy_version,
            timeframe=timeframe,
            with_curves=False,
            with_member_runs=False,
        )
        agreement = report["agreement"]
        metrics = report["metrics"]
        points.append(
            {
                "vote_threshold": threshold,
                # The vote value the portfolio's own entries actually cleared. This is
                # the coalition size that mattered, not the number the caller typed.
                "effective_vote": _effective_vote(inputs, threshold),
                "entries_taken": agreement["entries_taken"],
                "entry_bars": agreement["entry_bars"],
                "signalled_bars": agreement["signalled_bars"],
                "solo_signalled_bars": agreement["solo_signalled_bars"],
                "final_equity": report["final_equity"],
                "total_return": metrics.get("total_return"),
                "max_drawdown": metrics.get("max_drawdown"),
                "sharpe": metrics.get("sharpe"),
                "win_rate": metrics.get("win_rate"),
                "number_of_trades": metrics.get("number_of_trades"),
            }
        )

    return {
        "ensemble_version": ENSEMBLE_VERSION,
        "engine_version": f"ensemble-{ENSEMBLE_VERSION}",
        "feature_version": "ensemble",
        "vote_threshold": points[0]["vote_threshold"] if len(points) == 1 else None,
        "bars_evaluated": len(inputs.index),
        "initial_capital": inputs.capital,
        "thresholds": [point["vote_threshold"] for point in points],
        "points": points,
        "members": [
            {
                "label": item.label,
                "weight": item.weight,
                # The coalition totals the weighted vote can take. This is what makes a
                # staircase readable: between two of these values nothing can change.
                "weight_share": item.weight,
            }
            for item in inputs.evaluated
        ],
        "possible_votes": _possible_votes(inputs),
        # How many thresholds a sweep will evaluate. Reported so a client can explain the
        # bound instead of hard-coding it, and so a caller who is refused knows the number
        # they have to stay under.
        "max_thresholds": MAX_SWEEP_THRESHOLDS,
        "warnings": list(inputs.warnings),
    }


def _clears(votes: np.ndarray, vote_threshold: float) -> np.ndarray:
    """Bars where a weighted vote beats ``vote_threshold``.

    One place decides what "more than the threshold" means, so the single-threshold
    endpoint, the sweep and the reported ``effective_vote`` cannot drift apart. The
    comparison happens at the precision the response publishes, which is what keeps a
    rounded threshold from being cleared by the very coalition it names.
    """

    limit = round(vote_threshold, _TOTAL_DECIMALS)
    return np.round(votes, _TOTAL_DECIMALS) > limit


def _within_sweep_budget(thresholds: list[float]) -> list[float]:
    """Reject a default grid that is too large to evaluate, and say what to do about it.

    Unequal weights do not change the *shape* of the answer but they do multiply the
    boundaries: the totals the vote can take are the subset sums of the weights, which is
    why an equal-weight ensemble is cheap and a set of irregular weights is not. When the
    exact grid no longer fits the budget, the caller has to choose the thresholds they
    care about -- silently evaluating a subset would put a step in the chart that was
    never measured.
    """

    if len(thresholds) <= MAX_SWEEP_THRESHOLDS:
        return thresholds
    raise ValueError(
        f"this member set has {len(thresholds)} distinct coalition totals, more than the "
        f"{MAX_SWEEP_THRESHOLDS} thresholds a sweep will evaluate; pass an explicit "
        f"`thresholds` list to pick the boundaries you care about"
    )


def _default_thresholds_for(members: list[EnsembleMember]) -> list[float]:
    """Thresholds worth evaluating for this member set, in ``[0, 1)``.

    Every distinct coalition total strictly inside ``(0, 1)`` is a boundary where the
    answer changes; ``0.0`` makes any single member enough and is included so the
    "union" case is visible in the same chart.
    """

    weights = [float(m.weight) for m in members]
    if not weights:
        raise ValueError("an ensemble needs at least one member")
    total = sum(weights)
    if total <= 0:
        raise ValueError("member weights must not all be zero")
    normalised = [w / total for w in weights]

    grid = {0.0}
    for value in _coalition_totals(normalised):
        if 0.0 < value < 1.0:
            grid.add(round(value, _TOTAL_DECIMALS))
    return sorted(grid)


def _coalition_totals(normalised_weights: list[float]) -> list[float]:
    """Distinct totals the normalised weighted vote can actually take, ascending.

    Rounded once at the end rather than after every addition: rounding each partial sum
    compounds the error, and twelve equal members drifted all the way to
    ``0.249999``/``0.999996``, which both mislabelled the staircase and pushed a
    ``0.999996`` "boundary" below 1.0 so the default grid overflowed its own cap.
    """

    totals: set[float] = {0.0}
    for weight in normalised_weights:
        totals |= {existing + weight for existing in totals}
    # Rounding can merge two raw totals that were an ulp apart, so dedupe afterwards too.
    return sorted({round(value, _TOTAL_DECIMALS) for value in totals})


def _possible_votes(inputs: _EnsembleInputs) -> list[float]:
    """Distinct totals the weighted vote can actually take, ascending."""

    return _coalition_totals([item.weight for item in inputs.evaluated])


def _effective_vote(inputs: _EnsembleInputs, threshold: float) -> float:
    """Smallest achievable vote total that strictly exceeds ``threshold``.

    Reported next to the requested threshold so a reader can see which coalition the
    portfolio was actually waiting for. The comparison is the same one ``_clears`` makes,
    so this number always names the coalition that really cleared the bar rather than the
    one a bare ``>`` on rounded floats would suggest.
    """

    limit = round(threshold, _TOTAL_DECIMALS)
    for total in _possible_votes(inputs):
        if total > limit:
            return total
    return 0.0
