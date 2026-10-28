# My Quant Lab

Deployment layer for a personal quant-research instance that runs on **one NAS**.
It tracks US stocks and crypto, backtests strategies, keeps a **paper portfolio**
(no live orders are ever sent), and pushes signals plus a daily summary to
Telegram.

This repository contains **only** the deployment layer: a Compose file, a
configuration template, and operating notes. The application itself is pulled as
prebuilt container images, so there is no application source here.

- 中文说明: [README_CN.md](README_CN.md)
- 在飞牛 fnOS 上部署: [docs/NAS_DEPLOYMENT_CN.md](docs/NAS_DEPLOYMENT_CN.md)
- 依赖与许可说明: [NOTICE.md](NOTICE.md)

## What's here

| Path | Purpose |
| --- | --- |
| `docker-compose.yml` | The whole stack: 10 containers |
| `.env.example` | Configuration template; copy to `.env` |
| `scripts/bootstrap_env.py` | Fills the three required secrets with random values |
| `docs/NAS_DEPLOYMENT_CN.md` | Step-by-step NAS deployment (Chinese) |
| `VERSION` | Version of this deployment layer; release tags must match it |
| `.github/workflows/validate.yml` | Validates the Compose file on every push |
| `.github/workflows/release.yml` | Turns a `v*` tag into a Release plus a matching package |

## Stack

| Container | Role |
| --- | --- |
| `mql-web` | Serves the web UI and proxies `/api` |
| `mql-api` | HTTP API |
| `mql-trading` | Strategy runtime: evaluates bars, records paper trades |
| `mql-scheduler` | Portfolio monitor, order bookkeeping, market catalog sync |
| `mql-celery` | Backtests, AI jobs, maintenance |
| `mql-beat` | Periodic task scheduler |
| `mql-migration` | One-shot schema bootstrap |
| `mql-db` | PostgreSQL 18 — durable source of truth |
| `mql-redis` | Disposable cache |
| `mql-redis-jobs` | Durable Celery broker |

No message broker, no mobile client, no observability stack. Strategies run
in-process inside `mql-trading`, which is what makes ten containers enough.

## Quick start

Requirements: x86_64 host, Docker with Compose v2, 4 CPU cores, 8 GB RAM.

```bash
cp .env.example .env
python scripts/bootstrap_env.py     # fills POSTGRES_PASSWORD, SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY
docker compose pull
docker compose up -d
```

Open `http://<nas-ip>:8888` and sign in with `ADMIN_USER` / `ADMIN_PASSWORD`
from `.env`.

The migration container has to finish before the API starts; the first boot takes
about a minute. `docker compose ps` shows `mql-migration` as `exited (0)` when it
succeeded.

## Ports

| Port | Bound to | Why |
| --- | --- | --- |
| 8888 | all interfaces | Web UI, needed from the LAN |
| 5000 | loopback | API; reached through the web container's proxy |
| 5432 | loopback | PostgreSQL; direct access for admin only |
| 6379 | loopback | Redis cache; direct access for admin only |

If the NAS is reachable from the internet, put a TLS reverse proxy in front of
port 8888 and do not expose the others.

## Operating

```bash
docker compose ps                      # health
docker compose logs -f mql-api         # follow one service
docker compose pull && docker compose up -d    # update
docker compose down                    # stop, keep data
docker compose down -v                 # stop and delete the database
```

Back up the database with:

```bash
docker compose exec mql-db pg_dump -U quantdinger quantdinger > backup-$(date +%F).sql
```

Image versions are pinned in `.env` (`BACKEND_TAG`, `FRONTEND_TAG`). Bumping them
is a deliberate change: edit `.env`, then `docker compose pull && docker compose up -d`.
Keep `CREDENTIAL_ENCRYPTION_KEY` stable across upgrades or stored exchange keys
become unreadable.

## Versioning

This deployment layer is versioned on its own. `0.0.1` is the first published
version; the application is pinned separately through `BACKEND_TAG` /
`FRONTEND_TAG` in `.env`.

The version lives in `VERSION` and is released by pushing a matching tag:

```bash
git tag v0.0.2
git push origin v0.0.2
```

Each tag produces two artifacts with the same number:

| Artifact | What it is |
| --- | --- |
| GitHub Release | Release notes plus `my-quant-lab-<version>.zip`, the whole deployment layer |
| GHCR package `ghcr.io/bobvane/my-quant-lab:<version>` | The same files as a tiny container image |

The package is a snapshot of this deployment layer, **not** the application. It
exists so every release has an immutable, version-matched artifact you can pull:

```bash
docker pull ghcr.io/bobvane/my-quant-lab:0.0.1
docker run --rm ghcr.io/bobvane/my-quant-lab:0.0.1
```

The version in `VERSION` must match the tag; the release workflow refuses to
publish if they differ.

## Notes

- Nothing in this stack sends live orders. Exchange credentials, if you add any,
  are only used to read balances and prices. Paper fills are computed locally.
- Paper results are hypothetical. They use historical and delayed data and do not
  model fees, slippage, or liquidity the way a live account would.
- Licensing and attribution: see [NOTICE.md](NOTICE.md).
