"""Guards for `scripts/Test-NasDeployment.ps1`: a deployment step must be able to fail.

The NAS check is the only thing that looks at a real deployment before the operator
does, so a step that cannot fail is worse than a missing step: it reports health it
never observed. That is exactly what happened — the `/health` step was named
"含依赖与迁移状态" while `/health` carried no migration field at all, and the rest of
the script repeated the pattern in smaller ways (`if ($null -eq $r) { 'empty body' }`
printed a note and passed; `if ($r.inserted -lt 0)` could never be true; three steps
formatted the response and asserted nothing) — ADR-071, ADR-072.

These tests read the script as text. They cannot prove PowerShell runs correctly, so
they pin the *shape*: every step ends up able to throw, and the promises each step's
name makes are actually asserted. The behavioural proof is a run against a live
deployment (`-Base http://127.0.0.1:8080`, or the NAS).
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "Test-NasDeployment.ps1"
TEXT = SCRIPT.read_text(encoding="utf-8")
# The edge behaviour the web liveness step asserts lives in this file, so the guard
# reads both: the script and nginx drifted apart once already (see below).
NGINX = REPO_ROOT / "docker" / "web.nginx.conf"

_STEP = re.compile(r"^\s*Step\s+(?:'([^']*)'|\"([^\"]*)\")\s*\{", re.MULTILINE)


def _steps() -> list[tuple[str, str]]:
    """Return (name, body) for every invoked step, body ending where the next begins."""

    matches = list(_STEP.finditer(TEXT))
    steps: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        name = match.group(1) or match.group(2)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(TEXT)
        steps.append((name, TEXT[match.end() : end]))
    return steps


def _step(name_fragment: str) -> str:
    for name, body in _steps():
        if name_fragment in name:
            return body
    raise AssertionError(f"no step matches {name_fragment!r}; steps are {[n for n, _ in _steps()]}")


def test_the_check_actually_has_steps() -> None:
    names = [name for name, _ in _steps()]
    assert len(names) >= 8, names
    assert len(_step("创建策略")) > 0


def test_every_step_can_fail() -> None:
    """A step with no `throw` and no assertion helper can only ever report OK."""

    silent = [name for name, body in _steps() if "throw" not in body and "Assert-" not in body]
    assert silent == [], f"these steps cannot fail: {silent}"


def test_the_web_liveness_step_reads_the_body() -> None:
    """A 200 from the web edge is not evidence: nginx default pages are 200 too.

    The edge answers this path itself -- `return 200 "ok\\n"` in the
    `location = /healthz` block of `docker/web.nginx.conf` -- and that is the web
    container's own liveness answer, not a proxy to the API (whose probe has its own
    step at `/api/v1/healthz`). The step asked for an API body nginx never served for
    two days, and could not have read one anyway: the edge sends nginx's
    `application/octet-stream` first, so `Invoke-WebRequest` returns a `byte[]` and
    `"$($r.Content)"` compares the decimal byte list `"111 107 10"`. Found by the
    v2.2.0 NAS acceptance (2026-10-05) against a deployment that was correct.
    """

    body = _step("Web 容器 /healthz")
    assert "'ok'" in body, "the step no longer compares the body with what the edge sends"
    assert "byte[]" in body and "GetString" in body, (
        "the step reads a byte[] through string interpolation, so it compares "
        "'111 107 10' instead of the body"
    )
    assert "-notmatch" in body or "-ne" in body
    assert "text/plain" in body, "a 200 carrying the application's HTML would pass"
    assert "throw" in body
    assert "alive" not in body, "the step is back on the API probe this path never served"
    assert "'\"status\"'" not in body

    # Pin the edge behaviour the step asserts, so the script and nginx cannot drift
    # apart again without a test saying which side moved.
    nginx = NGINX.read_text(encoding="utf-8")
    location = nginx[nginx.index("location = /healthz") :]
    location = location[: location.index("}")]
    assert r'return 200 "ok\n";' in location
    assert "text/plain" in location


def test_the_api_liveness_step_reads_the_answer() -> None:
    body = _step("API /healthz")
    assert "'alive'" in body
    assert "Assert-Value" in body
    assert "empty body" not in body, "printing a note instead of failing is what ADR-072 removes"


def test_the_health_step_refuses_a_schema_it_cannot_name() -> None:
    body = _step("依赖 + 库结构版本")
    assert "healthy" in body
    assert "'unknown'" in body and "'none'" in body
    assert "throw" in body


def test_system_info_asserts_its_version_and_modules() -> None:
    body = _step("/system/info")
    assert "Assert-Value -Label 'version'" in body
    assert "modules.Count -eq 0" in body
    assert "throw" in body
    # The version a deployment answers is the point of the step, so it has to be
    # comparable with the release the operator meant to ship.
    assert "$ExpectVersion" in body
    assert "[string]$ExpectVersion = ''" in TEXT
    assert "-ExpectVersion" in TEXT.split("param(", maxsplit=1)[0]


def test_the_version_step_proves_immutability_twice() -> None:
    """The name promised an immutability check; now the step makes one."""

    body = _step("创建策略版本")
    assert "^[0-9a-f]{64}$" in body, "the hash has to be checked, not printed"
    assert "/verify" in body and "intact" in body
    assert "recomputed_hash" in body and "stored_hash" in body
    assert "Assert-Rejected" in body, "immutability is proven by being refused"
    assert "1.0.0" in body


def test_assert_rejected_refuses_to_treat_any_error_as_success() -> None:
    assert "function Assert-Rejected" in TEXT
    assert "期望它被拒绝" in TEXT
    # A 500 is not a validation refusal.
    assert r"\b422\b" in TEXT


def test_the_refusal_helper_does_not_shadow_a_step_payload() -> None:
    """The scriptblock parameter must not be called `Body`.

    A step body reads its own `$body` payload from inside the scriptblock it hands to
    this helper, and PowerShell resolves that bare `$body` through the helper's scope,
    where `$Body` was the scriptblock itself — the first live run of this check posted
    the helper instead of the version and reported "An item with the same key has
    already been added" instead of the 422 refusal the step exists to observe.
    """

    assert "[scriptblock]$Action" in TEXT
    assert "& $Action | Out-Null" in TEXT
    assert "Assert-Rejected -What" in TEXT and "-Action {" in TEXT


def test_the_backtest_step_requires_the_same_hash_twice() -> None:
    """The reproducibility red line is only shown by the same hash from two runs."""

    body = _step("运行回测")
    assert "^[0-9a-f]{64}$" in body
    assert "$second.result_hash" in body and "-ne $hash" in body
    assert "result_hash" in body


def test_no_step_compares_a_count_with_a_tautology() -> None:
    assert "inserted -lt 0" not in TEXT
    assert "-lt 0" not in TEXT
    assert "-ge 0" not in TEXT


def test_the_sync_step_reads_the_report_it_asked_for() -> None:
    body = _step("行情同步")
    assert "inserted" in body and "PSObject.Properties.Name" in body
    assert "series_id" in body
    assert "throw" in body


def test_the_paper_account_step_asserts_it_got_a_list() -> None:
    body = _step("模拟盘账户列表")
    assert "throw" in body


def test_the_summary_reports_the_failures() -> None:
    """A run has to end non-zero when a step failed, or CI/operators see success."""

    assert "exit 1" in TEXT
    assert "exit 0" in TEXT
    assert "$failed.Count -gt 0" in TEXT
