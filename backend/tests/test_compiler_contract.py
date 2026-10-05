"""The Strategy Compiler contract, pinned before the compiler exists (ADR-167).

``docs/29_STRATEGY_COMPILER_CONTRACT.md`` freezes the shape of
``StrategyDraft -> Deterministic Compiler -> StrategySpec 1.0``: what the
compiler may read, which slots are decided by whom, the rejection vocabulary,
the canonical structured-parameter format, the hash identity split, and the
Martin verdict. This module is the executable half of that freeze.

The guards are deliberately of two kinds:

* **Doc/constant binding** — the frozen vocabularies exist as literals here and
  the document must carry the same words. One fact, two files: if they drift,
  the contract describes another compiler (the pattern used by
  ``test_exposure_surface.py`` and ``test_api_spec_truth.py``).
* **Code-state binding** — the premises the contract rests on are read out of
  the running code (``SCHEMA_VERSION``, the DSL field sets, the origin strength
  ladder, the Martin fixture), so a silent change to the DSL or to the research
  layer turns a contract test red instead of quietly invalidating the document.

Nothing here implements the compiler. ``_project``/``_draft_hash`` are the
reference implementation of the frozen §15.2 projection only: they exist so the
"prose never reaches the hash" rule is executed against the real Martin fixture
rather than asserted in prose. Step 2B kept them in place on purpose, as the
hand-written second reading of §15.2: this module computes the §17.2 digest with
the reference projection while ``backend/tests/test_compiler_core.py`` pins the
same literal from ``backend/app/compiler/hashing.py``, so the two independent
readings of the document must agree.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any, get_args

import pytest
import research_payloads
from pydantic import ValidationError

from app.ai import research_schemas
from app.data import strategy_service
from app.strategies import dsl

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "29_STRATEGY_COMPILER_CONTRACT.md"
COMPILER_PACKAGE = REPO_ROOT / "backend" / "app" / "compiler"

#: Frozen by §3: the compiler has its own version, independent of the DSL's.
COMPILER_VERSION = "1.0"

#: Frozen by §7.1. §7.2 rejects ``ENGINE_DERIVED`` on purpose: a value is either
#: in the draft, mechanical and economically neutral, or the user's to give.
DECIDED_BY = ("DRAFT", "DRAFT_PARAMETER", "COMPILER_RULE", "USER_REQUIRED")

#: Frozen by §9.1 — exactly fifteen, and every one of them must appear in the
#: document's table. Adding a sixteenth is a contract change, not an edit.
REJECTION_CODES = (
    "needs_user_decision",
    "unknown_blocks_slot",
    "ambiguous_phrase",
    "capability_missing",
    "not_expressible",
    "indicator_unmapped",
    "parameter_invalid",
    "rule_unmapped",
    "rule_conflict",
    "indicator_collision",
    "missing_required_slot",
    "validation_failed",
    "engine_incompatible",
    "provenance_invalid",
    "version_conflict",
)

#: §8.2: "a human answering one question makes this draft compilable".
USER_DECIDABLE_CODES = (
    "needs_user_decision",
    "unknown_blocks_slot",
    "ambiguous_phrase",
    "missing_required_slot",
)

#: §15.2 — the prose fields excluded from ``draft_hash``. Deleting by key name
#: anywhere in the payload covers every path the document enumerates.
PROSE_FIELDS = frozenset(
    {
        "statement",
        "note",
        "why",
        "reason",
        "phrase",
        "quote",
        "notes",
        "understanding_of_original",
        "limitations",
    }
)

#: §14.1 — the DSL surface that must not move (ADR-155 froze DSL 1.0).
FROZEN_MODEL_FIELDS: dict[str, frozenset[str]] = {
    "StrategySpec": frozenset(
        {
            "schema_version",
            "strategy",
            "market",
            "indicators",
            "features",
            "parameters",
            "entry",
            "exit",
            "risk",
            "execution",
        }
    ),
    "StrategyBlock": frozenset({"id", "name", "version", "description", "source"}),
    "MarketSpec": frozenset({"asset_classes", "timeframes", "allow_short"}),
    "IndicatorSpec": frozenset({"id", "type", "period", "period_ref", "input", "params"}),
    "Condition": frozenset({"op", "left", "right", "threshold"}),
    "ConditionGroup": frozenset({"all", "any"}),
    "SideRules": frozenset({"long", "short"}),
    "RiskSpec": frozenset(
        {
            "stop_loss_atr_multiple",
            "take_profit_r_multiple",
            "take_profit_atr_multiple",
            "max_position_pct",
        }
    ),
    "SizingSpec": frozenset({"mode", "fraction", "risk_pct", "atr_multiple"}),
    "ExecutionSpec": frozenset(
        {
            "fill_model",
            "entry_order_type",
            "limit_offset_atr",
            "stop_offset_atr",
            "order_valid_bars",
            "fee_bps",
            "slippage_bps",
            "allow_fractional",
            "initial_capital",
            "sizing",
        }
    ),
}

FROZEN_ENUMS: dict[str, tuple[str, ...]] = {
    "ComparisonOp": ("gt", "gte", "lt", "lte", "eq", "ne", "crosses_above", "crosses_below"),
    "FillModel": ("next_bar_open", "close_bar"),
    "OrderType": ("market", "limit", "stop"),
    "SizingMode": ("fixed_fraction", "risk_per_trade", "atr_risk"),
}


def contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _project(payload: Any) -> Any:
    """§15.2: drop the prose fields, keep every structured one."""

    if isinstance(payload, dict):
        return {key: _project(value) for key, value in payload.items() if key not in PROSE_FIELDS}
    if isinstance(payload, list):
        return [_project(item) for item in payload]
    return payload


def _draft_hash(payload: Any) -> str:
    canonical = json.dumps(
        _project(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compiler_sources() -> list[pathlib.Path]:
    if not COMPILER_PACKAGE.is_dir():
        return []
    return sorted(COMPILER_PACKAGE.rglob("*.py"))


def _section(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head : text.index(end, head)]


def test_compiler_version_is_independent_of_schema_version() -> None:
    """§3: two version numbers, two questions — never one."""

    assert COMPILER_VERSION == "1.0"
    assert dsl.SCHEMA_VERSION == "1.0"

    text = contract_text()
    section = _section(text, "## 3. 编译器版本与 StrategySpec 版本的关系", "## 4. 编译器输入契约")
    assert "COMPILER_VERSION" in section
    assert "SCHEMA_VERSION" in section
    # The document has to say they move independently, in those words.
    assert "两者**独立**" in section
    # And it has to forbid smuggling the compiler version into the DSL document.
    assert "禁止把 `COMPILER_VERSION` 写进 `dsl_json`" in section


def test_decided_by_enum_is_frozen() -> None:
    """§7: four sources of a value, and no fifth called ``ENGINE_DERIVED``."""

    assert DECIDED_BY == ("DRAFT", "DRAFT_PARAMETER", "COMPILER_RULE", "USER_REQUIRED")
    assert "ENGINE_DERIVED" not in DECIDED_BY

    text = contract_text()
    for value in DECIDED_BY:
        assert f"`{value}`" in text, f"decided_by value {value} is missing from the contract"
    section = _section(text, "### 7.2 为什么拒绝 `ENGINE_DERIVED`", "## 8. 拒绝语义与状态")
    assert "**被显式拒绝**" in section
    assert "不存在第四种来源" in section


def test_rejection_code_vocabulary_is_frozen() -> None:
    """§9.1: exactly fifteen codes, each with a row in the document's table."""

    assert len(REJECTION_CODES) == 15
    assert len(set(REJECTION_CODES)) == 15

    section = _section(contract_text(), "### 9.1 15 个码", "### 9.2 码与结果状态的绑定")
    rows = [line for line in section.splitlines() if re.match(r"^\|\s*\d+\s*\|", line)]
    assert len(rows) == 15, f"the code table has {len(rows)} rows, the vocabulary has 15"
    for index, (code, row) in enumerate(zip(REJECTION_CODES, rows, strict=True), start=1):
        assert f"| {index} | `{code}` |" in row, f"row {index} does not carry {code}"

    # The two classifications have to be stated where the implementation reads them.
    for code in USER_DECIDABLE_CODES:
        assert code in section
    assert set(USER_DECIDABLE_CODES) <= set(REJECTION_CODES)
    assert "`draft_not_found`" in contract_text()
    assert "compile_failed" in contract_text()


