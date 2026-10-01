"""AIRouter routing tests (ADR-014): task capability + cost + budget."""

from __future__ import annotations

from app.ai.provider import AIRouter, ModelOption, task_capability


def _router(models, budgets=None, providers=None):
    names = providers or {m.provider for m in models}
    return AIRouter(
        {name: object() for name in names},
        budget_usd=10.0,
        models=models,
        budgets=budgets or {},
    )


def test_pick_prefers_cheapest_cheap_model_for_signal_explanation() -> None:
    models = [
        ModelOption("a", "gpt-4o", "standard", 5.0, 15.0),
        ModelOption("b", "mini", "cheap", 0.15, 0.6),
        ModelOption("b", "large", "high", 8.0, 24.0),
    ]
    provider, model = _router(models).pick("signal_explanation")
    assert provider == "b"
    assert model == "mini"


def test_pick_upgrades_to_high_capability_for_strateg_y_review() -> None:
    models = [
        ModelOption("a", "mini", "cheap", 0.1, 0.4),
        ModelOption("b", "pro", "high", 6.0, 18.0),
    ]
    provider, model = _router(models).pick("strategy_review")
    assert model == "pro"


def test_pick_falls_back_when_budget_spent() -> None:
    models = [
        ModelOption("a", "mini", "cheap", 0.1, 0.4),
        ModelOption("b", "pro", "high", 6.0, 18.0),
    ]
    # 'a' is out of budget -> must fall back to 'b'.
    provider, _model = _router(models, budgets={"a": 0.0, "b": 1.0}).pick("signal_explanation")
    assert provider == "b"


def test_pick_respects_preferred_model() -> None:
    models = [
        ModelOption("a", "mini", "cheap", 0.1, 0.4),
        ModelOption("b", "large", "standard", 3.0, 9.0),
    ]
    provider, model = _router(models).pick("signal_explanation", "large")
    assert (provider, model) == ("b", "large")


def test_pick_without_catalog_keeps_first_provider() -> None:
    router = AIRouter({"a": object()}, budget_usd=5.0)
    assert router.pick("backtest_analysis") == ("a", "default")


def test_task_capability_mapping() -> None:
    assert task_capability("signal_explanation") == "cheap"
    assert task_capability("research_report") == "high"
    assert task_capability("unknown_task") == "standard"
