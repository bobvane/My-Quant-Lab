#!/usr/bin/env bash
# My Quant Lab — RESTORE DRILL into a THROWAWAY scratch PostgreSQL (WP-B).
#
# SAFETY-CRITICAL. This script proves a backup can actually be restored, and does
# nothing else. It NEVER touches a production database: it starts an isolated
# scratch container, restores the given dump into it, optionally checks a
# caller-supplied fingerprint, and destroys the container again.
#
# The guards run in this order on purpose, cheapest-and-safest first, and every one
# of them is pure string/path work until guard 5 -- so an invocation that names a
# production database, forgets `--yes`, or is simply malformed is refused before
# the script touches Docker at all:
#
#   1. argument presence and shape (a missing --backup/--scratch-name is usage;
#      --expect must be `table=count` with a safe identifier and a whole number);
#   2. the scratch target must be provably NOT production: a strict
#      ^[a-z][a-z0-9-]*$ token that is none of the project's service names;
#   3. `--yes` must be given explicitly;
#   4. Docker must be usable, and no container may already own the scratch name;
#   5. the backup must exist, be readable and non-empty; if a sibling `.sha256`
#      exists it must MATCH; and the archive must parse (`pg_restore --list`).
#
# The scratch container is started with `--network none` and mounts nothing, so it
# cannot reach the deployment's database or reuse any of its volumes. Its
# credentials are generated here and never come from the deployment's environment.
#
# usage: scripts/restore-postgres-drill.sh \
#            --backup /path/to/mql-*.dump [--scratch-name NAME] \
#            [--expect 'table=count'] [--yes]
#
# Exit codes: 0 = restored, and the fingerprint matched when one was given;
# 1 = a guard or the restore failed; 2 = bad usage.
#
# What a success proves: the dump was readable, checksum-consistent and importable,
# and (with --expect) the restored copy reports the count you named. It is NOT a
# production restore and says nothing about the data's business correctness.
set -uo pipefail

BACKUP=""
SCRATCH_NAME=""
EXPECT=""
CONFIRM=""
PG_IMAGE="postgres:16-alpine"

usage() {
    sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'
}

usage_error() {
    echo "restore-postgres-drill: $1" >&2
    echo >&2
    usage >&2
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --backup)
            [ "$#" -ge 2 ] || usage_error "--backup needs a path"
            BACKUP="$2"; shift 2 ;;
        --scratch-name)
            [ "$#" -ge 2 ] || usage_error "--scratch-name needs a name"
            SCRATCH_NAME="$2"; shift 2 ;;
        --expect)
            [ "$#" -ge 2 ] || usage_error "--expect needs table=count"
            EXPECT="$2"; shift 2 ;;
        --yes)
            CONFIRM="yes"; shift ;;
        -h | --help)
            usage; exit 0 ;;
        *)
            usage_error "unknown argument: $1" ;;
    esac
done

# --- guard 1: required arguments and their shape -------------------------------
[ -n "$BACKUP" ] || usage_error "--backup is required"
[ -n "$SCRATCH_NAME" ] || usage_error "--scratch-name is required"

expect_key=""
expect_count=""
if [ -n "$EXPECT" ]; then
    case "$EXPECT" in
        *=*) ;;
        *) usage_error "--expect must look like table=count" ;;
    esac
    expect_key="${EXPECT%%=*}"
    expect_count="${EXPECT#*=}"
    # The key is interpolated into `SELECT count(*) FROM <key>` inside the scratch
    # server, so it is restricted to a plain (optionally schema-qualified) name.
    case "$expect_key" in
        '' | .* | *..* | *[!A-Za-z0-9_.]*)
            usage_error "--expect table must match [A-Za-z_][A-Za-z0-9_.]*" ;;
    esac
    case "$expect_count" in
        '' | *[!0-9]*)
            usage_error "--expect count must be a whole number" ;;
    esac
fi

# --- guard 2: the target must be provably isolated (no Docker yet) -------------
[[ "$SCRATCH_NAME" =~ ^[a-z][a-z0-9-]*$ ]] || {
    echo "restore-postgres-drill: --scratch-name must match ^[a-z][a-z0-9-]*\$ (got '$SCRATCH_NAME')" >&2
    exit 1
}
# Names that could mean production, or a checkout of this project, are refused
# outright. This is a deny-list of the names this project actually uses; the drill
# is meant to run against a name the operator invented for the occasion.
for bad in quantlab quantlab-postgres quantlab-postgres-1 quantlab_scratch postgres redis mql-wp-c1 mql-phase2-backup-release; do
    if [ "$SCRATCH_NAME" = "$bad" ]; then
        echo "restore-postgres-drill: '$SCRATCH_NAME' is a production/default name; refusing to use it as a scratch target" >&2
        exit 1
    fi
done

# --- guard 3: explicit confirmation -------------------------------------------
[ "$CONFIRM" = "yes" ] || {
    echo "restore-postgres-drill: refusing to restore without --yes (this starts a throwaway container and restores into it)" >&2
    exit 2
}

