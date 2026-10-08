"""One container runs four processes, and the launcher is not a supervisor (ADR-190).

Until v2.6.0 the deployment was six containers: a web edge, an API, a worker, a
scheduler, PostgreSQL and Redis. ADR-190 merged the first four into one
``quantlab-app`` image, so nginx, uvicorn, the Celery worker and Celery beat now
share a filesystem, a loopback interface and one lifecycle -- and nothing else.

The merge is only safe while the launcher stays a launcher:

* it starts each child exactly once and exits non-zero when any child dies; the
  restart is compose's job, not a per-child respawn loop with a backoff nobody
  measured;
* a stop signal is handled by the launcher (SIGTERM -> bounded wait -> SIGKILL),
  so no third-party process manager is on the critical path;
* nginx is still the only door the browser can reach; uvicorn listens on the
  container interface for one reason only -- the published API port is forwarded
  to that interface, never to the container's loopback -- and that host port
  stays 127.0.0.1-bound, as the separate api container had it in v2.5.0.

The four retired service names stay retired: no file, no image, no role.

These are text guards held by experience: the pieces only exist inside a
container, and this project has no workstation Docker, so the deployment's own
files are the evidence (the same reason the other deployment guards read text,
ADR-076).
"""

from __future__ import annotations

import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKER = REPO_ROOT / "docker"
ENTRYPOINT = DOCKER / "entrypoint.sh"
DOCKERFILE = DOCKER / "Dockerfile.app"
NGINX_CONF = DOCKER / "app.nginx.conf"
COMPOSE = REPO_ROOT / "docker-compose.yml"
OVERLAY = REPO_ROOT / "docker-compose.build.yml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

RETIRED = ("quantlab-api", "quantlab-web", "quantlab-worker", "quantlab-scheduler")

# The door opens last: a client never reaches a container whose API or static
# files are not up yet. Beat goes first only because its death is the one that
# silences every schedule, and publishing to Redis before a worker connects is
# harmless.
START_ORDER = ("beat", "worker", "api", "nginx")

# The two doors the browser and the diagnostics scripts have used since v2.5.0.
WEB_PORT_MAPPING = "${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8081}:8080"
API_PORT_MAPPING = "${API_BIND:-127.0.0.1}:${API_PORT:-8080}:8000"


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _services(path: pathlib.Path) -> dict:
    return yaml.safe_load(_text(path))["services"]


def _function(text: str, name: str) -> str:
    """Return one shell function, header to closing brace, without its neighbours."""

    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start)
    return text[start : end + 2]


def _starts(text: str) -> list[str]:
    return re.findall(r"^    start (\w+) ", text, flags=re.MULTILINE)


def test_the_launcher_starts_the_four_processes_once_each() -> None:
    text = _text(ENTRYPOINT)
    assert _starts(text) == list(START_ORDER), (
        "the launcher no longer starts exactly the four processes the merge promises: "
        f"{_starts(text)}"
    )

    # `celery worker -B` embeds beat in the worker's process: beat's death becomes
    # invisible and a second worker would silently become a second scheduler.
    worker = next(line for line in text.splitlines() if line.startswith("    start worker "))
    assert "-B" not in worker and "--beat" not in worker, (
        "beat must stay a child of its own, never `celery worker -B`"
    )
    assert 'celery -A "$CELERY_APP" beat' in text, "nothing starts `celery beat`"


