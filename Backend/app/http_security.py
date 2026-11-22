"""HTTP surface hardening: security headers, trusted hosts, reverse-proxy trust, HTTPS posture,
CORS and request-body limits. Pure ASGI/Starlette; no identity, database or document imports.

Design rules
- Fail closed without breaking start-up: missing settings never open anything. Outside development/test
  only loopback hosts are accepted until ALLOWED_HOSTS names the public host, API docs stay off, and
  forwarded headers are ignored until TRUSTED_PROXY_IPS lists the exact reverse-proxy addresses.
- CORS is off by default (same-origin web app behind one reverse proxy). Wildcard origins are refused.
- The reverse proxy remains the first line for TLS, request rate limits and body size; this layer is the
  second line and never trusts a forwarded header from a peer that is not a listed proxy.
"""
import ipaddress
import os
import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEV_ENVIRONMENTS = {"development", "test"}
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")
HOST_PATTERN = re.compile(r"^(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
ORIGIN_PATTERN = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$")

MIB = 1024 * 1024
DEFAULT_BODY_LIMIT = MIB
# 20 MiB file + multipart framing; revisions may carry up to 1,000,000 characters (4 bytes each in UTF-8).
BODY_RULES = (
    (re.compile(r"/api/documents/upload"), 21 * MIB),
    (re.compile(r"/api/documents/[0-9]+/version"), 21 * MIB),
    (re.compile(r"/api/documents/[0-9]+/revise"), 5 * MIB),
)


def _tokens(value):
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


def _flag(value):
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class SecuritySettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    app_env: str = "unspecified"
    allowed_hosts: tuple[str, ...] = ()
    cors_origins: tuple[str, ...] = ()
    https_required: bool = False
    trusted_proxies: tuple[str, ...] = ()
    enable_docs: bool | None = None  # None: only in development/test

    @field_validator("allowed_hosts")
    @classmethod
    def valid_hosts(cls, hosts):
        for host in hosts:
            if host in {"*"} or not HOST_PATTERN.fullmatch(host):
                raise ValueError("ALLOWED_HOSTS needs exact host names (optionally *.example.com); no bare wildcard")
        return hosts

    @field_validator("trusted_proxies")
    @classmethod
    def valid_proxies(cls, values):
        for value in values:
            ipaddress.ip_network(value, strict=False)  # ValueError on anything that is not an IP/CIDR
        return values

    @model_validator(mode="after")
    def valid_origins(self):
        for origin in self.cors_origins:
            if not ORIGIN_PATTERN.fullmatch(origin):
                raise ValueError("CORS_ALLOWED_ORIGINS needs exact origins (scheme://host[:port]); wildcards are refused")
            if not self.development and not origin.startswith("https://"):
                raise ValueError("CORS origins must use https outside development/test")
        return self

    @property
    def development(self):
        return self.app_env in DEV_ENVIRONMENTS

    @property
    def docs_enabled(self):
        return self.development if self.enable_docs is None else self.enable_docs

    @property
    def effective_hosts(self):
        extra = ("testserver",) if self.development else ()  # the in-process test client's host; never in production
        return tuple(dict.fromkeys(LOOPBACK_HOSTS + extra + self.allowed_hosts))

    @classmethod
    def from_environment(cls, app_env, environ=None):
        env = os.environ if environ is None else environ
        docs = env.get("ENABLE_API_DOCS")
        return cls(app_env=app_env, allowed_hosts=_tokens(env.get("ALLOWED_HOSTS")),
                   cors_origins=_tokens(env.get("CORS_ALLOWED_ORIGINS")),
                   https_required=_flag(env.get("HTTPS_REQUIRED")),
                   trusted_proxies=_tokens(env.get("TRUSTED_PROXY_IPS")),
                   enable_docs=None if docs is None else _flag(docs))


# ---------------------------------------------------------------- reverse-proxy trust

class TrustedProxyMiddleware:
    """Honor X-Forwarded-For/-Proto only when the direct peer is a listed proxy.

    The client becomes the first non-proxy address walking X-Forwarded-For from the right; anything
    unparsable means the header is ignored. Mutates the shared scope so outer middleware see the result.
    """

    def __init__(self, app: ASGIApp, trusted):
        self.app = app
        self.networks = [ipaddress.ip_network(item, strict=False) for item in trusted]

    def _trusted(self, address):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in network for network in self.networks)

    def _client(self, peer, forwarded):
        entries = [item.strip() for item in forwarded.split(",") if item.strip()]
        for entry in reversed(entries):
            try:
                ipaddress.ip_address(entry)
            except ValueError:
                return peer
            if not self._trusted(entry):
                return entry
        return entries[0] if entries else peer

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        client = scope.get("client")
        if scope["type"] == "http" and self.networks and client and self._trusted(client[0]):
            headers = scope["headers"]
            forwarded = b",".join(v for k, v in headers if k == b"x-forwarded-for").decode("latin-1")
            scope["client"] = (self._client(client[0], forwarded), 0)
            protos = [p.strip().lower() for v in (v for k, v in headers if k == b"x-forwarded-proto")
                      for p in v.decode("latin-1").split(",") if p.strip()]
            if protos and protos[-1] in {"http", "https"}:
                scope["scheme"] = protos[-1]
        await self.app(scope, receive, send)


