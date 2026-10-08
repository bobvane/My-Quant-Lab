#!/bin/bash
# Entrypoint for the single application image.
#
#   APP_ROLE=app      run migrations once, then start the four long-lived
#                     processes: celery beat, celery worker, uvicorn/FastAPI and
#                     nginx (the default; docker-compose.yml starts exactly this)
#   APP_ROLE=migrate  run migrations only, then exit (an operator's manual step)
#   APP_ROLE=shell    drop into a shell
#
# `app` is a launcher, not a supervisor: it starts each child once, forwards
# SIGTERM/SIGINT to all of them, and treats ANY child exit — clean or not — as
# "this container is no longer complete", tears the others down and exits
# non-zero so Docker's `restart: unless-stopped` reconciles the whole App. There
# is deliberately no per-child restart, no backoff and no third-party process
# manager (supervisord/s6/tini-as-supervisor); `init: true` in compose provides
# the PID-1 reaping (ADR-190).
#
# Bash, not sh: the launcher needs arrays and `wait -n` (bash >= 4.3).
set -Eeuo pipefail

ROLE="${APP_ROLE:-app}"

# How long the children get to stop after a SIGTERM before they are SIGKILLed.
# Deliberately shorter than compose's `stop_grace_period: 60s`, so this launcher
# always finishes its own shutdown before Docker escalates.
SHUTDOWN_GRACE_SECONDS="${SHUTDOWN_GRACE_SECONDS:-30}"

CELERY_APP="app.workers.celery_app.celery_app"
# Celery beat keeps its schedule in the celery_beat volume; see §10 of docs/33
# for why the compose healthcheck reads this file's mtime.
BEAT_SCHEDULE="/app/beat/celerybeat-schedule"
BEAT_PIDFILE="/app/beat/celerybeat.pid"
NGINX_RENDERED_CONF="/tmp/quantlab-nginx.conf"
NGINX_TEMPLATE="/etc/nginx/quantlab-app.conf.template"

# The app package lives in /app/app. Do not rely on the current directory
# happening to be on sys.path — `python -` (stdin) does not always add it, and a
# silently unimportable app turns the readiness probe into an endless retry.
export PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

log() { echo "[entrypoint] $*"; }

# Report a configuration error before anything starts waiting for the database.
# Reading the settings is what raises, and the old order (wait_for_db first) hid
# the reason: a refused SECRET_KEY looked like 60 s of "waiting for database"
# followed by "migrations failed", which names the wrong cause entirely
# (ADR-077).
check_settings() {
    environment=$(python -c 'from app.core.config import settings; print(settings.environment)' 2>&1) && {
        log "configuration accepted (environment=${environment})"
        return 0
    }
    log "ERROR: configuration is not usable; refusing to start"
    printf '%s\n' "$environment" | sed 's/^/[entrypoint] /'
    return 1
}

dump_context() {
    log "---- diagnostics ----"
    log "role=$ROLE"
    log "python: $(python -V 2>&1)"
    log "PYTHONPATH=$PYTHONPATH"
    log "app importable: $(python -c 'import app; print(app.__version__)' 2>&1 | tail -n 1)"
    log "alembic current: $(alembic current 2>&1 | tail -n 2 | tr '\n' ' ')"
    log "----------------------"
}

# Ask the database a real question (not just a TCP connect, which can succeed
# while the server is still initialising). The answer — including the failure —
# goes to stderr, and the caller keeps it: a reason nobody prints is a reason
# nobody can act on (ADR-080).
database_probe() {
    python -c '
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from app.core.config import settings

try:
    engine = create_engine(settings.database_url, poolclass=NullPool)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    engine.dispose()
except Exception as exc:
    print("not ready: %s" % exc, file=sys.stderr)
    sys.exit(1)
'
}

# Some answers cannot change while the clock runs: a password the server will
# never accept, a database or role that was never created, a driver that is not
# installed, a host name that does not resolve. Waiting 60 s for those turns a
# one-line configuration mistake into a minute of silence followed by a
# migration error that names the wrong cause (ADR-080).
database_error_is_permanent() {
    case "$1" in
        *"password authentication failed"*) return 0 ;;
        *"no password supplied"*) return 0 ;;
        *"does not exist"*) return 0 ;;
        *"NoSuchModuleError"*) return 0 ;;
        *"could not translate host name"*) return 0 ;;
        *"Name or service not known"*) return 0 ;;
        *"nodename nor servname provided"*) return 0 ;;
        *) return 1 ;;
    esac
}

