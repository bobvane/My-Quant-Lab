"""Every AI call goes through ``run_task()``; this keeps it that way.

The provider boundary is deliberately small: one module speaks HTTP to a model,
one module decides whether a call may happen at all (cache → budget → audit),
and the roles only ever ask ``run_task``. A new feature that reaches for the
provider on its own would quietly skip the budget, the audit trail and the
cache — the failure is invisible in review and invisible in tests, so the shape
of the boundary is asserted here instead (ADR-162).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

#: Attribute names that mean "ask a model for an answer".
PROVIDER_CALLS = frozenset({"structured_output", "explain_signal", "chat"})

#: The two modules allowed to make such a call, and why the others are not.
PROVIDER_CALLERS = frozenset({"ai/provider.py", "ai/runtime.py"})

#: Anything here that speaks HTTP to a model is a deliberate exception.
HTTP_CALLERS = frozenset({"ai/provider.py", "data/ai_provider_service.py"})

#: Every module of the AI layer, as directories are not walked here.
AI_MODULES = frozenset(
    {
        "ai/__init__.py",
        "ai/budget.py",
        "ai/explain.py",
        "ai/provider.py",
        "ai/research.py",
        "ai/research_schemas.py",
        "ai/role_contracts.py",
        "ai/runtime.py",
    }
)

ROLE_MODULES = ("ai/explain.py", "ai/research.py")


def _modules() -> list[tuple[str, ast.Module, str]]:
    """``(path relative to app/, parsed tree, source)`` for every module."""

    found: list[tuple[str, ast.Module, str]] = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        found.append((path.relative_to(APP).as_posix(), ast.parse(source), source))
    return found


def _attribute_calls(tree: ast.Module) -> set[str]:
    """The methods this module calls on an object, e.g. ``{"run_task"}`` is not one.

    Only ``something.method(...)`` counts: a bare ``explain_signal(...)`` is the
    application service of that name, not a call into the AI router.
    """

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _calls(tree: ast.Module) -> set[str]:
    """The names this module calls, e.g. ``{"run_task", "structured_output"}``."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


def test_only_the_runtime_asks_the_provider_for_an_answer():
    callers = {
        name for name, tree, _source in _modules() if _attribute_calls(tree) & PROVIDER_CALLS
    }

    assert callers == PROVIDER_CALLERS, (
        "a module outside the runtime called the AI provider directly: "
        f"{sorted(callers - PROVIDER_CALLERS)}. Route it through run_task()"
    )


def test_the_ai_layer_is_still_the_same_small_set_of_modules():
    """A new AI module is a design decision, not a detail.

    The classes may be imported for annotations (``explain.py`` names the
    provider in its signatures); what matters is that a module which makes a
    *call* is one of the two above. This test is the tripwire for the case the
    call test cannot see yet: a new module appearing in the layer at all.
    """

    modules = {
        name
        for name, _tree, _source in _modules()
        if name.startswith("ai/") and "/" not in name[len("ai/") :]
    }

    assert modules == AI_MODULES, f"the AI layer changed: {sorted(modules ^ AI_MODULES)}"


def test_only_the_provider_module_speaks_http_inside_the_ai_layer():
    speakers = {
        name for name, _tree, source in _modules() if name.startswith("ai/") and "httpx" in source
    }

    assert speakers == {"ai/provider.py"}


def test_the_settings_connection_test_is_the_only_other_ai_http_caller():
    """Testing a key means listing models; that is the one allowed exception."""

    speakers = {
        name
        for name, _tree, source in _modules()
        if "httpx" in source and (name.startswith("ai/") or "ai_provider" in name)
    }

    assert speakers == HTTP_CALLERS


@pytest.mark.parametrize("name", ROLE_MODULES)
def test_a_role_asks_the_runtime_and_not_the_provider(name: str):
    source = (APP / name).read_text(encoding="utf-8")
    called = _calls(ast.parse(source))

    assert "run_task" in called
    assert not called & PROVIDER_CALLS
    assert "httpx" not in source
