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

dump_context() {
    log "---- diagnostics ----"
    log "role=$ROLE port=$PORT"
    log "python: $(python -V 2>&1)"
    log "PYTHONPATH=$PYTHONPATH"
    log "app importable: $(python -c 'import app; print(app.__version__)' 2>&1 | tail -n 1)"
    log "alembic current: $(alembic current 2>&1 | tail -n 2 | tr '\n' ' ')"
    log "----------------------"
}

# Wait for PostgreSQL with a real query (not just a TCP connect, which can
# succeed while the server is still initialising).
# 30 x 2s = 60s ceiling keeps startup inside the container healthcheck budget.
wait_for_db() {
    i=0
    while [ "$i" -lt 30 ]; do
        if python -c '
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
' >/dev/null 2>&1; then
            log "database is ready"
            return 0
        fi
        i=$((i + 1))
        log "waiting for database ($i/30)"
        sleep 2
    done
    log "WARNING: database not ready after 60s; continuing so alembic reports the real error"
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
        wait_for_db
        run_migrations
        log "starting API on port ${PORT}"
        exec uvicorn app.api.main:app \
            --host 0.0.0.0 --port "${PORT}" \
            --proxy-headers --forwarded-allow-ips='*' \
            --no-server-header
        ;;
    worker)
        wait_for_db
        log "starting celery worker"
        exec celery -A app.workers.celery_app.celery_app worker \
            --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-2}"
        ;;
    scheduler)
        wait_for_db
        log "starting celery beat"
        exec celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
        ;;
    migrate)
        wait_for_db
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
