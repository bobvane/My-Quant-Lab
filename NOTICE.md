# Dependencies, licenses, and attribution

This repository contains configuration only. It does not contain, modify, or
redistribute any application source code.

## What the stack runs

| Component | Source | License |
| --- | --- | --- |
| `mql-api`, `mql-trading`, `mql-scheduler`, `mql-celery`, `mql-beat`, `mql-migration` | `ghcr.io/openbyteinc/quantdinger-backend`, published by [OpenByteInc/QuantDinger](https://github.com/OpenByteInc/QuantDinger) | Apache License 2.0 |
| `mql-web` | `ghcr.io/openbyteinc/quantdinger-frontend`, same publisher | Source-available, non-commercial use only |
| `mql-db` | `postgres` official image | PostgreSQL License |
| `mql-redis`, `mql-redis-jobs` | `redis` official image | Redis Source Available License / RSALv2 or SSPLv1 (see upstream) |

Images are pulled by digest-compatible release tag (`BACKEND_TAG` / `FRONTEND_TAG`
in `.env`) directly from the registry at container start. We do not host, mirror,
or re-publish them.

## Trademarks

"QuantDinger" and the QuantDinger logo belong to OpenByte Inc. They appear inside
the running application because that is the software being run. They are not used
as the name or branding of this repository, and nothing here implies endorsement
by or affiliation with the upstream project.

## If you need the source

The upstream project is public at <https://github.com/OpenByteInc/QuantDinger>.
Building the backend from source yourself is the supported path if you need
behaviour changes; this deployment layer will keep working with an image you
publish yourself by pointing `BACKEND_IMAGE` at it.

## No warranty

The configuration here is provided as-is, with no warranty of any kind. It is
maintained for one household's NAS. Nothing in it is investment advice.