def test_needs_user_decision_stays_valid_without_an_emission_point() -> None:
    """§9.1: the fallback code keeps its row, but Step 2B never reaches for it.

    ADR-168's ``unreachable_in_step_2b`` records *input-boundary* unreachability
    (docs/29 §17.2.1): the frozen ``CompilerInput`` carries no such input at all.
    The fallback code is a different case — the rules simply have no place to emit
    it — so merging the two would record a code as impossible when a later step
    only has to start using it.
    """

    assert "needs_user_decision" in REJECTION_CODES
    assert "needs_user_decision" in USER_DECIDABLE_CODES

    section = _section(contract_text(), "### 9.1 15 个码", "### 9.2 码与结果状态的绑定")
    # The definition itself is untouched: §9.1 still carries row 1.
    assert "| 1 | `needs_user_decision` |" in section
    # ...and the document states the zero-emission fact in those words.
    assert "valid global code" in section
    assert "unreachable by direct emission" in section
    assert "没有直接发射点" in section
    # The two kinds of unreachability must stay separate facts.
    assert "输入边界导致的不可达" in section
    assert "不得合并" in section
    assert "不得把本码加进那张表" in section

    # Structural half: the compiler keeps the code in its vocabulary and nowhere else.
    sources = _compiler_sources()
    if sources:
        emitters = [
            path.name
            for path in sources
            if path.name != "errors.py"
            and '"needs_user_decision"' in path.read_text(encoding="utf-8")
        ]
        assert emitters == [], f"needs_user_decision gained an emission point in {emitters}"


