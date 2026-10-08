"""The web edge must tell the truth about what it serves (ADR-068).

nginx only exists inside the App container, so the behaviour itself (a missing
asset is a 404, the bundle arrives gzipped, hashed files may be cached forever,
``/`` is revalidated) is verified by the container smoke test in CI. What lives
here are the guards that run with nothing but the repository: they fail in the
normal test suite the moment the config loses a directive that smoke test
depends on, instead of silently serving the wrong thing until someone deploys.

The config is also a template: ``docker/entrypoint.sh`` renders it with three
lines of Python (the image has no gettext-base on purpose), replacing only
``${AUTH_LINE}``, so any other ``${...}`` would be left for nginx to reject as an
unknown variable and break nginx's own ``$uri``/``$host``.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
NGINX_CONF = REPO_ROOT / "docker" / "app.nginx.conf"
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"
FAVICON = REPO_ROOT / "frontend" / "public" / "favicon.svg"


def _conf() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _location(text: str, header: str) -> str:
    """Return the body of one ``location`` block.

    Blocks in this file are flat: the body ends at the first line that is
    exactly ``    }``.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    assert start is not None, f"no {header!r} block in docker/app.nginx.conf"
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() == "}":
            return "\n".join(body)
        body.append(line)
    raise AssertionError(f"{header!r} block is not closed")


def test_a_missing_asset_is_not_answered_with_the_application() -> None:
    assets = _location(_conf(), "location /assets/ {")

    assert "try_files $uri =404;" in assets, (
        "without this, a request for a deleted bundle falls through to the SPA "
        "fallback and the browser parses index.html as JavaScript"
    )


def test_hashed_assets_may_be_cached_forever() -> None:
    assets = _location(_conf(), "location /assets/ {")

    assert "immutable" in assets
    assert "max-age=31536000" in assets


def test_the_application_shell_is_revalidated() -> None:
    root = _location(_conf(), "location / {")

    assert 'Cache-Control "no-cache"' in root, (
        "a cached index.html keeps pointing at the previous deploy's assets"
    )


def test_text_is_compressed_on_the_way_out() -> None:
    text = _conf()

    assert "gzip on;" in text, "nginx defaults to gzip off, so the bundle ships raw"
    assert re.search(r"gzip_types[^;]*application/javascript", text)
    assert re.search(r"gzip_types[^;]*text/css", text)


def test_the_icon_is_answered_without_the_application() -> None:
    text = _conf()

    assert "location = /favicon.ico {" in text
    assert re.search(r"location = /favicon\.ico \{[^}]*return 204;", text, re.S)


def test_the_shell_links_an_icon_that_exists() -> None:
    assert FAVICON.is_file(), "frontend/public/favicon.svg is what the shell links"
    assert 'rel="icon"' in INDEX_HTML.read_text(encoding="utf-8")
    assert "/favicon.svg" in INDEX_HTML.read_text(encoding="utf-8")


def test_only_the_auth_line_is_substituted_into_the_template() -> None:
    placeholders = set(re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", _conf()))

    assert placeholders == {"${AUTH_LINE}"}, (
        "the launcher only substitutes ${AUTH_LINE}; every other ${...} would be "
        "left in place and break nginx's own variables"
    )
