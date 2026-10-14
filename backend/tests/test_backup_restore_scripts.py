"""Guards for the backup / restore-drill tools (WP-B).

A backup nobody restored is a hope, and a restore tool that can point at the wrong
database is worse than no tool. These tests pin the two properties that make the
pair usable:

* `backup-postgres.sh` refuses to claim success on an unverified archive: it
  validates the custom-format dump with `pg_restore --list` before it reports OK.
* `restore-postgres-drill.sh` refuses production targets, refuses to run without an
  explicit `--yes`, and cannot be pointed at the deployment's own service names --
  the guards that matter run before it touches Docker at all.

The behavioural cases run the real scripts with `bash`, so they assert the exit
codes an operator would actually get. The static cases read the scripts as text;
they can prove the guards are written down, not that a given runner obeyed them
(the same line ADR-068/ADR-075 draw for shell and workflow guards).
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
BACKUP = SCRIPTS / "backup-postgres.sh"
DRILL = SCRIPTS / "restore-postgres-drill.sh"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _bash() -> str | None:
    """A bash that can run the scripts, or None when the host has none.

    CI runs on ubuntu (bash at /bin/bash). The Windows dev host reaches bash only
    through a Git-for-Windows install, which is not always on PATH; when it is
    missing the behavioural cases are skipped rather than silently passing.
    """

    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Users\bobvane\AppData\Local\hermes\git\bin\bash.exe",
    ):
        if pathlib.Path(candidate).exists():
            return candidate
    return None


def _run(script: pathlib.Path, argv: list[str]) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    assert bash is not None
    return subprocess.run(
        [bash, str(script), *argv],
        capture_output=True,
        text=True,
        # The scripts' help text carries non-ASCII prose; the default codec on a
        # Windows host is not UTF-8, and a decode error there would look like a
        # script failure.
        encoding="utf-8",
        errors="replace",
    )


# --------------------------------------------------------------------------- #
# the scripts are where the guards expect them
# --------------------------------------------------------------------------- #
def test_the_backup_and_restore_scripts_exist() -> None:
    assert BACKUP.exists(), "scripts/backup-postgres.sh is missing"
    assert DRILL.exists(), "scripts/restore-postgres-drill.sh is missing"


# --------------------------------------------------------------------------- #
# backup: never report success on an unverified archive
# --------------------------------------------------------------------------- #
def test_the_backup_validates_the_archive_before_calling_it_a_success() -> None:
    """A dump that was never listed is a file, not a backup."""

    script = _text(BACKUP)
    assert "pg_dump" in script and "--format=custom" in script, (
        "the dump format is not the restore format"
    )
    assert "pg_restore --list" in script, "the archive is never parsed before it is trusted"
    # The success line must come after the integrity check, not before it.
    assert script.index("pg_restore --list") < script.index("backup-postgres: OK")


def test_the_backup_never_puts_the_password_on_a_command_line_or_a_filename() -> None:
    """The password is read inside the container; it is never an argument we pass."""

    script = _text(BACKUP)
    # The dump command must not interpolate a password variable: the password is
    # read by the container's own environment, never handed over as an argument.
    dump_line = [ln for ln in script.splitlines() if "pg_dump -U" in ln]
    assert dump_line, "pg_dump invocation not found"
    for line in dump_line:
        assert "PASSWORD" not in line, "the password is interpolated into the pg_dump command"
        assert "password" not in line, "the password is interpolated into the pg_dump command"
    # The archive name carries the database and a timestamp, never a secret.
    assert "mql-${db_name}-${stamp}-${build}.dump" in script


def test_the_backup_returns_nonzero_when_it_cannot_dump() -> None:
    script = _text(BACKUP)
    assert "exit 1" in script
    assert "pg_dump failed" in script, "a failed dump has no message"
    assert "exit 2" in script, "bad usage does not have its own exit code"


# --------------------------------------------------------------------------- #
# restore drill: the safety guards
# --------------------------------------------------------------------------- #
def test_the_restore_drill_requires_an_explicit_yes() -> None:
    script = _text(DRILL)
    assert "--yes" in script
    assert 'CONFIRM" = "yes"' in script, "the confirmation flag is parsed but not enforced"


def test_the_restore_drill_denies_production_names() -> None:
    script = _text(DRILL)
    for name in ("quantlab-postgres", "quantlab", "postgres", "redis"):
        assert name in script, f"the deny-list does not mention {name!r}"
    assert "production/default name" in script, "the refusal has no message"


def test_the_restore_drill_checks_the_archive_before_restoring() -> None:
    script = _text(DRILL)
    assert "pg_restore --list" in script, "the drill restores an archive it never validated"
    assert script.index("pg_restore --list") < script.index("pg_restore -U"), (
        "the integrity check runs after the restore, which is too late"
    )


def test_the_restore_drill_cleans_up_its_scratch_container() -> None:
    script = _text(DRILL)
    assert "trap cleanup EXIT" in script, "a failed drill would leave the scratch container behind"
    assert "docker rm -f" in script


def test_the_restore_drill_does_not_borrow_the_deployment_credentials() -> None:
    """The scratch password is generated here, so a mistake cannot read production."""

    script = _text(DRILL)
    assert "openssl rand" in script, "the scratch credentials are not generated locally"


# --------------------------------------------------------------------------- #
# behavioural: the exit codes an operator would actually see
# --------------------------------------------------------------------------- #
_DRILL_CASES = [
    ("no arguments", [], 2),
    ("--backup without --scratch-name", ["--backup", "/tmp/x.dump"], 2),
    ("--scratch-name without --backup", ["--scratch-name", "drill-a"], 2),
    (
        "a production name",
        ["--backup", "/tmp/x.dump", "--scratch-name", "quantlab-postgres", "--yes"],
        1,
    ),
    (
        "the bare service name",
        ["--backup", "/tmp/x.dump", "--scratch-name", "quantlab", "--yes"],
        1,
    ),
    (
        "the upstream image name",
        ["--backup", "/tmp/x.dump", "--scratch-name", "postgres", "--yes"],
        1,
    ),
    (
        "a name that is not a safe token",
        ["--backup", "/tmp/x.dump", "--scratch-name", "Drill_A", "--yes"],
        1,
    ),
    ("no --yes", ["--backup", "/tmp/x.dump", "--scratch-name", "drill-a"], 2),
    ("an unknown argument", ["--nope"], 2),
]


@pytest.mark.parametrize(
    ("label", "argv", "expected"), _DRILL_CASES, ids=[c[0] for c in _DRILL_CASES]
)
def test_the_restore_drill_refuses_unsafe_invocations(
    label: str, argv: list[str], expected: int
) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host; the script guards are still asserted as text")
    result = _run(DRILL, argv)
    assert result.returncode == expected, (
        f"{label!r}: expected exit {expected}, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_the_restore_drill_help_is_available_without_docker() -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    result = _run(DRILL, ["--help"])
    assert result.returncode == 0
    assert "RESTORE DRILL" in result.stdout


_BACKUP_CASES = [
    ("--help", ["--help"], 0),
    ("an unknown argument", ["--nope"], 2),
    ("--compose with no value", ["--compose"], 2),
    ("--keep with no value", ["--keep"], 2),
]


@pytest.mark.parametrize(
    ("label", "argv", "expected"), _BACKUP_CASES, ids=[c[0] for c in _BACKUP_CASES]
)
def test_the_backup_script_parses_arguments(label: str, argv: list[str], expected: int) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    result = _run(BACKUP, argv)
    assert result.returncode == expected, (
        f"{label!r}: expected exit {expected}, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


# --------------------------------------------------------------------------- #
# stub-docker integration: the CONTROL FLOW, with no daemon
#
# These run the real scripts against a controlled stand-in for the docker CLI, so
# the branches that matter -- dump failed, archive unreadable, checksum mismatch,
# import failed, fingerprint mismatch, retention -- are exercised end to end
# rather than read as text. This is NOT a PostgreSQL integration test: no server
# ever starts, and a pass here does not mean `pg_dump`/`pg_restore` really work.
# --------------------------------------------------------------------------- #
_STUB = r"""#!/usr/bin/env bash
# A stand-in for the docker CLI: records each call and simulates only what the
# backup/drill scripts depend on.
set -u
[ -n "${STUB_LOG:-}" ] && printf '%s\n' "$*" >> "$STUB_LOG"
cmd="${1:-}"; shift 2>/dev/null || true
case "$cmd" in
  info) exit 0 ;;
  ps)
    [ -n "${STUB_EXISTING_CONTAINER:-}" ] && printf '%s\n' "$STUB_EXISTING_CONTAINER"
    exit 0 ;;
  inspect)
    fmt=""
    while [ "$#" -gt 0 ]; do
      case "$1" in -f) fmt="${2:-}"; shift 2 ;; *) shift ;; esac
    done
    case "$fmt" in
      *State.Running*) printf '%s\n' "${STUB_RUNNING:-true}" ;;
      *Config.Image*) printf '%s\n' "${STUB_IMAGE:-postgres:16-alpine}" ;;
      *) printf '\n' ;;
    esac
    exit 0 ;;
  compose)
    while [ "$#" -gt 0 ]; do
      case "$1" in ps) printf '%s\n' "${STUB_CONTAINER:-stubcontainer}"; exit 0 ;; *) shift ;; esac
    done
    exit 0 ;;
  exec)
    while [ "$#" -gt 0 ]; do
      case "$1" in -T | -i) shift ;; *) break ;; esac
    done
    shift
    prog="${1:-}"; shift 2>/dev/null || true
    case "$prog" in
      pg_dump)
        if [ "${STUB_DUMP_FAIL:-0}" = "1" ]; then echo "pg_dump: error: simulated" >&2; exit 1; fi
        dest=""
        for a in "$@"; do case "$a" in --file=*) dest="${a#--file=}" ;; esac; done
        target="${STUB_FS}${dest}"
        mkdir -p "$(dirname "$target")"
        printf 'PGDMP-stub\n' > "$target"
        exit 0 ;;
      sh)
        shift
        script="${1:-}"
        case "$script" in
          *POSTGRES_USER*) printf '%s' "${STUB_DB_USER:-quantlab}"; exit 0 ;;
          *POSTGRES_DB*) printf '%s' "${STUB_DB_NAME:-quantlab}"; exit 0 ;;
          *"cat "*)
            path="$(printf '%s' "$script" | sed -n "s/.*cat '\([^']*\)'.*/\1/p")"
            [ "${STUB_COPY_FAIL:-0}" = "1" ] && exit 1
            src="${STUB_FS}${path}"
            [ -f "$src" ] || exit 1
            cat "$src"; rm -f "$src"; exit 0 ;;
        esac
        case "$script" in
          *pg_restore*)
            [ "${STUB_RESTORE_FAIL:-0}" = "1" ] && exit 1
            exit 0 ;;
        esac
        exit 0 ;;
      rm) exit 0 ;;
      pg_isready) exit 0 ;;
      psql) printf '%s\n' "${STUB_PSQL_COUNT:-0}"; exit 0 ;;
      pg_restore) cat >/dev/null 2>&1 || true; exit 0 ;;
    esac
    exit 0 ;;
  run)
    entry=""; mount=""; name=""; detach=0; image=""; args=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -v) mount="${2:-}"; shift 2 ;;
        --entrypoint) entry="${2:-}"; shift 2 ;;
        --name) name="${2:-}"; shift 2 ;;
        --network) shift 2 ;;
        -e) shift 2 ;;
        -d) detach=1; shift ;;
        --rm) shift ;;
        *) if [ -z "$image" ]; then image="$1"; else args="$args $1"; fi; shift ;;
      esac
    done
    if [ "$entry" = "pg_restore" ]; then
      case "$args" in
        *--list*)
          hostdir="${mount%%:*}"
          target=""
          for a in $args; do case "$a" in /b/*) target="$a" ;; esac; done
          base="${target#/b/}"
          if [ "${STUB_ARCHIVE_BAD:-0}" != "1" ] && [ -f "$hostdir/$base" ]; then exit 0; fi
          exit 1 ;;
      esac
      exit 1
    fi
    if [ "$detach" = "1" ]; then
      [ "${STUB_RUN_FAIL:-0}" = "1" ] && exit 1
      [ -n "$name" ] && printf '%s\n' "$name"
    fi
    exit 0 ;;
esac
exit 0
"""


def _stub_env(tmp_path: pathlib.Path, **overrides: str) -> dict[str, str]:
    """A PATH with the stub docker first, plus the scenario's switches."""

    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["STUB_LOG"] = str(tmp_path / "docker.log")
    env["STUB_FS"] = str(tmp_path / "containerfs")
    env.update(overrides)
    return env


def _posix(path: pathlib.Path) -> str:
    """A path bash accepts (backslashes would be read as escapes)."""

    return str(path).replace("\\", "/")


def _exec(
    script: pathlib.Path,
    argv: list[str],
    env: dict[str, str],
    cwd: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    assert bash is not None
    return subprocess.run(
        [bash, str(script), *argv],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _calls(tmp_path: pathlib.Path) -> str:
    log = tmp_path / "docker.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


# --- backup ----------------------------------------------------------------- #
def test_backup_success_leaves_an_archive_a_checksum_and_no_partial(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    out = tmp_path / "backups"
    (tmp_path / "version.txt").write_text("v9.9.9", encoding="utf-8")
    env = _stub_env(tmp_path, STUB_DB_NAME="quantlab", STUB_DB_USER="quantlab")
    result = _exec(
        BACKUP,
        ["--out-dir", _posix(out), "--container", "stubcontainer"],
        env,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    dumps = list(out.glob("mql-*.dump"))
    assert len(dumps) == 1, f"expected exactly one archive, got {dumps}"
    assert dumps[0].name.endswith("-9.9.9.dump"), dumps[0].name
    assert (out / (dumps[0].name + ".sha256")).exists(), "no checksum written"
    assert not list(out.glob("*.partial")), "a .partial file was left behind"


def test_backup_discards_the_archive_when_pg_dump_fails(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    out = tmp_path / "backups"
    env = _stub_env(tmp_path, STUB_DUMP_FAIL="1")
    result = _exec(BACKUP, ["--out-dir", _posix(out), "--container", "stubcontainer"], env)
    assert result.returncode == 1, "a failed dump did not fail the script"
    assert not list(out.glob("mql-*.dump")), "a failed dump left an archive behind"
    assert not list(out.glob("*.partial")), "a failed dump left a .partial behind"


def test_backup_discards_the_archive_when_it_cannot_be_parsed(tmp_path: pathlib.Path) -> None:
    """The archive is only accepted after `pg_restore --list` reads it."""

    if _bash() is None:
        pytest.skip("no bash on this host")
    out = tmp_path / "backups"
    env = _stub_env(tmp_path, STUB_ARCHIVE_BAD="1")
    result = _exec(BACKUP, ["--out-dir", _posix(out), "--container", "stubcontainer"], env)
    assert result.returncode == 1, "an unreadable archive was accepted"
    assert not list(out.glob("mql-*.dump")), "an unreadable archive took its final name"


def test_backup_mounts_the_archive_for_the_readability_check(tmp_path: pathlib.Path) -> None:
    """A container asked to list a host path must be given that path as a mount."""

    if _bash() is None:
        pytest.skip("no bash on this host")
    out = tmp_path / "backups"
    env = _stub_env(tmp_path)
    _exec(BACKUP, ["--out-dir", _posix(out), "--container", "stubcontainer"], env)
    calls = _calls(tmp_path)
    listing = [line for line in calls.splitlines() if "--list" in line]
    assert listing, f"no archive check was made at all:\n{calls}"
    assert any("-v " in line and ":/b:ro" in line for line in listing), (
        "the archive check ran without mounting the file, so it could never read it"
    )


def test_backup_refuses_a_database_name_that_could_escape_the_out_dir(
    tmp_path: pathlib.Path,
) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    out = tmp_path / "backups"
    env = _stub_env(tmp_path, STUB_DB_NAME="../../etc/passwd")
    result = _exec(BACKUP, ["--out-dir", _posix(out), "--container", "stubcontainer"], env)
    assert result.returncode == 1, "a path-traversing database name was accepted"
    assert "refusing database name" in result.stderr


def test_backup_prunes_older_archives_keeping_the_newest(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    out = tmp_path / "backups"
    out.mkdir()
    # Three archives with distinct, explicitly-set ages: retention keeps the NEWEST
    # (by modification time), so the fixture must make that order unambiguous.
    base = 1_600_000_000
    older = []
    for index, stamp in enumerate(("20200101000000", "20210101000000", "20220101000000")):
        path = out / f"mql-quantlab-{stamp}-v9.dump"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (base + index, base + index))
        older.append(path.name)
    env = _stub_env(tmp_path)
    result = _exec(
        BACKUP,
        ["--out-dir", _posix(out), "--container", "stubcontainer", "--keep", "2"],
        env,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    remaining = sorted(p.name for p in out.glob("mql-*.dump"))
    assert len(remaining) == 2, f"--keep 2 retained {remaining}"
    assert older[0] not in remaining, "the oldest archive survived the prune"
    assert older[1] not in remaining, "the second-oldest archive survived the prune"
    assert older[2] in remaining, "the newest pre-existing archive was pruned"


def test_backup_leaves_foreign_files_in_the_out_dir_alone(tmp_path: pathlib.Path) -> None:
    """Retention owns only this tool's own naming scheme."""

    if _bash() is None:
        pytest.skip("no bash on this host")
    out = tmp_path / "backups"
    out.mkdir()
    foreign = out / "my-own-notes.dump"
    foreign.write_text("not ours", encoding="utf-8")
    env = _stub_env(tmp_path)
    _exec(BACKUP, ["--out-dir", _posix(out), "--container", "stubcontainer", "--keep", "1"], env)
    assert foreign.exists(), "retention deleted a file that is not one of this tool's backups"


# --- restore drill ---------------------------------------------------------- #
def _drill_args(tmp_path: pathlib.Path, backup: pathlib.Path) -> list[str]:
    return [
        "--backup",
        _posix(backup),
        "--scratch-name",
        "mql-drill-xyz",
        "--yes",
    ]


def _write_backup(tmp_path: pathlib.Path, *, body: str = "PGDMP-stub\n") -> pathlib.Path:
    backup = tmp_path / "mql-quantlab-20200101000000-v9.dump"
    backup.write_text(body, encoding="utf-8")
    return backup


def test_drill_refuses_a_backup_whose_checksum_does_not_match(tmp_path: pathlib.Path) -> None:
    """A truncated dump can still parse; only the checksum catches it."""

    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    (tmp_path / (backup.name + ".sha256")).write_text(
        "0" * 64 + "  " + backup.name + "\n",
        encoding="utf-8",
        newline="\n",
    )
    env = _stub_env(tmp_path)
    result = _exec(DRILL, _drill_args(tmp_path, backup), env)
    assert result.returncode == 1, "a checksum mismatch did not stop the drill"
    assert "checksum mismatch" in result.stderr


def test_drill_accepts_a_backup_whose_checksum_matches(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    (tmp_path / (backup.name + ".sha256")).write_text(
        f"{digest}  {backup.name}\n", encoding="utf-8", newline="\n"
    )
    env = _stub_env(tmp_path)
    result = _exec(DRILL, _drill_args(tmp_path, backup), env)
    assert result.returncode == 0, result.stderr
    assert "checksum OK" in result.stdout


def test_drill_refuses_an_unreadable_archive(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    env = _stub_env(tmp_path, STUB_ARCHIVE_BAD="1")
    result = _exec(DRILL, _drill_args(tmp_path, backup), env)
    assert result.returncode == 1
    assert "readability check" in result.stderr


def test_drill_fails_and_cleans_up_when_the_import_fails(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    env = _stub_env(tmp_path, STUB_RESTORE_FAIL="1")
    result = _exec(DRILL, _drill_args(tmp_path, backup), env)
    assert result.returncode == 1, "a failed import did not fail the drill"
    assert "pg_restore failed" in result.stderr
    assert "rm -f mql-drill-xyz" in _calls(tmp_path), "the scratch container was not removed"


def test_drill_fails_on_a_fingerprint_mismatch(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    env = _stub_env(tmp_path, STUB_PSQL_COUNT="7")
    args = _drill_args(tmp_path, backup) + ["--expect", "strategies=3"]
    result = _exec(DRILL, args, env)
    assert result.returncode == 1, "a wrong row count was accepted"
    assert "fingerprint mismatch" in result.stderr


def test_drill_passes_when_the_fingerprint_matches(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    env = _stub_env(tmp_path, STUB_PSQL_COUNT="3")
    args = _drill_args(tmp_path, backup) + ["--expect", "strategies=3"]
    result = _exec(DRILL, args, env)
    assert result.returncode == 0, result.stderr
    assert "fingerprint strategies expected=3 got=3" in result.stdout


def test_drill_fails_when_the_scratch_container_will_not_start(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    env = _stub_env(tmp_path, STUB_RUN_FAIL="1")
    result = _exec(DRILL, _drill_args(tmp_path, backup), env)
    assert result.returncode == 1
    assert "could not start the scratch postgres" in result.stderr


def test_drill_refuses_a_scratch_name_that_already_exists(tmp_path: pathlib.Path) -> None:
    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    env = _stub_env(tmp_path, STUB_EXISTING_CONTAINER="mql-drill-xyz")
    result = _exec(DRILL, _drill_args(tmp_path, backup), env)
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_drill_runs_the_scratch_container_off_the_network(tmp_path: pathlib.Path) -> None:
    """The throwaway server must not be able to reach the deployment's database."""

    if _bash() is None:
        pytest.skip("no bash on this host")
    backup = _write_backup(tmp_path)
    env = _stub_env(tmp_path)
    _exec(DRILL, _drill_args(tmp_path, backup), env)
    calls = _calls(tmp_path)
    started = [line for line in calls.splitlines() if "--name mql-drill-xyz" in line]
    assert started, f"the scratch container was never started:\n{calls}"
    assert "--network none" in started[0], "the scratch container joined a network"
    assert " -v " not in started[0], "the scratch container mounted a volume"