def test_the_launcher_owns_the_container_lifecycle() -> None:
    text = _text(ENTRYPOINT)
    main = _function(text, "main_app")
    assert "trap on_signal TERM INT" in main, "a stop signal during startup would kill PID 1"
    assert "wait -n || status=$?" in main, "the launcher does not notice a dead child"
    assert 'on_child_exit "$status"' in main

    signalled = _function(text, "on_signal")
    assert "terminate_children" in signalled
    assert "exit 0" in signalled, "an operator's stop must not look like a crash"

    assert "kill -TERM" in _function(text, "terminate_children")
    assert "kill -KILL" in _function(text, "kill_children")
    assert "kill_children" in _function(text, "wait_for_children"), (
        "the launcher must have a bound; a child that ignores SIGTERM cannot hold "
        "`docker stop` open forever"
    )

    exited = _function(text, "on_child_exit")
    assert "terminate_children" in exited
    assert "exit 1" in exited, "a dead child must stop the App, not be replaced in place"

    # A supervisor inside the image would be the thing this merge was told not to
    # build. Prose may name them (the launcher explains what it is *not*); a line
    # that starts one is what this refuses.
    for manager in ("supervisord", "s6-svscan", "circus", "pm2"):
        assert not any(line.strip().startswith(manager) for line in text.splitlines()), (
            f"{manager} would be a second supervisor"
        )

    install = next(line for line in _text(DOCKERFILE).splitlines() if "apt-get install" in line)
    for manager in ("supervisor", "circus", "pm2", "s6"):
        assert manager not in install, f"{manager} must not be installed into the image"


def test_the_whole_app_is_restarted_by_compose_not_by_a_respawn_loop() -> None:
    app = _services(COMPOSE)["quantlab-app"]
    assert app["init"] is True, "no PID-1 reaper for the children's orphans"
    assert app["restart"] == "unless-stopped", "compose must be the one that restarts the App"
    assert app["stop_grace_period"] == "60s"

    grace = int(
        re.search(
            r'SHUTDOWN_GRACE_SECONDS="\$\{SHUTDOWN_GRACE_SECONDS:-(\d+)\}"',
            _text(ENTRYPOINT),
        ).group(1)
    )
    assert grace < 60, (
        "the launcher's own bound has to stay below stop_grace_period, or Docker "
        "escalates to SIGKILL while we are still waiting politely"
    )


def test_the_single_process_roles_are_gone() -> None:
    text = _text(ENTRYPOINT)
    case = text[text.index('case "$ROLE" in') :]
    roles = set(re.findall(r"^    ([a-z]+)\)$", case, flags=re.MULTILINE))
    assert roles == {"app", "migrate", "shell"}, (
        "starting the API, the worker or the scheduler as its own container is not a "
        f"runtime option any more: {sorted(roles)}"
    )


def test_the_image_carries_both_halves_and_runs_unprivileged() -> None:
    text = _text(DOCKERFILE)
    assert "FROM node:22-alpine AS web-build" in text, "the UI is no longer built into the image"
    assert "npm ci" in text and "npm run build" in text
    assert "COPY --from=web-build /app/dist /usr/share/nginx/html" in text
    assert "COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh" in text
    assert "COPY docker/app.nginx.conf /etc/nginx/quantlab-app.conf.template" in text
    assert "USER quantlab" in text and "--uid 10001" in text
    assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in text

    # ADR-100: the probe belongs to the deployment, and it asks about all four
    # children -- a question this image cannot answer about itself. (The file says
    # so in prose, so this looks for the instruction, not the word.)
    assert not re.search(r"^HEALTHCHECK", text, flags=re.MULTILINE)

    install = next(line for line in text.splitlines() if "apt-get install" in line)
    assert "nginx" in install and "curl" in install
    assert "gettext" not in install, "the renderer is three lines of Python, not envsubst"


def test_nginx_is_still_the_only_door_the_browser_can_reach() -> None:
    conf = _text(NGINX_CONF)
    assert "listen 8080;" in conf, "uid 10001 cannot bind 80, and does not try to"
    assert "proxy_pass http://127.0.0.1:8000/api/;" in conf, "the API left the loopback"
    assert conf.count("            ${AUTH_LINE}") == 3, (
        "/api/, /docs and /openapi.json share one token rule"
    )
    assert 'return 200 "ok\\n";' in conf, "the static liveness answer changed shape"

    for line in conf.splitlines():
        if "tmp_path" in line:
            assert "/tmp/" in line, f"an unprivileged nginx cannot write here: {line.strip()}"
    assert "pid /tmp/nginx.pid;" in conf

    launcher = _text(ENTRYPOINT)
    assert 'nginx -c "$NGINX_RENDERED_CONF"' in launcher, "nginx does not read the rendered file"
    assert '.replace("${AUTH_LINE}", os.environ.get("AUTH_LINE", ""))' in launcher