# --- guard 4: Docker must be usable -------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "restore-postgres-drill: docker CLI not found" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "restore-postgres-drill: docker daemon not reachable" >&2; exit 1; }

# A scratch name that already exists would make the target ambiguous.
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$SCRATCH_NAME"; then
    echo "restore-postgres-drill: container '$SCRATCH_NAME' already exists; refusing to touch it" >&2
    exit 1
fi

# --- guard 5: the backup must be real, checksum-consistent, and parse ----------
[ -f "$BACKUP" ] || { echo "restore-postgres-drill: no such backup file: $BACKUP" >&2; exit 1; }
[ -s "$BACKUP" ] || { echo "restore-postgres-drill: backup file is empty: $BACKUP" >&2; exit 1; }

backup_dir="$(cd "$(dirname "$BACKUP")" && pwd)"
backup_name="$(basename "$BACKUP")"
sum_file="${BACKUP}.sha256"
if [ -f "$sum_file" ]; then
    # A truncated or edited dump can still parse; the checksum is what catches it.
    if ! ( cd "$backup_dir" && sha256sum -c "$(basename "$sum_file")" ) >/dev/null 2>&1; then
        echo "restore-postgres-drill: checksum mismatch for '$backup_name' (sha256sum -c); refusing to restore it" >&2
        exit 1
    fi
    echo "restore-postgres-drill: checksum OK ($(basename "$sum_file"))"
else
    echo "restore-postgres-drill: WARNING no ${backup_name}.sha256 alongside the backup; skipping the checksum check" >&2
fi

# The file is on the HOST, so any check inside a container has to MOUNT it.
archive_ok=0
if command -v pg_restore >/dev/null 2>&1; then
    pg_restore --list "$BACKUP" >/dev/null 2>&1 && archive_ok=1
fi
if [ "$archive_ok" -ne 1 ]; then
    if docker run --rm -v "${backup_dir}:/b:ro" --entrypoint pg_restore "$PG_IMAGE" \
        --list "/b/${backup_name}" >/dev/null 2>&1; then
        archive_ok=1
    fi
fi
if [ "$archive_ok" -ne 1 ]; then
    echo "restore-postgres-drill: backup failed the readability check (pg_restore --list); refusing to restore it" >&2
    exit 1
fi

# --- the drill -----------------------------------------------------------------
scratch_pw="$(openssl rand -hex 16)"
scratch_db="mql_scratch"
scratch_user="mql_scratch"
started=0

cleanup() {
    [ "$started" -eq 1 ] || return 0
    if ! docker rm -f "$SCRATCH_NAME" >/dev/null 2>&1; then
        echo "restore-postgres-drill: WARNING could not remove the scratch container '$SCRATCH_NAME'; remove it by hand: docker rm -f $SCRATCH_NAME" >&2
    fi
}
trap cleanup EXIT

echo "restore-postgres-drill: starting throwaway postgres '$SCRATCH_NAME' (network none, no mounts)"
# `--network none` and no `-v`: the throwaway server cannot reach the deployment's
# database and cannot mount any of its volumes -- only this script's own inputs
# ever enter it.
if ! docker run -d --rm \
        --name "$SCRATCH_NAME" \
        --network none \
        -e POSTGRES_USER="$scratch_user" \
        -e POSTGRES_PASSWORD="$scratch_pw" \
        -e POSTGRES_DB="$scratch_db" \
        "$PG_IMAGE" >/dev/null 2>&1; then
    echo "restore-postgres-drill: could not start the scratch postgres" >&2
    exit 1
fi
started=1

ready=0
for _ in $(seq 1 30); do
    if docker exec "$SCRATCH_NAME" pg_isready -U "$scratch_user" -d "$scratch_db" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
[ "$ready" -eq 1 ] || { echo "restore-postgres-drill: scratch postgres did not become ready" >&2; exit 1; }

echo "restore-postgres-drill: restoring into scratch database '$scratch_db'"
if ! docker exec -i "$SCRATCH_NAME" \
        sh -c "pg_restore -U '$scratch_user' -d '$scratch_db' --no-owner --no-privileges" \
        < "$BACKUP" >/dev/null 2>&1; then
    echo "restore-postgres-drill: pg_restore failed" >&2
    exit 1
fi

if [ -n "$expect_key" ]; then
    got="$(docker exec "$SCRATCH_NAME" psql -U "$scratch_user" -d "$scratch_db" -tAc "SELECT count(*) FROM $expect_key" 2>/dev/null | tr -d '[:space:]')"
    echo "restore-postgres-drill: fingerprint $expect_key expected=$expect_count got=${got:-<error>}"
    if [ "$got" != "$expect_count" ]; then
        echo "restore-postgres-drill: fingerprint mismatch; drill FAILED" >&2
        exit 1
    fi
fi

echo "restore-postgres-drill: OK — restored $backup_name into throwaway '$SCRATCH_NAME' (now being removed)"
exit 0