# ---------------------------------------------------------------- HTTPS posture

class HTTPSRequiredMiddleware:
    """Defense in depth behind a TLS-terminating proxy: refuse requests whose effective scheme is http.

    A loopback peer may reach /health (container probe). GET/HEAD are redirected, others get 400.
    """

    def __init__(self, app: ASGIApp, enabled: bool):
        self.app, self.enabled = app, enabled

    @staticmethod
    def _loopback(scope):
        client = scope.get("client")
        try:
            return bool(client) and ipaddress.ip_address(client[0]).is_loopback
        except ValueError:
            return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (not self.enabled or scope["type"] != "http" or scope.get("scheme") == "https"
                or (scope.get("path") == "/health" and self._loopback(scope))):
            await self.app(scope, receive, send)
            return
        if scope["method"] in {"GET", "HEAD"}:
            host = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"host"), "")
            host = re.sub(r":[0-9]+$", "", host) if not host.startswith("[") else host.split("]")[0] + "]"
            query = scope.get("query_string", b"").decode("latin-1")
            target = "https://" + host + scope.get("path", "/") + ("?" + query if query else "")
            response = RedirectResponse(target, status_code=308)
        else:
            response = JSONResponse({"detail": "HTTPS required"}, status_code=400)
        await response(scope, receive, send)


# ---------------------------------------------------------------- response headers

STATIC_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
)
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
            "frame-ancestors 'none'; base-uri 'none'")
HSTS = "max-age=31536000; includeSubDomains"
DOCS_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}


class SecurityHeadersMiddleware:
    """Outermost layer: every response (including errors raised by inner layers) carries these headers.
    Existing values from inner code are never overwritten. HSTS is sent only over effective HTTPS."""

    def __init__(self, app: ASGIApp, docs_enabled: bool):
        self.app, self.docs_enabled = app, docs_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = self.docs_enabled and scope.get("path") in DOCS_PATHS

        async def hardened_send(message: Message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                wanted = list(STATIC_HEADERS) + [("Content-Security-Policy", DOCS_CSP if docs else API_CSP)]
                if scope.get("scheme") == "https":
                    wanted.append(("Strict-Transport-Security", HSTS))
                for name, value in wanted:
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, hardened_send)


# ---------------------------------------------------------------- request body limits

class BodySizeLimitMiddleware:
    """Reject oversized bodies before any parser runs (declared length) and while streaming (chunked or lying
    clients). The 413 is a Starlette HTTPException so FastAPI's body parsing re-raises it unchanged."""

    def __init__(self, app: ASGIApp, rules=BODY_RULES, default=DEFAULT_BODY_LIMIT):
        self.app, self.rules, self.default = app, rules, default

    def _limit(self, scope):
        path = scope.get("path", "")
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root):]
        for pattern, limit in self.rules:
            if pattern.fullmatch(path):
                return limit
        return self.default

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope)
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = None
            if size is not None and size > limit:
                await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status_code=413, detail="Request body too large")
            return message

        await self.app(scope, limited_receive, send)


# ---------------------------------------------------------------- installation

def install_inner(app, settings: SecuritySettings):
    """Innermost layer. Call BEFORE adding ResponseSecurityMiddleware."""
    app.add_middleware(BodySizeLimitMiddleware)


def install_outer(app, settings: SecuritySettings):
    """Outer layers, added last so they wrap the response policy. Order outer to inner: security headers,
    trusted proxy, trusted host, HTTPS posture, CORS (only when origins are configured)."""
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"], max_age=600)
    app.add_middleware(HTTPSRequiredMiddleware, enabled=settings.https_required)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.effective_hosts))
    app.add_middleware(TrustedProxyMiddleware, trusted=settings.trusted_proxies)
    app.add_middleware(SecurityHeadersMiddleware, docs_enabled=settings.docs_enabled)
