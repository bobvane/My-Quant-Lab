#!/usr/bin/env bash
# My Quant Lab — consistent PostgreSQL backup (WP-B).
#
# Runs `pg_dump` INSIDE the quantlab-postgres container, so it needs no PostgreSQL
# client on the host and reaches the same data directory the app writes to. The
# custom format (`-Fc`) keeps all the metadata that `pg_restore` needs for a
# selective or reordered restore, and it is what the restore-drill consumes.
#
# A backup is not a copy of a Docker volume: the volume also carries the WAL and
# the exact on-disk format, both tied to the PostgreSQL major version. A volume
# tarball can only be restored onto the same major; a `pg_dump -Fc` stream can be
# restored onto any newer major and is the portable, auditable artifact (WP-B).
#
# Credentials are read from the RUNNING container's own environment, the same way
# the server itself sees them, and are NEVER echoed: nothing here puts a password
# on a command line, in a filename, or in a log line. The dump is written to a
# `.partial` name and only moved into place once it has been copied out and parsed
# by `pg_restore --list`, so an interrupted run cannot leave a truncated file that
# looks like a real backup.
#
# usage: scripts/backup-postgres.sh [options]
#
#   --compose PATH    compose file(s) to use, repeatable (default docker-compose.yml)
#   --env-file PATH   env file to hand to compose (default: none; use .env)
#   --service NAME    the postgres service (default quantlab-postgres)
#   --out-dir DIR     where the backup file and checksum go (default ${MQL_BACKUP_DIR:-./backups})
#   --keep N          how many backups to retain (default 14)
#   --container C     override the container name (default: compose-managed)
#   -h, --help        show this help
#
# Exit codes: 0 = backup written and verified; 1 = a check or dump failed;
# 2 = bad usage.
#
# The file is named `mql-<db>-<YYYYmmddHHMMSS>-<build>.dump` where <build> is the
# app version if it is known, else `unknown`, so an operator can tell which
# release a backup was taken under without opening it.
#
# What this proves and what it does not: a reported success means the archive was
# copied out and `pg_restore --list` could parse it. It is NOT a restore, and it
# does not prove the data inside is what the application expects. Only
# `scripts/restore-postgres-drill.sh` (or a real restore) does that.
set -uo pipefail

# `docker compose` takes files as `-f FILE`, never as a positional: a bare name
# would be read as the COMMAND and the lookup would fail on every invocation.
COMPOSE_ARGS=(-f docker-compose.yml)
COMPOSE_DISPLAY=("docker-compose.yml")
ENV_FILE_ARGS=()
SERVICE="quantlab-postgres"
OUT_DIR="${MQL_BACKUP_DIR:-./backups}"
KEEP=14
CONTAINER=""

usage() {
    sed -n '2,48p' "$0" | sed 's/^# \{0,1\}//'
}

usage_error() {
    echo "backup-postgres: $1" >&2
    echo >&2
    usage >&2
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --compose)
            [ "$#" -ge 2 ] || usage_error "--compose needs a path"
            COMPOSE_ARGS+=(-f "$2")
            COMPOSE_DISPLAY+=("$2")
            shift 2
            ;;
        --env-file)
            [ "$#" -ge 2 ] || usage_error "--env-file needs a path"
            ENV_FILE_ARGS=(--env-file "$2")
            shift 2
            ;;
        --service)
            [ "$#" -ge 2 ] || usage_error "--service needs a name"
            SERVICE="$2"
            shift 2
            ;;
        --out-dir)
            [ "$#" -ge 2 ] || usage_error "--out-dir needs a path"
            OUT_DIR="$2"
            shift 2
            ;;
        --keep)
            [ "$#" -ge 2 ] || usage_error "--keep needs a number"
            KEEP="$2"
            shift 2
            ;;
        --container)
            [ "$#" -ge 2 ] || usage_error "--container needs a name"
            CONTAINER="$2"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage_error "unknown argument: $1"
            ;;
    esac
done

case "$KEEP" in
    '' | *[!0-9]*) usage_error "--keep must be a whole number" ;;
esac
[ "$KEEP" -ge 1 ] || usage_error "--keep must be at least 1"

command -v docker >/dev/null 2>&1 || { echo "backup-postgres: docker CLI not found" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "backup-postgres: docker daemon not reachable" >&2; exit 1; }

mkdir -p "$OUT_DIR" || { echo "backup-postgres: cannot create $OUT_DIR" >&2; exit 1; }

# Resolve the postgres service to a running container. Prefer the explicit
# --container, else let compose name it from the project.
if [ -n "$CONTAINER" ]; then
    db_container="$CONTAINER"
else
    db_container="$(docker compose "${COMPOSE_ARGS[@]}" ${ENV_FILE_ARGS[@]+"${ENV_FILE_ARGS[@]}"} ps -q "$SERVICE" 2>/dev/null | head -n1)"
fi
if [ -z "$db_container" ]; then
    echo "backup-postgres: no running container for service '$SERVICE' (compose file(s): ${COMPOSE_DISPLAY[*]})" >&2
    echo "       start the stack first: docker compose up -d" >&2
    exit 1
fi
docker inspect -f '{{.State.Running}}' "$db_container" 2>/dev/null | grep -q '^true$' \
    || { echo "backup-postgres: container $db_container is not running" >&2; exit 1; }

