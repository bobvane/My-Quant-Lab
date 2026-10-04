"""The capability registry must describe the engine, not a wish list (ADR-151).

Every group in :mod:`app.capabilities` claims a source in code. These tests read
that source and compare: if the DSL gains an operator, or the feature engine
gains an indicator, the registry has to change with it. A registry that drifts
would let an AI role propose a strategy the engine cannot execute — which is the
exact failure this module exists to prevent.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from types import SimpleNamespace
from typing import get_args

import pytest

from app.capabilities import (
    CAPABILITY_STATUSES,
    GROUPS,
    MODEL_CAPABILITIES,
    UNSUPPORTED_CAPABILITIES,
    assess,
    capability_payload,
    supported_tokens,
)
from app.data.providers import PROVIDER_NAMES
from app.features.catalogue import FEATURE_CATALOGUE
from app.features.engine import (
    SUPPORTED_INDICATOR_TYPES,
    _materialize_indicator,
    normalise_indicator_type,
)
from app.research.metrics import BARRS_PER_YEAR, Metrics
from app.strategies.dsl import (
    ComparisonOp,
    ExecutionSpec,
    FillModel,
    MarketSpec,
    OrderType,
    RiskSpec,
    SizingMode,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _group(key: str):
    for group in GROUPS:
        if group.key == key:
            return group
    raise AssertionError(f"no capability group named {key!r}")


def test_every_group_lists_something_and_names_its_source():
    # An empty group would silently answer "this system supports nothing here",
    # which reads exactly like a truthful registry.
    for group in GROUPS:
        assert group.items, f"capability group {group.key} is empty"
        assert group.source.strip(), f"capability group {group.key} names no source"
        assert group.label.strip(), f"capability group {group.key} has no label"


def test_the_registry_agrees_with_the_dsl_and_the_engine():
    assert _group("operators").items == tuple(get_args(ComparisonOp))
    assert _group("indicators").items == SUPPORTED_INDICATOR_TYPES
    assert _group("fill_models").items == tuple(get_args(FillModel))
    assert _group("entry_order_types").items == tuple(get_args(OrderType))
    assert _group("sizing_modes").items == tuple(get_args(SizingMode))
    assert _group("risk_models").items == tuple(RiskSpec.model_fields)
    assert _group("execution_fields").items == tuple(ExecutionSpec.model_fields)
    assert _group("market_fields").items == tuple(MarketSpec.model_fields)
    assert _group("timeframe_annualisation").items == tuple(BARRS_PER_YEAR)
    assert _group("data_providers").items == PROVIDER_NAMES


def test_the_metric_list_is_the_metric_dataclass_minus_the_inputs():
    listed = set(_group("metrics").items)
    computed = {f.name for f in dataclasses.fields(Metrics)}
    assert listed <= computed
    # ``initial_capital`` is an input and ``notes`` is prose: neither is a result.
    assert listed == computed - {"notes", "initial_capital"}


def test_features_come_from_the_feature_catalogue():
    for key, family in (("features", "indicator"), ("price_action_features", "price_action")):
        expected = tuple(spec.name for spec in FEATURE_CATALOGUE if spec.family == family)
        assert expected, f"the feature catalogue lists no {family} features"
        assert _group(key).items == expected


def test_a_listed_analysis_engine_is_a_file_that_exists():
    for item in _group("analysis_engines").items:
        assert (REPO_ROOT / item).is_file(), f"{item} is listed as an engine but is not here"


def test_the_documented_indicator_spellings_are_the_ones_the_engine_accepts():
    for indicator in SUPPORTED_INDICATOR_TYPES:
        assert normalise_indicator_type(indicator) == indicator
        assert normalise_indicator_type(indicator.lower()) == indicator
    # The aliases are accepted spellings, not extra capabilities.
    assert normalise_indicator_type("bb") == "BOLLINGER"
    assert normalise_indicator_type("BOLLINGER_BANDS") == "BOLLINGER"
    assert normalise_indicator_type("vwap") not in SUPPORTED_INDICATOR_TYPES


@pytest.mark.parametrize("indicator_type", SUPPORTED_INDICATOR_TYPES)
def test_every_listed_indicator_type_actually_materialises(indicator_type, sample_bars):
    # The list is only trustworthy if the engine behind it produces the column.
    frame = sample_bars.copy()
    warmup = _materialize_indicator(
        frame,
        SimpleNamespace(
            id="probe", type=indicator_type, input="close", period=20, period_ref=None, params={}
        ),
        {},
    )
    assert warmup >= 1
    assert "probe" in frame.columns


def test_an_indicator_the_registry_does_not_list_is_rejected_by_the_engine(sample_bars):
    frame = sample_bars.copy()
    with pytest.raises(ValueError, match="unsupported indicator type"):
        _materialize_indicator(
            frame,
            SimpleNamespace(
                id="probe", type="VWAP", input="close", period=20, period_ref=None, params={}
            ),
            {},
        )


def test_a_supported_request_is_supported():
    report = assess(["ema", "crosses_above", "next_bar_open", "metrics:cagr"])
    assert report.status == "SUPPORTED"
    assert report.missing == ()
    assert report.partial == ()


def test_an_unknown_capability_is_reported_not_assumed():
    report = assess(["vwap"])
    assert report.status == "UNSUPPORTED"
    assert report.missing == ("vwap",)
    assert "registry" in report.reasons["vwap"]


def test_a_partial_capability_is_not_reported_as_supported():
    report = assess(["ema", "short_selling"])
    assert report.status == "PARTIALLY_SUPPORTED"
    assert report.partial == ("short_selling",)
    assert "short_selling" not in report.supported
    assert report.reasons["short_selling"]


def test_a_request_mixing_gaps_and_supported_pieces_is_partial():
    report = assess(["sma", "portfolio_rules"])
    assert report.status == "PARTIALLY_SUPPORTED"
    assert report.missing == ("portfolio_rules",)


def test_every_known_gap_carries_its_reason_and_is_listed_once():
    tokens = [entry.token for entry in UNSUPPORTED_CAPABILITIES]
    assert len(tokens) == len(set(tokens))
    for entry in UNSUPPORTED_CAPABILITIES:
        assert entry.reason.strip(), entry.token
        assert entry.token == entry.token.lower(), entry.token
        assert entry.label.strip(), entry.token


def test_a_known_gap_is_never_also_advertised_as_supported():
    index = supported_tokens()
    for entry in UNSUPPORTED_CAPABILITIES:
        assert entry.token not in index, entry.token


def test_the_payload_is_ready_for_a_client_or_a_role():
    payload = capability_payload()
    json.dumps(payload)  # nothing unserialisable may hide in the registry
    assert payload["statuses"] == list(CAPABILITY_STATUSES)
    assert payload["model_capabilities"] == list(MODEL_CAPABILITIES)
    assert payload["groups"] and payload["unsupported"]
    assert {group["key"] for group in payload["groups"]} == {group.key for group in GROUPS}


def test_a_role_asking_for_capabilities_gets_a_bounded_vocabulary():
    # MODEL_CAPABILITIES is what a role contract may require; a contract that
    # asked for something else would never be satisfiable.
    assert len(MODEL_CAPABILITIES) == len(set(MODEL_CAPABILITIES))
    assert all(token.islower() for token in MODEL_CAPABILITIES)
