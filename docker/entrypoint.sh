#!/bin/sh
# Role-based entrypoint for the shared backend image.
#
#   APP_ROLE=api        -> run migrations, then serve the FastAPI app
#   APP_ROLE=worker     -> run Celery worker
#   APP_ROLE=scheduler  -> run Celery beat
#   APP_ROLE=migrate    -> run migrations only, then exit
set -eu

ROLE="${APP_ROLE:-api}"

wait_for_db() {
    # Alembic will fail fast anyway; this gives a clearer log on first boot.
    i=0
    while [ "$i" -lt 30 ]; do
        if python -c "
import sys
from sqlalchemy import create_engine, text
from app.core.config import settings
try:
    e = create_engine(settings.database_url, poolclass=__import__('sqlalchemy').pool.NullPool)
    with e.connect() as c:
        c.execute(text('SELECT 1'))
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            return 0
        fi
        i=$((i + 1))
        echo "waiting for database ($i/30)..."
        sleep 2
    done
    echo "database not reachable, continuing anyway (alembic will report the error)"
    return 0
}

run_migrations() {
    echo ">>> running database migrations"
    alembic upgrade head
}

case "$ROLE" in
    api)
        wait_for_db
        run_migrations
        echo ">>> starting API on port ${PORT:-8080}"
        exec uvicorn app.api.main:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers
        ;;
    worker)
        wait_for_db
        echo ">>> starting celery worker"
        exec celery -A app.workers.celery_app.celery_app worker \
            --loglevel=INFO --concurrency="${CELERY_CONCURRENCY:-2}"
        ;;
    scheduler)
        wait_for_db
        echo ">>> starting celery beat"
        exec celery -A app.workers.celery_app.celery_app beat --loglevel=INFO
        ;;
    migrate)
        wait_for_db
        run_migrations
        echo ">>> migrations complete"
        ;;
    shell)
        exec /bin/sh
        ;;
    *)
        echo "unknown APP_ROLE: $ROLE" >&2
        exit 1
        ;;
esac
