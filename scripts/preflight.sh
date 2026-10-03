#!/usr/bin/env bash
# My Quant Lab — pre-flight checks before `docker compose up` (ADR-078).
#
# The supported deployment is TWO files: docker-compose.yml + .env, with images
# pulled from GHCR. This script checks exactly that deployment:
#
#   * the compose file and the env file exist, and the compose file parses;
#   * EVERY project image the compose file names is either already present
#     locally (CI builds its own) or reachable in the registry. The list is
#     DERIVED from the compose file: the previous hardcoded pair of image names
#     silently ignored the docker-proxy image (ADR-076, ADR-078);
#   * SECRET_KEY satisfies the same rule the API enforces at startup (ADR-077),
#     with the shell environment winning over the env file, exactly as compose
#     resolves it;
#   * source files (Dockerfiles) are demanded only when the compose file
#     actually builds from source — they are not part of a two-file deploy.
#
# Usage:
#   ./scripts/preflight.sh [--compose docker-compose.yml] [--env-file .env]
#
# Run it against a deployment that lives elsewhere (only compose + env needed):
#   ./scripts/preflight.sh --compose /volume1/docker/mql/docker-compose.yml \
#                          --env-file /volume1/docker/mql/.env
#
# Exit codes: 0 = ready, 1 = a check failed, 2 = bad usage.
set -euo pipefail

COMPOSE_PATH="docker-compose.yml"
ENV_PATH=".env"

# Kept identical to backend/app/core/config.py (ADR-077); the guard test
# test_deploy_preflight.py fails if the two drift apart, because a secret rule
# that differs from the one the API enforces is not a rule.
SECRET_PLACEHOLDERS=("change-me" "change_me" "changeme" "your-" "replace-me" "example" "placeholder" "insecure")
PUBLISHED_SECRETS=("change-me-openssl-rand-hex-32" "change-me-in-production" "0123456789abcdef0123456789abcdef")
SECRET_MIN_LENGTH=32

usage() {
    cat <<'EOF'
usage: preflight.sh [--compose PATH] [--env-file PATH]

  --compose PATH    compose file to inspect (default: docker-compose.yml)
  --env-file PATH   env file the deployment uses (default: .env)
  -h, --help        show this help

Exit codes: 0 = ready, 1 = a check failed, 2 = bad usage.
EOF
}

