"""Administrator gate for dangerous operations (ADR-218).

``ADMIN_TOKEN`` is a *second*, deliberately separate credential. The bundled
nginx injects ``API_AUTH_TOKEN`` into every ``/api``, ``/docs`` and
``/openapi.json`` request (``docker/app.nginx.conf``'s ``${AUTH_LINE}``), so any
browser that can reach ``:8081`` already presents that token: it tells "this
client goes through our proxy" apart from "this client called the API directly",
and it cannot tell an administrator from a reader (ADR-097).

The admin token is never injected by nginx, never read from a query string, a
cookie, a request body or front-end storage. The only carrier is the
``X-Admin-Token`` request header. It travels in clear text over plain HTTP, so
this gate does not claim to stop sniffing on the LAN; what it stops is a reader
of the web UI performing a destructive operation.

"Not configured" is a refusal, never a fallback: with ``ADMIN_TOKEN`` empty every
admin-only endpoint answers 503 ``admin_token_not_configured`` rather than
treating ``API_AUTH_TOKEN`` -- or the absence of both -- as administrator rights.

V-B0 ships the gate and its configuration only. The endpoints that will declare
it are the maintenance ones (V-B2) and export/restore (V-B3); they attach
``dependencies=[Depends(require_admin_token)]``.
"""

from __future__ import annotations

import logging
import secrets
from typing import Final

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

#: The only carrier we accept. A query string would end up in the container's
#: nginx access log (``docker/app.nginx.conf`` logs ``$request``), and a cookie
#: or a body field would be replayed by anything that can see the browser.
ADMIN_TOKEN_HEADER: Final = "X-Admin-Token"

CODE_NOT_CONFIGURED: Final = "admin_token_not_configured"
CODE_INVALID: Final = "admin_token_invalid"

MESSAGE_NOT_CONFIGURED: Final = (
    "管理员令牌未配置（ADMIN_TOKEN 为空），危险操作已停用："
    "请在 .env 里设置 ADMIN_TOKEN（至少 8 位），再重启容器。"
)
MESSAGE_INVALID: Final = "管理员令牌缺失或不正确。"


class AdminGateError(Exception):
    """A refusal from the administrator gate, carrying its own HTTP answer."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}

    def content(self) -> dict[str, object]:
        """The same envelope as the API auth middleware and the compile errors."""

        return {"error": {"code": self.code, "message": self.message, "details": {}}}


def require_admin_token(request: Request) -> None:
    """FastAPI dependency for admin-only endpoints.

    Reads ``settings.admin_token`` on every call (not at import time), so a test
    -- or an operator restarting with a new ``.env`` -- cannot be served by a
    value cached at startup.
    """

    configured = settings.admin_token
    if not configured:
        logger.warning(
            "admin operation refused on %s: ADMIN_TOKEN is not configured",
            request.url.path,
        )
        raise AdminGateError(503, CODE_NOT_CONFIGURED, MESSAGE_NOT_CONFIGURED)

    presented = request.headers.get(ADMIN_TOKEN_HEADER, "").strip()
    if not presented or not secrets.compare_digest(presented, configured):
        # The value is never logged: only the fact that it did not match.
        logger.warning(
            "admin operation refused on %s: missing or invalid %s",
            request.url.path,
            ADMIN_TOKEN_HEADER,
        )
        raise AdminGateError(
            401,
            CODE_INVALID,
            MESSAGE_INVALID,
            headers={"WWW-Authenticate": ADMIN_TOKEN_HEADER},
        )


async def admin_gate_error_handler(request: Request, exc: AdminGateError) -> JSONResponse:
    """Render a gate refusal; registered on the app in ``app.api.main``.

    Kept next to the gate so anything that mounts ``require_admin_token`` on its
    own app renders the identical shape.
    """

    return JSONResponse(
        status_code=exc.status_code,
        content=exc.content(),
        headers=exc.headers,
    )
