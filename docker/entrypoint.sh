#!/bin/sh
# Role-based entrypoint for the shared backend image.
#
#   APP_ROLE=api        run migrations, then serve the FastAPI app
#   APP_ROLE=worker     run the Celery worker
#   APP_ROLE=scheduler  run Celery beat
#   APP_ROLE=migrate    run migrations only, then exit
#   APP_ROLE=shell      drop into a shell
set -eu

ROLE="${APP_ROLE:-api}"
PORT="${PORT:-8080}"

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
    log "role=$ROLE port=$PORT"
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
# `wait_for_db --required` is for the roles that never run migrations: they have
# no alembic behind them to name the reason, so a budget that runs out must stop
# the container. Without it a worker whose database is unreachable stayed up and
# failed every task while compose (which only pings celery) called it healthy
# (ADR-099).
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
            log "check POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB and DB_HOST where this deployment reads them"
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
        log "ERROR: database not ready after $((attempts * interval))s ($attempts attempts); this role runs no migrations, so nothing else would report it"
        log "reason: $last_reason"
        return 1
    fi
    log "WARNING: database not ready after $((attempts * interval))s ($attempts attempts); continuing so alembic reports the real error"
    return 0
}

run_migrations() {
    # Several API/worker replicas can start at once; alembic serialises on the
    # version table, and a failure here must stop the container loudly rather
    # than let the app serve against a half-migrated schema.
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

case "$ROLE" in
    api)
        check_settings || exit 1
        wait_for_db || exit 1
        run_migrations
        log "starting API on port ${PORT}"
        exec uvicorn app.api.main:app \
            --host 0.0.0.0 --port "${PORT}" \
            --proxy-headers --forwarded-allow-ips='*' \
            --no-server-header
        ;;
    worker)
        check_settings || exit 1
        wait_for_db --required || exit 1
        log "starting celery worker"
        exec celery -A app.workers.celery_app.celery_app worker \
            --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-2}"
        ;;
    scheduler)
        check_settings || exit 1
        wait_for_db --required || exit 1
        log "starting celery beat"
        exec celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
        ;;
    migrate)
        check_settings || exit 1
        wait_for_db || exit 1
        run_migrations
        log "migrations complete"
        ;;
    shell)
        exec /bin/sh
        ;;
    *)
        log "unknown APP_ROLE: $ROLE"
        exit 1
        ;;
esac