usage_error() {
    echo "preflight: $1" >&2
    echo >&2
    usage >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --compose)
            [ $# -ge 2 ] || usage_error "--compose needs a path"
            COMPOSE_PATH="$2"
            shift 2
            ;;
        --env-file)
            [ $# -ge 2 ] || usage_error "--env-file needs a path"
            ENV_PATH="$2"
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

fail=0
ok() { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad() {
    printf '  \033[31mFAIL\033[0m %s\n' "$1"
    fail=1
}
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

# Value of a variable as `docker compose` would resolve it: the shell
# environment wins, and only then does the env file apply. Quotes and a stray
# CR (an env file written by a Windows editor) are stripped the way compose does.
env_value() {
    local key="$1" value=""
    value="$(printenv "$key" 2>/dev/null || true)"
    if [ -z "$value" ] && [ -f "$ENV_PATH" ]; then
        value="$(grep -E "^[[:space:]]*${key}=" "$ENV_PATH" | head -n 1 | cut -d= -f2- || true)"
    fi
    value="${value%$'\r'}"
    case "$value" in
        \"*\") value="${value#\"}" && value="${value%\"}" ;;
        \'*\') value="${value#\'}" && value="${value%\'}" ;;
    esac
    printf '%s' "$value"
}

# Expand ${VAR} and ${VAR:-default} in a compose value (only these two forms
# appear in this project's compose file).
expand_image() {
    local out="$1" prefix suffix token name fallback value
    while [[ "$out" == *'${'* ]]; do
        prefix="${out%%\$\{*}"
        token="${out#*\$\{}"
        token="${token%%\}*}"
        suffix="${out#*\$\{}"
        suffix="${suffix#*\}}"
        if [[ "$token" == *:-* ]]; then
            name="${token%%:-*}"
            fallback="${token#*:-}"
        else
            name="$token"
            fallback=""
        fi
        value="$(env_value "$name")"
        [ -n "$value" ] || value="$fallback"
        out="${prefix}${value}${suffix}"
    done
    printf '%s' "$out"
}

# Project images named by the compose file, expanded. Upstream images
# (postgres, redis) are someone else's to publish, so they are filtered out by
# the project's own registry path rather than by a hand-written list.
compose_images() {
    local raw expanded
    [ -f "$COMPOSE_PATH" ] || return 0
    while IFS= read -r raw; do
        [ -n "$raw" ] || continue
        expanded="$(expand_image "$raw")"
        case "$expanded" in
            *my-quant-lab*) printf '%s\n' "$expanded" ;;
        esac
    done < <(grep -E '^[[:space:]]*image:[[:space:]]*' "$COMPOSE_PATH" \
        | sed -E 's/^[[:space:]]*image:[[:space:]]*//' \
        | sed -E 's/[[:space:]]+#.*$//' \
        | sed -E 's/[[:space:]]+$//')
}

# Dockerfiles the compose file builds from source.
compose_dockerfiles() {
    [ -f "$COMPOSE_PATH" ] || return 0
    grep -E '^[[:space:]]*dockerfile:[[:space:]]*' "$COMPOSE_PATH" \
        | sed -E 's/^[[:space:]]*dockerfile:[[:space:]]*//' \
        | sed -E 's/[[:space:]]+#.*$//' \
        | sed -E 's/[[:space:]]+$//' \
        | tr -d '\r'
}

compose_builds_from_source() {
    [ -f "$COMPOSE_PATH" ] && grep -Eq '^[[:space:]]*build:' "$COMPOSE_PATH"
}

check_secret() {
    local key="$1" value="$2" marker published
    if [ -z "$value" ]; then
        bad "$key is not set (checked the shell environment and $ENV_PATH) — generate one: openssl rand -hex 32"
        return 0
    fi
    for marker in "${SECRET_PLACEHOLDERS[@]}"; do
        case "$value" in
            *"$marker"*)
                bad "$key still holds an example value (it contains '$marker'); in production the API refuses to start — generate one: openssl rand -hex 32"
                return 0
                ;;
        esac
    done
    for published in "${PUBLISHED_SECRETS[@]}"; do
        if [ "$value" = "$published" ]; then
            bad "$key is a secret this repository has published; anyone with a database copy can decrypt stored provider keys — generate one: openssl rand -hex 32"
            return 0
        fi
    done
    if [ "${#value}" -lt "$SECRET_MIN_LENGTH" ]; then
        bad "$key must be at least $SECRET_MIN_LENGTH characters in production (it has ${#value}) — generate one: openssl rand -hex 32"
        return 0
    fi
    ok "$key is set and not a published value (${#value} characters)"
}

check_image() {
    local image="$1" repository
    if docker image inspect "$image" >/dev/null 2>&1; then
        ok "image present locally ($image)"
        return 0
    fi
    if docker manifest inspect "$image" >/dev/null 2>&1; then
        ok "image reachable in the registry ($image)"
        return 0
    fi
    repository="${image%%:*}"
    repository="${repository##*/}"
    bad "image neither present locally nor reachable ($image)"
    echo "       packages must be public, or run: docker login ghcr.io"
    echo "       check https://github.com/bobvane/My-Quant-Lab/pkgs/container/${repository}"
}

echo "My Quant Lab pre-flight  (compose: $COMPOSE_PATH  env: $ENV_PATH)"
echo

echo "Deployment files"
if [ -f "$COMPOSE_PATH" ]; then
    ok "$COMPOSE_PATH"
else
    bad "$COMPOSE_PATH not found — a deployment needs this file (see README)"
fi
if [ -f "$ENV_PATH" ]; then
    ok "$ENV_PATH"
else
    bad "$ENV_PATH not found — run: cp .env.example .env  and edit POSTGRES_PASSWORD / SECRET_KEY"
fi

if compose_builds_from_source; then
    echo
    echo "Source files ($COMPOSE_PATH builds from source)"
    dockerfiles="$(compose_dockerfiles || true)"
    if [ -z "$dockerfiles" ]; then
        warn "$COMPOSE_PATH has a build: section but names no dockerfile"
    else
        while IFS= read -r dockerfile; do
            [ -n "$dockerfile" ] || continue
            if [ -e "$dockerfile" ]; then
                ok "$dockerfile"
            else
                bad "$dockerfile is missing — this compose file builds from source, so it needs the full checkout"
            fi
        done <<<"$dockerfiles"
    fi
else
    echo
    echo "Source files"
    warn "$COMPOSE_PATH has no build: sections — images come from the registry, so a two-file deploy needs no source (expected)"
fi

echo
echo "Configuration"
if [ -f "$ENV_PATH" ] || [ -n "$(env_value SECRET_KEY)" ]; then
    check_secret "SECRET_KEY" "$(env_value SECRET_KEY)"
    postgres_password="$(env_value POSTGRES_PASSWORD)"
    if [ -z "$postgres_password" ]; then
        bad "POSTGRES_PASSWORD is not set (checked the shell environment and $ENV_PATH)"
    else
        placeholder=""
        for marker in "${SECRET_PLACEHOLDERS[@]}"; do
            case "$postgres_password" in
                *"$marker"*) placeholder="$marker" ;;
            esac
        done
        if [ -n "$placeholder" ]; then
            bad "POSTGRES_PASSWORD still holds an example value (it contains '$placeholder')"
        else
            ok "POSTGRES_PASSWORD is set"
            if [ "${#postgres_password}" -lt 16 ]; then
                warn "POSTGRES_PASSWORD is shorter than 16 characters"
            fi
        fi
    fi
    mql_version="$(env_value MQL_VERSION)"
    [ -n "$mql_version" ] || mql_version=latest
    ok "MQL_VERSION=$mql_version"
