#!/usr/bin/env bash
# My Quant Lab — version and release helper.
#
# Version scheme (project decision, ADR-079): every component counts 0-9 and
# carries over at 10, so the third field is ALWAYS a single digit:
#
#   v1.5.8 → v1.5.9 → v1.6.0 → … → v1.6.9 → v1.7.0 → … → v1.9.9 → v2.0.0
#
# There is no v1.5.10: the release after v1.5.9 is v1.6.0. `set` refuses a
# version that breaks the carry, because a tag that breaks it cannot be named
# by the next bump.
#
#   ./scripts/version.sh show              print the current version
#   ./scripts/version.sh bump [--tag]      bump version.txt, commit, tag, push
#   ./scripts/version.sh set v0.1.0        set an explicit version
#   ./scripts/version.sh notes             print the release notes template
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${REPO_ROOT}/version.txt"
DEFAULT_VERSION="v0.0.1"

current_version() {
    if [ -f "$VERSION_FILE" ]; then
        tr -d '[:space:]' < "$VERSION_FILE"
    else
        echo "$DEFAULT_VERSION"
    fi
}

require_git() {
    git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 || {
        echo "not a git repository: $REPO_ROOT" >&2
        exit 1
    }
}

bump_version() {
    local version major minor patch
    version="$(current_version)"
    version="${version#v}"

    major="${version%%.*}"
    minor="$(echo "$version" | cut -d. -f2)"
    patch="$(echo "$version" | cut -d. -f3)"

    if [ "$patch" -lt 9 ]; then
        patch=$((patch + 1))
    elif [ "$minor" -lt 9 ]; then
        minor=$((minor + 1))
        patch=0
    else
        major=$((major + 1))
        minor=0
        patch=0
    fi

    echo "v${major}.${minor}.${patch}"
}

write_version() {
    printf '%s\n' "$1" > "$VERSION_FILE"
}

sync_env_example() {
    # Keep .env.example's documented MQL_VERSION aligned with the release.
    # The tag keeps the "v" prefix; file contents must not (PEP 440 rejects it).
    local version="$1" version="${1#v}"
    if [ -f "${REPO_ROOT}/.env.example" ]; then
        sed -i.bak "s/^MQL_VERSION=.*/MQL_VERSION=${version}/" "${REPO_ROOT}/.env.example" \
            && rm -f "${REPO_ROOT}/.env.example.bak"
    fi
}

sync_version_references() {
    # version.txt is the single source of truth. Mirror the version WITHOUT the
    # "v" prefix into every package manifest: `pyproject.toml` must be valid
    # PEP 440, and the API reads `app.__version__` to report the running build.
    local version="${1#v}"
    sed -i.bak "s/^__version__ = \".*\"/__version__ = \"${version}\"/" \
        "${REPO_ROOT}/backend/app/__init__.py" 2>/dev/null && rm -f "${REPO_ROOT}/backend/app/__init__.py.bak"
    sed -i.bak "s/^version = \".*\"/version = \"${version}\"/" \
        "${REPO_ROOT}/backend/pyproject.toml" 2>/dev/null && rm -f "${REPO_ROOT}/backend/pyproject.toml.bak"
    sed -i.bak 's/^\([[:space:]]*"version":[[:space:]]*"\)[^"]*\("\)/\1'"${version}"'\2/' \
        "${REPO_ROOT}/frontend/package.json" 2>/dev/null && rm -f "${REPO_ROOT}/frontend/package.json.bak"
    # package-lock.json carries the version twice: once at the top level and once
    # in the root entry of "packages". Both are 2-space indented and sit next to
    # "name", which no dependency entry has, so anchoring on "name" keeps the
    # rewrite off the dependency tree. Without this the lock file silently drifts
    # behind package.json on every release (it had stuck at 0.9.8 while the app
    # was already at 1.0.0).
    if [ -f "${REPO_ROOT}/frontend/package-lock.json" ]; then
        sed -i.bak -E \
            '/^[[:space:]]*"name": "my-quant-lab-web",$/{n;s/^([[:space:]]*"version": ")[^"]*(")/\1'"${version}"'\2/;}' \
            "${REPO_ROOT}/frontend/package-lock.json" \
            && rm -f "${REPO_ROOT}/frontend/package-lock.json.bak"
    fi
}

cmd_show() {
    echo "current version: $(current_version)"
}

cmd_set() {
    local version="${1:-}" minor
    if ! printf '%s' "$version" | grep -Eq '^v?[0-9]+\.[0-9]+\.[0-9]$'; then
        echo "usage: $0 set vX.Y.Z" >&2
        echo "  every component counts 0-9 and carries over at 10 (ADR-079), so the" >&2
        echo "  third field is always one digit: after v1.5.9 comes v1.6.0, never v1.5.10." >&2
        exit 2
    fi
    minor="$(printf '%s' "${version#v}" | cut -d. -f2)"
    if [ "$minor" -gt 9 ]; then
        echo "refusing $version: minor versions count 0-9 and carry over at 10" >&2
        echo "  (v1.9.9 → v2.0.0), so a v1.${minor}.x release cannot exist (ADR-079)." >&2
        exit 2
    fi
    write_version "$version"
    sync_env_example "$version"
    sync_version_references "$version"
    echo "version set to $version"
}

cmd_bump() {
    require_git
    local new_version
    new_version="$(bump_version)"
    write_version "$new_version"
    sync_env_example "$new_version"
    sync_version_references "$new_version"

    git -C "$REPO_ROOT" add "$VERSION_FILE" .env.example backend/app/__init__.py \
        backend/pyproject.toml frontend/package.json frontend/package-lock.json 2>/dev/null || true
    git -C "$REPO_ROOT" commit -m "build: release ${new_version}"

    if [ "${1:-}" = "--tag" ]; then
        git -C "$REPO_ROOT" tag -a "$new_version" -m "My Quant Lab ${new_version}"
        echo "tagged ${new_version} — push with: git push origin main && git push origin ${new_version}"
        echo "the release workflow will publish GHCR images and create the GitHub Release."
    fi
    echo "bumped to ${new_version}"
}

cmd_notes() {
    cat <<'EOF'
## Deploy on the NAS

```bash
git clone https://github.com/bobvane/My-Quant-Lab.git
cd My-Quant-Lab
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD and SECRET_KEY
docker compose up -d
```

- Web UI: http://<nas-ip>:8081
- API docs: http://<nas-ip>:8080/docs
- Market data defaults to `synthetic` (deterministic offline data). Set
  `MARKET_DATA_PROVIDER=yahoo_finance` for real quotes.
- AI is optional; quantitative features work with AI disabled.
- No broker integration: this project never places orders.
EOF
}

case "${1:-show}" in
    show) cmd_show ;;
    bump) shift; cmd_bump "${1:-}" ;;
    set) shift; cmd_set "${1:-}" ;;
    notes) cmd_notes ;;
    *)
        echo "usage: $0 {show|bump [--tag]|set vX.Y.Z|notes}" >&2
        exit 2
        ;;
esac