def test_validator_accepts_columns_the_engine_never_materialises() -> None:
    """§13.2: the validator's column vocabulary is wider than the engine's columns."""

    section = _section(contract_text(), "### 13.2 ", "### 13.3 编译器与注册表的分工")
    for name in (
        "highest_high_20",
        "lowest_low_20",
        "previous_high",
        "previous_low",
        "rolling_high_prev",
        "rolling_low_prev",
        "rsi",
    ):
        assert name in section, f"§13.2 must name the drifted column {name}"
    assert "engine_incompatible" in section
    # The list must come from the engine's own listing, never from a copy.
    assert "FEATURE_CATALOGUE" in section
    assert "spec = null" in section
    assert "compile_hash = null" in section


def test_strategy_spec_1_0_surface_is_unchanged() -> None:
    """§14: the compiler adapts to the DSL; the DSL does not adapt to it."""

    for name, expected in FROZEN_MODEL_FIELDS.items():
        model = getattr(dsl, name)
        assert set(model.model_fields) == expected, f"{name} moved; DSL 1.0 is frozen (ADR-155)"

    for name, expected in FROZEN_ENUMS.items():
        assert get_args(getattr(dsl, name)) == expected, f"{name} moved; DSL 1.0 is frozen"

    # The document has to carry the same snapshot, or it describes another DSL.
    text = contract_text()
    for name in FROZEN_MODEL_FIELDS:
        assert f"`{name}`" in text