else
    warn "no $ENV_PATH and no exported secrets — skipping the configuration checks"
fi

echo
echo "Images (derived from $COMPOSE_PATH)"
image_count=0
if [ -f "$COMPOSE_PATH" ]; then
    while IFS= read -r image; do
        [ -n "$image" ] || continue
        image_count=$((image_count + 1))
        if command -v docker >/dev/null 2>&1; then
            check_image "$image"
        else
            warn "cannot check $image — docker CLI not found"
        fi
    done < <(compose_images || true)
fi
if [ "$image_count" -eq 0 ]; then
    bad "no project image found in $COMPOSE_PATH — the file does not look like a My Quant Lab deployment"
fi

echo
echo "Docker"
if command -v docker >/dev/null 2>&1; then
    ok "docker CLI present"
    if docker info >/dev/null 2>&1; then
        ok "docker daemon reachable"
        if docker compose version >/dev/null 2>&1; then
            ok "docker compose (v2) present"
            if [ -n "$(env_value SECRET_KEY)" ] && [ -n "$(env_value POSTGRES_PASSWORD)" ]; then
                config_args=(-f "$COMPOSE_PATH" config)
                if [ -f "$ENV_PATH" ]; then
                    config_args=(--env-file "$ENV_PATH" "${config_args[@]}")
                fi
                if docker compose "${config_args[@]}" >/dev/null 2>&1; then
                    ok "$COMPOSE_PATH parses and its required variables are satisfied"
                else
                    bad "$COMPOSE_PATH does not parse, or a required variable is missing (docker compose ${config_args[*]})"
                fi
            else
                warn "skipping the compose parse check — secrets are not configured yet"
            fi
        else
            bad "docker compose v2 plugin missing"
        fi
    else
        bad "docker daemon not reachable (is Docker running? do you need sudo?)"
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