def test_the_deployment_keeps_the_same_two_doors_as_v2_5() -> None:
    services = _services(COMPOSE)
    assert set(services) == {"quantlab-postgres", "quantlab-redis", "quantlab-app"}, (
        f"the deployment is three containers: {sorted(services)}"
    )

    app = services["quantlab-app"]
    assert app["ports"] == [WEB_PORT_MAPPING, API_PORT_MAPPING]
    assert set(app["networks"]) == {"backend"}
    assert app["volumes"] == ["celery_beat:/app/beat"], (
        "beat's schedule file has to survive an image upgrade, or the staleness "
        "probe reads 'missing' until the first dispatch"
    )
    for name, dependency in app["depends_on"].items():
        assert dependency["condition"] == "service_healthy", (
            f"{name} is waited for by existence, not by readiness"
        )
    assert set(_services(COMPOSE)) == set(services)


def test_the_published_api_port_can_actually_reach_the_api() -> None:
    """A published port reaches the container's interface, never its loopback.

    The first v2.6.0 CI run failed exactly here: the container was healthy,
    nginx answered, and ``http://127.0.0.1:${API_PORT}/api/v1/healthz`` timed out
    from the host because uvicorn was bound to the container's loopback. The two
    files have to agree, so this guard reads both.
    """

    app = _services(COMPOSE)["quantlab-app"]
    assert API_PORT_MAPPING in app["ports"], "the API door is gone from the deployment"

    launcher = _text(ENTRYPOINT)
    api = launcher[launcher.index("start api uvicorn") : launcher.index("start nginx")]
    assert "--host 0.0.0.0 --port 8000" in api, (
        f"a loopback-bound API makes the published port a dead door: {' '.join(api.split())}"
    )

    # The browser's door still goes through the container loopback, and that is
    # the only place the API is addressed by name.
    assert "proxy_pass http://127.0.0.1:8000" in _text(NGINX_CONF)


def test_the_process_check_cannot_match_its_own_command_line() -> None:
    """The CI process check must read a filtered listing, not raw ``ps`` output.

    ``ps -eo args=`` prints the shell that is running the check too, and that
    shell's command line carries every needle the check looks for. Matching the
    raw listing therefore let the step pass by matching itself (it could never
    fail), while the ``worker -B`` probe failed on its own text -- both on the
    first run of that step. The listing is filtered to the shapes a real child
    has, and every needle is matched against that filtered file.
    """

    ci = _text(CI)
    step = re.search(
        r"- name: Container process assertions \(ADR-190\)\n(?P<body>.*?)(?=\n      - name: )",
        ci,
        re.S,
    )
    assert step is not None, "the container process assertions step is gone"
    body = step.group("body")

    assert "/tmp/ps.txt > /tmp/ps.kids" in body, "the raw listing is matched unfiltered"
    assert '-e "^nginx:"' in body and '-e "^/usr/local/bin/python"' in body
    for needle in (
        'grep -qF -- "$needle" /tmp/ps.kids',
        'grep -qF -- "/bin/bash /usr/local/bin/entrypoint.sh" /tmp/ps.kids',
        'grep -qF -- "worker -B" /tmp/ps.kids',
    ):
        assert needle in body, f"the process check no longer reads the filtered listing: {needle}"
    assert not re.search(r"grep -qF -- (?:\"\$needle\"|.*worker -B).*?/tmp/ps\.txt", body), (
        "a needle is matched against the raw listing again"
    )


def test_the_retired_services_leave_nothing_behind() -> None:
    for path in (ENTRYPOINT, DOCKERFILE, NGINX_CONF, COMPOSE, OVERLAY):
        text = _text(path)
        for name in RETIRED:
            assert name not in text, f"{path.name} still names the retired {name}"

    for gone in ("Dockerfile.backend", "Dockerfile.web", "web.nginx.conf", "web-entrypoint.sh"):
        assert not (DOCKER / gone).exists(), f"{gone} is a second way to build the old stack"

    overlay = _services(OVERLAY)
    assert set(overlay) == {"quantlab-app"}
    assert set(overlay) <= set(_services(COMPOSE))
    assert overlay["quantlab-app"]["build"]["dockerfile"] == "docker/Dockerfile.app"