# Wait for PostgreSQL, then give up and let alembic report the real error.
# The budget is an environment variable so the ceiling stays a decision:
# defaults 30 x 2 s = 60 s, inside the container healthcheck budget (ADR-080).
#
# `--required` makes a spent budget fatal. The App uses it because the App IS the
# deployment: if PostgreSQL is still not answering after the whole budget, a
# container that came up anyway would say "healthy" for four children that cannot
# serve anything, and there is no second container left to name the reason
# (ADR-099, ADR-190). `migrate` does not: an operator running migrations by hand is
# watching the output and alembic's own error names the host, port and database it
# tried, which is the better message when the caller is a human.
wait_for_db() {
    required=0
    if [ "${1:-}" = "--required" ]; then
        required=1
    fi
    attempts="${DB_WAIT_ATTEMPTS:-30}"
    interval="${DB_WAIT_INTERVAL:-2}"
    i=0
    last_reason=''
    while [ "$i" -lt "$attempts" ]; do
        if reason=$(database_probe 2>&1); then
            log "database is ready"
            return 0
        fi
        reason=$(printf '%s' "$reason" | tr '\n' ' ' | sed 's/  */ /g')
        if database_error_is_permanent "$reason"; then
            log "ERROR: the database answered with a problem that waiting cannot fix; giving up now instead of retrying ${attempts} times"
            log "reason: $reason"
            log "check POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB and POSTGRES_HOST where this deployment reads them"
            return 1
        fi
        if [ "$reason" != "$last_reason" ]; then
            log "database not ready yet: $reason"
            last_reason="$reason"
        fi
        i=$((i + 1))
        log "waiting for database ($i/$attempts)"
        sleep "$interval"
    done
    if [ "$required" -eq 1 ]; then
        log "ERROR: database not ready after $((attempts * interval))s ($attempts attempts)"
        log "reason: $last_reason"
        return 1
    fi
    log "WARNING: database not ready after $((attempts * interval))s ($attempts attempts); continuing so alembic reports the real error"
    return 0
}

# Migrations run here — in the launcher's own process, once, before any
# long-lived child starts. In the merged container nobody else migrates at all,
# so a failure must stop the App loudly rather than let it serve (or let a
# worker consume tasks) against a schema that is behind (ADR-099).
#
# Several App containers can still be started by hand; alembic serialises on the
# version table, so retrying is safe.
run_migrations() {
    attempt=1
    while [ "$attempt" -le 3 ]; do
        log "running database migrations (attempt ${attempt}/3)"
        if alembic upgrade head; then
            log "migrations complete"
            return 0
        fi
        log "migration attempt ${attempt} failed"
        attempt=$((attempt + 1))
        sleep 5
    done
    log "ERROR: migrations failed; refusing to start"
    dump_context
    return 1
}

# ---------------------------------------------------------------------------
# The merged App: render nginx's config, then start the four children.
# ---------------------------------------------------------------------------

# The bearer token must reach nginx's proxy_set_header without being baked into
# the image and without leaving the container. The old web image used envsubst;
# this image has no gettext-base on purpose, and Python is guaranteed present,
# so the substitution is three lines of Python instead (ADR-190). Only
# ${AUTH_LINE} is replaced — nginx's own $host/$remote_addr stay intact.
render_nginx_config() {
    if [ -n "${API_AUTH_TOKEN:-}" ]; then
        AUTH_LINE="proxy_set_header Authorization \"Bearer ${API_AUTH_TOKEN}\";"
    else
        AUTH_LINE=""
    fi
    export AUTH_LINE

    python - <<'PY'
import os
from pathlib import Path

template = Path("/etc/nginx/quantlab-app.conf.template")
rendered = Path("/tmp/quantlab-nginx.conf")
rendered.write_text(
    template.read_text().replace("${AUTH_LINE}", os.environ.get("AUTH_LINE", ""))
)
PY

    if [ -n "${API_AUTH_TOKEN:-}" ]; then
        log "rendered the nginx configuration (bearer token injected)"
    else
        log "rendered the nginx configuration (no bearer token configured)"
    fi
}

# One array of child PIDs, one of printable names, same index. No PID files:
# nothing outside this process needs them.
pids=()
names=()