def test_statement_text_is_not_part_of_compiler_semantics() -> None:
    """§15.2: prose may be reported, never hashed and never mapped."""

    text = contract_text()
    section = _section(text, "### 15.2 `draft_hash`（新）", "### 15.3 `compile_hash`（新）")
    for path in (
        "rules[].statement",
        "rules[].note",
        "indicators[].note",
        "unknowns[].why",
        "assumptions[].statement",
        "ambiguities[].phrase",
        "notes[]",
        "understanding_of_original",
        "limitations[]",
        "evidence[].quote",
    ):
        assert path in section, f"{path} must stay out of draft_hash"
    assert "`parameters`" in section

    # Executed against the real fixture: rewriting every statement changes nothing.
    original = research_payloads.draft_payload()
    rewritten = json.loads(json.dumps(original))
    for rule in rewritten["rules"]:
        rule["statement"] = "完全不同的一句话，但结构化字段一模一样。"
    rewritten["understanding_of_original"] = "换掉的散文。"
    rewritten["notes"] = ["换掉的备注。"]
    rewritten["assumptions"][0]["statement"] = "换掉的假设叙述。"
    assert _draft_hash(rewritten) == _draft_hash(original)

    # And the structured fields still decide the identity.
    edited = json.loads(json.dumps(original))
    edited["rules"][2]["parameters"]["threshold"] = 31
    assert _draft_hash(edited) != _draft_hash(original)


def test_provenance_can_only_be_weakened() -> None:
    """§9.1 ``provenance_invalid``: origin may be kept or weakened, never raised."""

    assert research_schemas.ORIGIN_STRENGTH == {
        "EXPLICIT": 3,
        "INFERRED": 2,
        "ASSUMED": 1,
        "UNKNOWN": 0,
    }
    ladder = list(research_schemas.ORIGIN_STRENGTH.values())
    assert ladder == sorted(ladder, reverse=True), "the strength ladder must be strictly ordered"

    source = (REPO_ROOT / "backend" / "app" / "ai" / "research_schemas.py").read_text(
        encoding="utf-8"
    )
    assert "provenance_stronger_than_hypothesis" in source

    text = contract_text()
    assert "`provenance_invalid`" in text
    assert "溯源被升级或缺失" in text


def test_user_required_slots_are_never_filled_by_defaults() -> None:
    """§12: a pydantic default is a record of engine drift, not permission."""

    text = contract_text()
    section = _section(text, "## 12. 哪些字段不允许编译器自动决定", "## 13. 能力判定规则")
    for slot in (
        "`max_position_pct`",
        "`fee_bps` / `slippage_bps`",
        "`allow_short` / `side`",
        "`timeframe`",
        "`exit` 条件",
        "`entry`/`exit` 多条规则的 `all`/`any`",
        "`fill_model`",
        "`spec.features`",
        "`Condition.threshold`",
    ):
        assert slot in section, f"{slot} must be listed as not-auto-decidable"
    assert "**不是**编译器可以使用的取值来源" in section
    # The forbidden default sources have to be named, not implied.
    assert "`dsl.py:200-206`" in section
    assert "`engine.py:203-205`" in section

    # Code premises: these are the defaults the contract refuses to reuse.
    assert dsl.ExecutionSpec().fee_bps == 0.0
    assert dsl.SizingSpec().mode == "fixed_fraction"
    with pytest.raises(ValidationError):
        dsl.RiskSpec(max_position_pct=0.5)  # a stop or a take profit is required


