#!/usr/bin/env bash
# Pre-flight checks before `docker compose up`.
#
# Standard deploy needs only docker-compose.yml + .env (images come from GHCR),
# so this script verifies: compose file present, .env configured, images
# reachable, docker daemon working.
#
# Usage:  ./scripts/preflight.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

echo "My Quant Lab pre-flight  (repo: $REPO_ROOT)"
echo

echo "Repository contents"
for f in docker/Dockerfile.backend docker/Dockerfile.web docker/entrypoint.sh \
         docker/web.nginx.conf docker-compose.yml backend/requirements.txt \
         backend/app/api/main.py frontend/package.json frontend/package-lock.json; do
    if [ -e "$f" ]; then ok "$f"; else bad "$f is missing — your copy is incomplete or out of date"; fi
done

if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    behind=$(git -C "$REPO_ROOT" fetch --dry-run 2>/dev/null | wc -l || echo 0)
    head=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "?")
    ok "git checkout at $head"
    if [ "${behind:-0}" -gt 0 ]; then
        warn "origin has newer commits — run: git pull"
    fi
else
    warn "not a git checkout; cannot detect updates (git pull unavailable)"
fi

echo
echo "Configuration"
if [ -f .env ]; then
    ok ".env exists"
    for key in POSTGRES_PASSWORD SECRET_KEY; do
        value=$(grep -E "^${key}=" .env | head -n 1 | cut -d= -f2- || true)
        if [ -z "$value" ]; then
            bad ".env: $key is empty"
        elif printf '%s' "$value" | grep -qiE 'change-me|replace|^your'; then
            bad ".env: $key still holds a placeholder value"
        else
            ok ".env: $key is set"
        fi
    done

    policy=$(grep -E '^MQL_VERSION=' .env | head -n 1 | cut -d= -f2- || echo latest)
    if [ -z "$policy" ]; then
        policy=latest
    fi
    ok ".env: MQL_VERSION=${policy}"
    for img in "ghcr.io/bobvane/my-quant-lab-backend:${policy}" \
               "ghcr.io/bobvane/my-quant-lab-web:${policy}"; do
        if docker manifest inspect "$img" >/dev/null 2>&1; then
            ok "image reachable ($img)"
        else
            bad "image NOT reachable ($img)"
            echo "       packages must be public, or run: docker login ghcr.io"
            echo "       check https://github.com/bobvane/My-Quant-Lab/pkgs/container/my-quant-lab-backend"
        fi
    done
else
    bad ".env not found — run: cp .env.example .env  and edit POSTGRES_PASSWORD / SECRET_KEY"
fi

echo
echo "Docker"
if command -v docker >/dev/null 2>&1; then
    ok "docker CLI present"
    if docker info >/dev/null 2>&1; then
        ok "docker daemon reachable"
    else
        bad "docker daemon not reachable (is Docker running? do you need sudo?)"
    fi
    if docker compose version >/dev/null 2>&1; then
        ok "docker compose (v2) present"
    else
        bad "docker compose v2 plugin missing"
    fi
else
    bad "docker CLI not found"
fi

echo
if [ "$fail" -ne 0 ]; then
    echo "Pre-flight FAILED — fix the items above before running docker compose."
    exit 1
fi
echo "Pre-flight passed. Next:"
echo "  docker compose pull && docker compose up -d"
echo "  docker compose logs -f quantlab-api"