start() {
    local name="$1"
    shift
    log "starting ${name}: $*"
    "$@" &
    pids+=("$!")
    names+=("$name")
}

terminate_children() {
    local pid
    for pid in "${pids[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
}

kill_children() {
    local pid
    for pid in "${pids[@]}"; do
        kill -KILL "$pid" 2>/dev/null || true
    done
}

# Bounded wait: poll the children for up to SHUTDOWN_GRACE_SECONDS, then SIGKILL
# the survivors. Always returns 0 — stopping is the caller's decision.
wait_for_children() {
    local waited=0 alive pid
    while [ "$waited" -lt "$SHUTDOWN_GRACE_SECONDS" ]; do
        alive=0
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                alive=1
                break
            fi
        done
        if [ "$alive" -eq 0 ]; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    log "children still running after ${SHUTDOWN_GRACE_SECONDS}s; sending SIGKILL"
    kill_children
    return 0
}

# SIGTERM/SIGINT is an operator asking us to stop: forward it, wait, and exit 0
# so Docker records a clean stop rather than a crash.
on_signal() {
    trap - TERM INT
    log "received a stop signal; terminating ${names[*]:-no children}"
    terminate_children
    wait_for_children
    wait 2>/dev/null || true
    log "stopped"
    exit 0
}

# A child that exits on its own — even with status 0 — means the container is
# incomplete: without nginx the UI is gone, without uvicorn the API is gone,
# without the worker every task queues, without beat nothing is scheduled. Tear
# the rest down and exit non-zero; compose's `restart: unless-stopped` starts a
# complete App again.
on_child_exit() {
    local status="$1"
    log "a critical process exited (status ${status}); stopping the App"
    terminate_children
    wait_for_children
    wait 2>/dev/null || true
    exit 1
}

main_app() {
    # The database first: check_settings names a bad configuration immediately,
    # wait_for_db names an unreachable database, run_migrations names a broken
    # schema. None of the four children starts until all three are satisfied.
    check_settings || exit 1
    wait_for_db --required || exit 1
    run_migrations || exit 1

    render_nginx_config

    # Installed before the first child exists so a `docker stop` during startup
    # is still a clean signal-driven exit.
    trap on_signal TERM INT

    # Order: beat, worker, api, nginx. Two reasons for that order.
    #   * the door opens last: a client never reaches a container whose API or
    #     static files are not up yet.
    #   * beat goes first only because the requested order lists it first, which
    #     is harmless: `celery beat` merely publishes to Redis, and Redis queues
    #     the messages until the worker connects a moment later.
    # Beat stays a child of its own — never `celery worker -B` — so its death is
    # observable and only one Beat can ever exist.
    start beat celery -A "$CELERY_APP" beat \
        --loglevel=INFO --schedule="$BEAT_SCHEDULE" --pidfile="$BEAT_PIDFILE"
    start worker celery -A "$CELERY_APP" worker \
        --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-2}"
    # `--host 0.0.0.0` is required, not sloppy: compose publishes this port as
    # `${API_BIND:-127.0.0.1}:${API_PORT:-8080}:8000`, and Docker forwards a
    # published port to the container's interface, never to its loopback — a
    # loopback listener would leave that door dead (CI caught exactly that).
    # The browser still talks to nginx alone, and the API's own door stays a
    # loopback-bound *host* port, which is how the separate api container had it
    # in v2.5.0.
    start api uvicorn app.api.main:app \
        --host 0.0.0.0 --port 8000 \
        --proxy-headers --forwarded-allow-ips='*' \
        --no-server-header
    start nginx nginx -c "$NGINX_RENDERED_CONF" -g 'daemon off;'

    log "all four processes are running: ${names[*]}"

    # `wait -n` returns when the first child exits, whatever its status. `||`
    # keeps `set -e` from aborting before the teardown runs.
    status=0
    wait -n || status=$?
    on_child_exit "$status"
}

case "$ROLE" in
    app)
        main_app
        ;;
    migrate)
        # Deliberately the soft wait: this role exists for an operator who is
        # watching, and alembic's failure names the connection it tried.
        check_settings || exit 1
        wait_for_db || exit 1
        run_migrations || exit 1
        log "migrations complete"
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        log "unknown APP_ROLE: $ROLE"
        exit 1
        ;;
esac