def test_martin_scenario_stays_uncompilable() -> None:
    """§17: the reference case must stay a question for a human, not a product."""

    hypothesis = research_schemas.StrategyHypothesis.model_validate(
        research_payloads.hypothesis_payload()
    )
    draft = research_schemas.parse_draft(research_payloads.draft_payload())

    # Two readings the draft itself says a human has to choose between.
    assert [item.needs_decision for item in hypothesis.ambiguities] == [True, True]
    # Four things the material never said, all of them needed to formalize it.
    assert {item.field for item in hypothesis.unknowns} == {"timeframe", "exit", "risk", "sizing"}
    assert all(item.needed_to_formalize for item in hypothesis.unknowns)

    # The buy signal is carried by a rule whose ``field`` is ``indicator``...
    entry_like = {rule.field for rule in draft.rules}
    assert "exit" not in entry_like
    indicators = [rule for rule in draft.rules if rule.field == "indicator"]
    assert [rule.id for rule in indicators] == ["d-entry"]
    # ...and the only ``entry`` rule is prose with no structured parameters.
    intent = [rule for rule in draft.rules if rule.field == "entry"]
    assert [rule.id for rule in intent] == ["d-intent"]
    assert intent[0].parameters == {}

    # The instrument lives in fields DSL 1.0 does not have.
    assert draft.market.universe == "BTC"
    assert draft.market.markets == ["crypto"]
    assert not hasattr(dsl.MarketSpec(), "universe")
    assert not hasattr(dsl.MarketSpec(), "markets")

    section = _section(contract_text(), "### 17.2 结论（冻结）", "### 17.3 必须同时记录的字段异常")
    for code in (
        "unknown_blocks_slot",
        "missing_required_slot",
        "rule_unmapped",
        "parameter_invalid",
        "not_expressible",
    ):
        assert code in section, f"Martin's verdict must name {code}"
    assert "NEEDS_USER_DECISION" in section
    assert "executable" in contract_text()
    # The draft's buy rule is not even in the canonical condition shape (§10.6).
    assert set(indicators[0].parameters) == {"indicator", "period", "threshold"}

    # A1 / ADR-168: five codes, not six. ``ambiguous_phrase`` stays in the vocabulary and
    # in §9.1, but it is *unreachable* under the frozen CompilerInput.
    verdict = json.loads(re.search(r"```json\n(\{.*?\})\n```", section, re.DOTALL).group(1))
    assert verdict["result"] == "NEEDS_USER_DECISION"
    assert verdict["spec"] is None
    assert verdict["compile_hash"] is None
    assert verdict["strategy_version_id"] is None
    assert verdict["rejections"] == [
        "unknown_blocks_slot",
        "missing_required_slot",
        "rule_unmapped",
        "parameter_invalid",
        "not_expressible",
    ]
    assert "ambiguous_phrase" not in verdict["rejections"]
    assert verdict["unreachable_in_step_2b"] == ["ambiguous_phrase"]
    assert "remains a valid global rejection code" in section
    # The definition itself is untouched: §9.1 still carries row 3, still USER_DECIDABLE.
    vocabulary = _section(contract_text(), "### 9.1 15 个码", "### 9.2 码与结果状态的绑定")
    assert "| 3 | `ambiguous_phrase` |" in vocabulary
    assert "ambiguous_phrase" in USER_DECIDABLE_CODES
    # §20 states the same five codes as §17.2.
    closing = contract_text()[contract_text().index("## 20.") :]
    for code in verdict["rejections"]:
        assert f"`{code}`" in closing, f"§20 must agree with §17.2 about {code}"

    # Contract boundary guard: nothing may make that code reachable again in Step 2B.
    assert "ambiguities" not in research_schemas.StrategyDraft.model_fields
    assert "ambiguities" not in research_payloads.draft_payload()
    for source_path in _compiler_sources():
        source = source_path.read_text(encoding="utf-8")
        for token in ("StrategyHypothesis", "parse_hypothesis", "needs_decision", '"ambiguities"'):
            assert token not in source, (
                f"{source_path.name} reaches outside CompilerInput ({token}); "
                "see docs/29 §17.2.1 and ADR-168"
            )

    # The existing research-layer guard against premature executability is intact.
    guard = (REPO_ROOT / "backend" / "tests" / "test_ai_strategy_draft.py").read_text(
        encoding="utf-8"
    )
    assert "compiled_strategy_version_id" in guard
    assert "executable is False" in guard or "executable=False" in guard or "is False" in guard


