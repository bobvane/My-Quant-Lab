#!/usr/bin/env bash
# One door for the question "did the stack we just started actually serve?"
# (ADR-076).
#
# Both pipelines that publish images have to answer that question after they
# start a stack: the release pipeline (release.yml, pinned to the released tag)
# and the nightly pipeline (nightly.yml, pinned to :nightly). They used to
# answer it in two different places -- and the nightly pipeline did not answer
# it at all -- so the verdict lives here, once, and both workflows call it.
#
# usage: scripts/verify-stack.sh [--api URL] [--web-base URL] [--attempts N] [--interval S]
#
#   --api URL        dependency-health endpoint of the API (default http://127.0.0.1:8080/api/v1/health)
#   --web-base URL   base URL of the web edge       (default http://127.0.0.1:8081)
#   --attempts N     how many times to poll         (default 30)
#   --interval S     seconds between attempts       (default 5)
#
# The API half is deliberately NOT /api/v1/healthz: that probe "touches nothing"
# on purpose, so it answers 200 for a stack whose workers never came up (a
# crash-looping celery container is still "up" for `docker compose up -d`, which
# exits 0). What both pipelines need to know before they call a release good is
# the dependency verdict: /api/v1/health reports status=healthy (database
# reachable *and* migration named) and workers="<n> online" (ADR-069/071/090).
#
# Exit codes: 0 = both halves answered, 1 = at least one of them did not,
# 2 = the arguments were wrong (a caller that cannot ask is not a pass).
set -uo pipefail

api_url="http://127.0.0.1:8080/api/v1/health"
web_base="http://127.0.0.1:8081"
attempts=30
interval=5

while [ "$#" -gt 0 ]; do
  case "$1" in
    --api)
      api_url="${2:-}"
      shift 2
      ;;
    --web-base)
      web_base="${2:-}"
      shift 2
      ;;
    --attempts)
      attempts="${2:-}"
      shift 2
      ;;
    --interval)
      interval="${2:-}"
      shift 2
      ;;
    *)
      echo "verify-stack: unknown argument '$1'" >&2
      echo "usage: scripts/verify-stack.sh [--api URL] [--web-base URL] [--attempts N] [--interval S]" >&2
      exit 2
      ;;
  esac
done

if [ -z "$api_url" ] || [ -z "$web_base" ]; then
  echo "verify-stack: --api and --web-base need a value" >&2
  exit 2
fi
web_base="${web_base%/}"

api_ok=0
web_ok=0
attempt=0

while [ "$attempt" -lt "$attempts" ]; do
  attempt=$((attempt + 1))

  api_ok=0
  api_reason='没有回答'
  # Read the two fields that decide this: a stack is serving only if its
  # dependencies are (status=healthy) and somebody is there to run the work
  # (workers="<n> online", never "0 online" or "unknown").
  if api_body=$(curl -fsS "$api_url" 2>/dev/null); then
    api_status=$(printf '%s' "$api_body" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([a-z]*\)".*/\1/p')
    api_workers=$(printf '%s' "$api_body" | sed -n 's/.*"workers"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    api_reason="status=${api_status:-?} workers=${api_workers:-?}"
    case "$api_workers" in
      [1-9]*' online')
        if [ "$api_status" = "healthy" ]; then
          api_ok=1
        fi
        ;;
    esac
  fi

  web_ok=0
  if curl -fsS "${web_base}/healthz" >/dev/null 2>&1; then
    shell=$(curl -fsS "${web_base}/" 2>/dev/null || true)
    missing=$(curl -sS -o /dev/null -w '%{http_code}' \
      "${web_base}/assets/does-not-exist.js" 2>/dev/null || echo 000)
    case "$shell" in
      # The shell has to be the application, not an nginx welcome page, and a
      # missing hashed asset has to be a 404 rather than that shell (ADR-068).
      *'id="app"'*)
        if [ "$missing" = "404" ]; then
          web_ok=1
        fi
        ;;
    esac
  fi

  echo "attempt ${attempt}/${attempts}: api_ok=${api_ok} (${api_reason}) web_ok=${web_ok}"

  if [ "$api_ok" -eq 1 ] && [ "$web_ok" -eq 1 ]; then
    break
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    sleep "$interval"
  fi
done

echo "api_ok: ${api_ok} (${api_reason})   web_ok: ${web_ok}   attempts: ${attempt}"
if [ "$api_ok" -ne 1 ] || [ "$web_ok" -ne 1 ]; then
  echo "VERIFY_STACK_FAILED (api_ok ${api_ok}, web_ok ${web_ok} after ${attempt} attempts; api ${api_reason})"
  exit 1
fi
echo "VERIFY_STACK_OK"
