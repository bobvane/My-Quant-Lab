"""Role contracts: the markdown files are the prompt source (ADR-150).

The runtime must read prompt text from ``app/ai/contracts/*.md``, index it into
``ai_role_contracts`` and be able to trace a completed task back to the exact
file. These tests hold the loader and the database index to that.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import select

from app.ai.role_contracts import (
    CONTRACTS_DIR,
    SYSTEM_CONTRACT_NAME,
    ContractError,
    contract_for_role,
    load_contracts,
    parse_contract,
    role_contracts,
    sync_role_contracts,
    system_contract,
    task_output_schemas,
)
from app.capabilities import MODEL_CAPABILITIES
from app.domain.models import AIPrompt, AIRoleContract

SHIPPED_ROLES = {"RESEARCHER", "STRATEGY_ARCHITECT", "EXPLAINER"}


@pytest.fixture(autouse=True)
def _clear_contract_cache():
    load_contracts.cache_clear()
    yield
    load_contracts.cache_clear()


def test_the_contracts_that_ship_with_the_build_load():
    contracts = load_contracts()
    assert SYSTEM_CONTRACT_NAME in contracts
    assert {contract.role for contract in role_contracts()} >= SHIPPED_ROLES


def test_the_system_contract_declares_no_task_and_every_role_contract_defines_its_prompts():
    for contract in load_contracts().values():
        assert contract.content_hash, contract.name
        assert pathlib.Path(contract.path).is_file(), contract.path
        if contract.is_system:
            assert not contract.task_types
            continue
        assert contract.task_types, contract.name
        for task_type in contract.task_types:
            assert contract.system_prompt_for(task_type).strip()
            assert contract.prompt_name_for(task_type)


def test_the_explainer_keeps_the_prompt_names_that_history_uses():
    contract = contract_for_role("EXPLAINER")
    # Renaming these would orphan every existing ai_prompts row and audit entry.
    assert contract.prompt_name_for("signal_explanation") == "signal_explain"
    assert contract.prompt_name_for("backtest_analysis") == "backtest_explain"


def test_required_capabilities_are_model_capabilities_not_invented_words():
    for contract in load_contracts().values():
        for capability in contract.required_capabilities:
            assert capability in MODEL_CAPABILITIES, f"{contract.name}: {capability}"


def test_the_contracts_state_the_language_of_their_output():
    assert system_contract().output_language == "en"
    for contract in role_contracts():
        assert contract.output_language, contract.name


def test_every_explanation_task_has_a_json_schema():
    schemas = task_output_schemas()
    for task_type in contract_for_role("EXPLAINER").task_types:
        assert schemas.get(task_type), task_type


def test_a_contract_without_front_matter_is_rejected():
    with pytest.raises(ContractError):
        parse_contract("# Just prose\n")


def test_a_contract_missing_a_required_key_is_rejected():
    with pytest.raises(ContractError):
        parse_contract("---\nname: X\n---\n\nbody\n")


def test_a_contract_that_declares_a_task_without_its_prompt_is_rejected():
    text = (
        "---\n"
        "name: X\nrole: X\nversion: 1.0.0\n"
        "task_types: strategy_research\n"
        "---\n\n"
        "Body with no task section.\n"
    )
    with pytest.raises(ContractError, match="no '## Task:' section"):
        parse_contract(text)


def test_a_contract_that_defines_the_wrong_task_section_is_rejected():
    text = (
        "---\n"
        "name: X\nrole: X\nversion: 1.0.0\n"
        "task_types: strategy_research\n"
        "---\n\n"
        "## Task: something_else\n\nDo that instead.\n"
    )
    with pytest.raises(ContractError, match="strategy_research"):
        parse_contract(text)


def test_two_contracts_may_not_claim_the_same_role(tmp_path):
    (tmp_path / "SYSTEM.md").write_text(
        "---\nname: SYSTEM\nrole: SYSTEM\nversion: 1.0.0\ntask_types: *\n---\n\nSystem body.\n",
        encoding="utf-8",
    )
    for filename in ("A.md", "B.md"):
        (tmp_path / filename).write_text(
            "---\nname: DUP\nrole: DUP\nversion: 1.0.0\ntask_types: t\n---\n\n## Task: t\n\nGo.\n",
            encoding="utf-8",
        )
    with pytest.raises(ContractError, match="two contracts claim role"):
        load_contracts(tmp_path)


def test_a_directory_without_the_system_contract_is_rejected(tmp_path):
    with pytest.raises(ContractError, match="SYSTEM.md"):
        load_contracts(tmp_path)


def test_the_content_hash_follows_the_bytes():
    path = CONTRACTS_DIR / "EXPLAINER.md"
    original = path.read_text(encoding="utf-8")
    changed = parse_contract(original + "\nOne more rule.\n", path=str(path))
    assert changed.content_hash != parse_contract(original, path=str(path)).content_hash
    assert changed.name == parse_contract(original, path=str(path)).name


def test_the_role_body_is_kept_out_of_the_task_prompt():
    contract = contract_for_role("EXPLAINER")
    task_text = contract.system_prompt_for("signal_explanation")
    assert contract.body in task_text
    assert contract.task_prompts["signal_explanation"] in task_text
    # A task prompt must not leak the other task's instructions.
    assert contract.task_prompts["backtest_analysis"] not in task_text


def test_an_unknown_role_is_an_error_not_an_empty_contract():
    with pytest.raises(ContractError, match="no contract declares role"):
        contract_for_role("NOPE")


def test_a_missing_task_prompt_is_an_error():
    with pytest.raises(ContractError, match="declares no prompt for task"):
        contract_for_role("EXPLAINER").system_prompt_for("strategy_research")


def test_syncing_the_contracts_is_idempotent(db_session):
    first = sync_role_contracts(db_session)
    db_session.expire_all()
    second = sync_role_contracts(db_session)
    rows = db_session.scalars(select(AIRoleContract)).all()
    assert len(first) == len(second) == len(rows) == len(load_contracts())
    assert {row.name for row in rows} == {c.name for c in load_contracts().values()}


def test_the_indexed_row_carries_the_file_hash_and_its_tasks(db_session):
    sync_role_contracts(db_session)
    row = db_session.scalar(select(AIRoleContract).where(AIRoleContract.name == "EXPLAINER"))
    contract = contract_for_role("EXPLAINER")
    assert row is not None
    assert row.role == "EXPLAINER"
    assert row.version == contract.version
    assert row.content_hash == contract.content_hash
    assert row.task_types_json == list(contract.task_types)
    assert row.output_language == contract.output_language
    assert row.output_schema_json is not None  # signal_explanation has a code schema
    assert row.source_path.endswith("EXPLAINER.md")
    assert row.is_active is True


def test_the_registered_prompts_come_from_the_contract_files(db_session):
    from app.api.routers.ai import _seed_builtin_prompts

    _seed_builtin_prompts(db_session)
    prompts = {(row.name, row.version): row for row in db_session.scalars(select(AIPrompt)).all()}
    explainer = contract_for_role("EXPLAINER")
    for task_type, prompt_name in explainer.prompt_names.items():
        row = prompts.get((prompt_name, explainer.version))
        assert row is not None, f"{prompt_name}@{explainer.version} was not seeded"
        assert row.system_prompt == explainer.system_prompt_for(task_type)
        assert row.task_type == task_type


def test_reading_the_roles_api_returns_the_files_and_their_hashes(client):
    response = client.get("/api/v1/ai/roles")
    assert response.status_code == 200
    payload = response.json()
    roles = {role["role"]: role for role in payload["roles"]}
    assert set(roles) >= SHIPPED_ROLES
    explainer = contract_for_role("EXPLAINER")
    assert roles["EXPLAINER"]["content_hash"] == explainer.content_hash
    assert roles["EXPLAINER"]["ref"] == explainer.ref
    assert payload["system"]["content_hash"] == system_contract().content_hash
    assert payload["system"]["ref"] == system_contract().ref


def test_reading_the_capabilities_api_returns_the_registry(client):
    response = client.get("/api/v1/ai/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["statuses"] == ["SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"]
    assert {group["key"] for group in payload["groups"]}
    assert payload["unsupported"]