# The database and user are read from the RUNNING container's own environment --
# not from `docker compose run` (which would start a second container and would
# report the compose config rather than what the server actually started with).
db_user="$(docker exec "$db_container" sh -c 'printf "%s" "${POSTGRES_USER:-}"' 2>/dev/null || true)"
[ -z "$db_user" ] && db_user="quantlab"
db_name="$(docker exec "$db_container" sh -c 'printf "%s" "${POSTGRES_DB:-}"' 2>/dev/null || true)"
[ -z "$db_name" ] && db_name="quantlab"

# The database name becomes part of a filename, so it must not be able to carry a
# path separator or a `..`: a name like `../../x` would otherwise write the dump
# (and the retention pass) outside --out-dir.
case "$db_name" in
    *[!A-Za-z0-9_]* | '')
        echo "backup-postgres: refusing database name '$db_name' (expected [A-Za-z0-9_]+)" >&2
        exit 1
        ;;
esac
case "$db_user" in
    *[!A-Za-z0-9_.-]* | '')
        echo "backup-postgres: refusing user name '$db_user'" >&2
        exit 1
        ;;
esac

stamp="$(date -u +%Y%m%d%H%M%S)"
build="unknown"
if [ -f version.txt ]; then
    v="$(tr -d '[:space:]' < version.txt | sed 's/^v//')"
    [ -n "$v" ] && build="$v"
fi
file_base="${OUT_DIR}/mql-${db_name}-${stamp}-${build}.dump"
partial="${file_base}.partial"
sum_file="${file_base}.sha256"
# A per-run temp name inside the container: two concurrent backups against the same
# server must not read, overwrite or `rm` each other's staging file.
in_container_tmp="/tmp/mql-backup.$$.dump"

echo "backup-postgres: dumping database '$db_name' from container '$db_container'"
if ! docker exec -T "$db_container" \
    pg_dump -U "$db_user" -d "$db_name" --format=custom --no-owner --file="$in_container_tmp" 2>"${partial}.err"; then
    echo "backup-postgres: pg_dump failed" >&2
    cat "${partial}.err" >&2 || true
    docker exec -T "$db_container" rm -f "$in_container_tmp" >/dev/null 2>&1 || true
    rm -f "${partial}.err" "$partial"
    exit 1
fi

docker exec -T "$db_container" sh -c "cat '$in_container_tmp' && rm -f '$in_container_tmp'" \
    > "$partial" 2>"${partial}.err"
rc=$?
rm -f "${partial}.err"
if [ "$rc" -ne 0 ] || [ ! -s "$partial" ]; then
    echo "backup-postgres: could not copy the dump out of the container (exit $rc)" >&2
    docker exec -T "$db_container" rm -f "$in_container_tmp" >/dev/null 2>&1 || true
    rm -f "$partial"
    exit 1
fi

# Archive readability. The file lives on the HOST, so a check inside a container
# must MOUNT it: `docker run ... pg_restore --list <host-path>` would look for a
# path the container cannot see and would fail on every good backup.
out_abs="$(cd "$OUT_DIR" && pwd)"
partial_name="$(basename "$partial")"
pg_image="$(docker inspect -f '{{.Config.Image}}' "$db_container" 2>/dev/null || true)"
archive_ok=0
if command -v pg_restore >/dev/null 2>&1; then
    pg_restore --list "$partial" >/dev/null 2>&1 && archive_ok=1
fi
if [ "$archive_ok" -ne 1 ] && [ -n "$pg_image" ]; then
    if docker run --rm -v "${out_abs}:/b:ro" --entrypoint pg_restore "$pg_image" \
        --list "/b/${partial_name}" >/dev/null 2>&1; then
        archive_ok=1
    fi
fi
if [ "$archive_ok" -ne 1 ]; then
    echo "backup-postgres: archive failed the readability check (pg_restore --list); discarding" >&2
    rm -f "$partial"
    exit 1
fi

# Only now does the file take its real name, so `ls mql-*.dump` never shows a file
# that has not been parsed.
mv -f "$partial" "$file_base" || { echo "backup-postgres: could not finalise $file_base" >&2; rm -f "$partial"; exit 1; }

if ! ( cd "$OUT_DIR" && sha256sum "$(basename "$file_base")" > "$(basename "$sum_file")" ) \
    || [ ! -s "$sum_file" ]; then
    echo "backup-postgres: could not write the checksum file; removing the backup" >&2
    rm -f "$file_base" "$sum_file"
    exit 1
fi

echo "backup-postgres: OK  $(basename "$file_base")  (readable: pg_restore --list)"

# Retention: keep the KEEP newest backups, then remove older ones. The glob is
# anchored to this tool's own naming scheme inside --out-dir, so a foreign file, a
# symlink target or anything outside that directory is never a candidate.
if [ "$KEEP" -gt 0 ]; then
    ls -1t "${OUT_DIR}"/mql-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
        case "$old" in
            "${OUT_DIR}"/mql-*.dump) ;;
            *) continue ;;
        esac
        [ -f "$old" ] || continue
        rm -f -- "$old" "${old}.sha256" "${old}.partial" 2>/dev/null || true
        echo "backup-postgres: pruned old backup $(basename "$old")" >&2
    done
fi

echo "backup-postgres: wrote $(basename "$file_base") and $(basename "$sum_file")"
exit 0