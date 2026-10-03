"""A documented boundary must be the boundary the default deployment has.

`.env.example` and `README.md` used to tell the operator that the API is bound to
loopback and reached only through the web container, so "局域网无法直连" — a
boundary nobody delivered.  The web container publishes `${WEB_BIND:-0.0.0.0}:8081`
on every interface and proxies `/api/` through nginx (`docker/web.nginx.conf:38-49`),
and `docker/web-entrypoint.sh:10-15` injects the bearer token into that proxy when
one is configured.  So a default install answers `/api/v1/*` to anyone who can
reach port 8081, token or no token; `API_BIND=127.0.0.1` closes port 8080, not the
deployment.  What the operator needs is the truth plus the one setting that really
closes it (`WEB_BIND=127.0.0.1`), and that is what these guards hold in place.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
README = REPO_ROOT / "README.md"
NGINX = REPO_ROOT / "docker" / "web.nginx.conf"

# The exact sentence that claimed a boundary the composition below does not have.
FALSE_CLAIMS = ("局域网无法直连", "局域网无法无认证访问", "避免无认证暴露")


def test_the_env_example_does_not_promise_the_missing_boundary() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for claim in FALSE_CLAIMS:
        assert claim not in text, f".env.example still claims '{claim}'"


def test_the_ports_section_names_who_can_reach_the_web_port() -> None:
    """The exposure has to be said where the port is chosen, not in a footnote."""

    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    section = text[text.index("# --- 端口") : text.index("# --- API 鉴权")]
    assert "局域网开放" in section, (
        "the port section no longer says the web port is the LAN-open one"
    )
    assert "8081" in section, "the port section no longer names the exposed port"
    assert "127.0.0.1" in section, "the port section no longer says how to close it"


def test_the_token_section_says_what_the_token_does_not_cover() -> None:
    """A token that the proxy injects cannot protect the proxy's own clients."""

    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    section = text[text.index("# --- API 鉴权") : text.index("RATE_LIMIT_PER_MINUTE")]
    assert "绕过 Web 容器" in section, (
        "the token section no longer says which clients the token actually stops"
    )
    assert "8081" in section, "the token section no longer says the proxied path still works"


def test_the_readme_security_section_tells_the_same_story() -> None:
    text = README.read_text(encoding="utf-8")
    section = text[text.index("## 安全与暴露面") : text.index("## 可选：从源码构建")]
    for claim in FALSE_CLAIMS:
        assert claim not in section, f"README still claims '{claim}'"
    assert "局域网" in section and "8081" in section, (
        "the README security section no longer explains that the web port is the exposed one"
    )
    assert "WEB_BIND" in section, "the README no longer says how to close the deployment"


def test_the_settings_comment_does_not_sell_the_token_as_a_lan_boundary() -> None:
    """The third copy of the old claim lived next to the setting itself."""

    config = (REPO_ROOT / "backend" / "app" / "core" / "config.py").read_text(encoding="utf-8")
    start = config.index("api_auth_token: str | None = None")
    comment = config[max(0, start - 800) : start]
    assert "safe only because" not in comment, (
        "config.py still says an empty token is safe because of the loopback bind"
    )
    assert "WEB_BIND" in comment, (
        "config.py no longer names the setting that actually closes the deployment"
    )


def test_the_composition_that_makes_the_old_claim_false_is_still_there() -> None:
    """If this changes, the sentences above stop being true — read them again."""

    nginx = NGINX.read_text(encoding="utf-8")
    assert "proxy_pass http://quantlab-api:8080/api/;" in nginx
    assert "${AUTH_LINE}" in nginx
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${WEB_BIND:-0.0.0.0}" in compose, "the web port no longer defaults to every interface"
