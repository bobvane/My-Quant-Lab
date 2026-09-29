#!/usr/bin/env bash
# My Quant Lab — version and release helper.
#
# Version scheme (project decision): v0.0.1 → … → v0.0.9 → v0.0.10 → … →
# v0.1.0 → … Each component counts 0-9 and carries over at 10.
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
    # Keep .env.example aligned with the released tag so NAS deploys pull the
    # matching image.
    local version="$1" version="${1#v}"
    if [ -f "${REPO_ROOT}/.env.example" ]; then
        sed -i.bak "s/^MQL_VERSION=.*/MQL_VERSION=${version}/" "${REPO_ROOT}/.env.example" \
            && rm -f "${REPO_ROOT}/.env.example.bak"
    fi
}

sync_version_references() {
    # version.txt is the single source of truth; mirror it into the backend
    # package so /api/v1/health reports the released version.
    local version="$1"
    sed -i.bak "s/^__version__ = \".*\"/__version__ = \"${version}\"/" \
        "${REPO_ROOT}/backend/app/__init__.py" 2>/dev/null && rm -f "${REPO_ROOT}/backend/app/__init__.py.bak"
    sed -i.bak "s/^version = \".*\"/version = \"${version}\"/" \
        "${REPO_ROOT}/backend/pyproject.toml" 2>/dev/null && rm -f "${REPO_ROOT}/backend/pyproject.toml.bak"
    sed -i.bak "s/^version: \".*\"/version: \"${version}\"/" \
        "${REPO_ROOT}/frontend/package.json" 2>/dev/null && rm -f "${REPO_ROOT}/frontend/package.json.bak"
}

cmd_show() {
    echo "current version: $(current_version)"
}

cmd_set() {
    local version="${1:-}"
    if ! printf '%s' "$version" | grep -Eq '^v?[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo "usage: $0 set vX.Y.Z" >&2
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
        backend/pyproject.toml frontend/package.json 2>/dev/null || true
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
