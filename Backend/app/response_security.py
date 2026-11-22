"""HTTP response policy only: no identity, database or document dependencies."""
import logging

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)
SENSITIVE_ROOTS = ("/auth", "/admin", "/me", "/departments", "/api/documents", "/api/conversations", "/api/handoffs", "/api/notifications", "/api/admin", "/health/db")


def sensitive_path(scope: Scope) -> bool:
    path = scope.get("path", "")
    root = scope.get("root_path", "").rstrip("/")
    if root and (path == root or path.startswith(root + "/")):
        path = path[len(root):]
    return any(path == prefix or path.startswith(prefix + "/") for prefix in SENSITIVE_ROOTS)


class ResponseSecurityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False
        private = sensitive_path(scope)

        async def protected_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if private or message["status"] >= 500:
                    headers = MutableHeaders(scope=message)
                    headers["Cache-Control"] = "no-store"
                    headers["Pragma"] = "no-cache"
            await send(message)

        try:
            await self.app(scope, receive, protected_send)
        except Exception:
            # Do not log exception messages, SQL parameters, URLs, request bodies,
            # headers or traceback locals. A stable event is sufficient here.
            logger.error("Unhandled HTTP application failure")
            if started:
                # Never append an error body to an already-started response.
                # Suppress the original exception chain in server diagnostics.
                raise RuntimeError("HTTP response failed after headers were sent") from None
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
            await response(scope, receive, protected_send)
