# My Quant Lab — the single application image.
#
# Four processes share this image (nginx, uvicorn/FastAPI, celery worker, celery
# beat); docker/entrypoint.sh starts them in that order and treats any one of
# them exiting as "the container is no longer complete" (ADR-190). PostgreSQL and
# Redis stay official images in docker-compose.yml; they are not built here.
#
# The frontend is folded in as a build stage so one image carries the API, the
# worker, the scheduler and the UI. That is the whole point of the merge: one
# image to publish, pull and roll back.

# --- stage 1: the Vue app (was docker/Dockerfile.web) -----------------------
FROM node:22-alpine AS web-build

WORKDIR /app

# The lock file is committed so `npm ci` produces a reproducible install.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# --- stage 2: the runtime (was docker/Dockerfile.backend) -------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq for psycopg, curl for the compose healthcheck, procps for the CI
# diagnostics (`ps aux` inside the container on failure), nginx for the static
# UI and the /api reverse proxy. `--no-install-recommends` keeps the nginx
# modules we never configure out of the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl procps nginx \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so code edits do not invalidate the layer
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/ /app/
COPY --from=web-build /app/dist /usr/share/nginx/html
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# A complete nginx configuration, rendered to /tmp by the entrypoint (this file
# is the template: ${AUTH_LINE} is substituted there). It is NOT installed as
# /etc/nginx/nginx.conf because the merged container runs as uid 10001 and
# cannot rewrite that path — render, then start nginx with -c.
COPY docker/app.nginx.conf /etc/nginx/quantlab-app.conf.template

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /app/beat \
    && useradd --create-home --uid 10001 quantlab \
    && chown -R quantlab:quantlab /app

# Everything runs unprivileged: nginx listens on 8080 (not 80), which needs no
# CAP_NET_BIND_SERVICE, and never writes outside /tmp (ADR-190).
USER quantlab

ENV APP_ROLE=app

# 8080 = nginx (published as ${WEB_PORT:-8081}), 8000 = uvicorn on the container
# loopback (published as ${API_PORT:-8080}, bound to 127.0.0.1 on the host).
EXPOSE 8080 8000

# No HEALTHCHECK here on purpose: docker-compose.yml declares one, and the probe
# belongs to the deployment (ADR-100). The probe asks about all four children,
# which is a question only compose can answer.

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