def test_compile_hash_is_not_the_immutable_hash() -> None:
    """§15: one hash proves a row was not edited, the other names its maker."""

    spec = research_payloads.draft_payload()
    version = "1.0.0"
    canonical = json.dumps(
        {"version": version, "dsl": spec}, sort_keys=True, separators=(",", ":"), default=str
    )
    assert (
        strategy_service.immutable_hash(spec, version)
        == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )

    # A compile hash answers a different question, so it cannot be the same digest.
    compile_style = json.dumps(
        {
            "compiler_version": COMPILER_VERSION,
            "draft_hash": _draft_hash(spec),
            "strategy": {"id": 7, "version": version},
            "spec": spec,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    assert hashlib.sha256(compile_style.encode("utf-8")).hexdigest() != (
        strategy_service.immutable_hash(spec, version)
    )

    text = contract_text()
    section = _section(text, "## 15. hash 契约", "## 16. Compile Report 结构")
    assert "`immutable_hash`" in section
    assert "`draft_hash`" in section
    assert "`compile_hash`" in section
    assert "不得用 `immutable_hash` 当 `compile_hash`" in section


def test_compiler_never_imports_the_ai_layer() -> None:
    """§13.4 / §19: the compiler is deterministic, so it cannot call a model."""

    forbidden = ("app.ai", "app\\ai", "AITask", "run_task", "record_usage", "budget")
    sources = _compiler_sources()
    if not sources:
        section = _section(
            contract_text(), "### 13.4 编译器不得调用 AI", "## 14. StrategySpec 1.0 不变式"
        )
        assert "不得创建 `AITask`" in section
        assert "不得调用 `record_usage`" in section
        return
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} references {token}: the compiler calls no model"


def test_compiler_never_touches_the_network() -> None:
    """§19.2: the compiler has no I/O — ingestion already happened upstream."""

    forbidden = ("httpcore", "httpx", "requests", "urllib", "socket", "aiohttp", "urlopen")
    sources = _compiler_sources()
    if not sources:
        section = _section(contract_text(), "## 19. 后续实现阶段的禁止项", "## 20. 本阶段结论")
        assert "不联网" in section
        assert "不读 `AITask.output_json`" in section
        return
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} references {token}: the compiler has no I/O"


def test_compiler_never_runs_a_backtest_or_writes_rows() -> None:
    """§5.3 / §19: judging and persisting are different owners."""

    forbidden = ("run_backtest", "BacktestRun", "db.add", "session.commit", "get_session")
    sources = _compiler_sources()
    if not sources:
        section = _section(
            contract_text(), "## 5. 编译器输出契约", "## 6. 草案规则 → StrategySpec 映射契约"
        )
        assert "不允许。这是冻结结论。" in section
        assert "不产生 `Strategy` 行、`StrategyVersion` 行、`BacktestRun` 行" in section
        code_section = _section(contract_text(), "## 19. 后续实现阶段的禁止项", "## 20. 本阶段结论")
        assert "不得跑回测" in code_section
        assert "不得落库" in code_section
        return
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} references {token}: judging is not persisting"


def test_the_http_contract_uses_the_projects_error_envelope_for_conflicts() -> None:
    """§16.7 + ADR-169: a 409 is an input-boundary error with no compile report."""

    section = _section(
        contract_text(), "### 16.7 HTTP 契约（冻结）", "## 17. Martin 场景的契约结论"
    )

    conflicts = [line for line in section.splitlines() if line.startswith("409 ")]
    assert len(conflicts) == 3, conflicts
    for line, code in zip(
        conflicts,
        ("draft_already_compiled", "version_unassignable", "version_conflict"),
        strict=True,
    ):
        assert f'409  {{"error": {{"code": "{code}"' in line, line
        # No fabricated compile report: nothing ran, so there is no `result` or `report`.
        assert '"result"' not in line, line
        assert '"report"' not in line, line
    assert "绝不带 `report`" in section
    assert "`version_conflict` 不可由编译器产生" in section


def test_the_capability_report_shape_is_the_stored_one() -> None:
    """§4.1 / §13.3 + ADR-170: `verdict` is stored; `status`/`UNSUPPORTED` never is."""

    text = contract_text()
    inputs = _section(text, "### 4.1 允许读", "### 4.2 禁止读")
    assert "`CapabilityDecision.as_dict()`" in inputs
    assert "verdict/requested/supported/partial/missing/model_capabilities/reasons/items" in inputs
    assert "`CapabilityReport.as_dict()`" not in inputs

    section = _section(text, "### 13.3 编译器与注册表的分工", "### 13.4 编译器不得调用 AI")
    assert "`CapabilityDecision.as_dict()`" in section
    assert "research_schemas.py:1448-1458" in section
    assert "`verdict`" in section
    assert "NEEDS_CAPABILITY" in section
    assert 'capability_report_json.status == "UNSUPPORTED"' not in section
