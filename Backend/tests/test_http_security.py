"""HTTP surface hardening: headers, host/proxy trust, HTTPS posture, CORS, docs gating, body limits.
Synthetic probes only; no database, provider or document dependency."""
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.auth.config import AuthSettings
from app.http_security import (API_CSP, DOCS_CSP, MIB, STATIC_HEADERS, SecuritySettings, install_inner,
                               install_outer)
from app.main import create_app
from app.response_security import ResponseSecurityMiddleware

PROXY = ("10.0.0.0/24",)


def settings(**changes):
    return SecuritySettings(**{"app_env": "test", **changes})


def probe_app(config):
    """Minimal app wired exactly like create_app: inner limits, response policy, outer layers."""
    app = FastAPI(docs_url="/docs" if config.docs_enabled else None,
                  openapi_url="/openapi.json" if config.docs_enabled else None)
    calls = {"count": 0}

    @app.get("/echo")
    def echo(request: Request):
        return {"client": request.client.host, "scheme": request.url.scheme}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/documents/probe")
    async def probe(request: Request):
        calls["count"] += 1
        return {"bytes": len(await request.body())}

    @app.post("/api/documents/upload")
    async def upload(file: UploadFile = File(...)):
        calls["count"] += 1
        return {"bytes": len(await file.read())}

    @app.post("/api/documents/{document_id}/revise")
    async def revise(document_id: int, request: Request):
        calls["count"] += 1
        return {"bytes": len(await request.body())}

    @app.get("/boom")
    def boom():
        raise RuntimeError("synthetic")

    install_inner(app, config)
    app.add_middleware(ResponseSecurityMiddleware)
    install_outer(app, config)
    app.state.calls = calls
    return app


def assert_hardened(response, *, hsts=False, docs=False):
    for name, value in STATIC_HEADERS:
        assert response.headers[name] == value, name
    assert response.headers["content-security-policy"] == (DOCS_CSP if docs else API_CSP)
    assert ("strict-transport-security" in response.headers) is hsts


# ------------------------------------------------------------------ settings

def test_environment_parsing_and_fail_closed_defaults():
    env = {"ALLOWED_HOSTS": "portal.example.com, *.example.org", "CORS_ALLOWED_ORIGINS": "https://portal.example.com",
           "HTTPS_REQUIRED": "true", "TRUSTED_PROXY_IPS": "10.0.0.5,10.1.0.0/16", "ENABLE_API_DOCS": "0"}
    parsed = SecuritySettings.from_environment("production", env)
    assert parsed.allowed_hosts == ("portal.example.com", "*.example.org") and parsed.https_required
    assert parsed.trusted_proxies == ("10.0.0.5", "10.1.0.0/16") and parsed.docs_enabled is False
    empty = SecuritySettings.from_environment("production", {})
    assert empty.effective_hosts == ("localhost", "127.0.0.1", "[::1]")  # loopback only until configured
    assert not empty.cors_origins and not empty.trusted_proxies and not empty.docs_enabled
    assert SecuritySettings.from_environment("unspecified", {}).docs_enabled is False
    assert SecuritySettings.from_environment("test", {}).docs_enabled is True
    assert "testserver" in SecuritySettings.from_environment("test", {}).effective_hosts
    assert "testserver" in SecuritySettings.from_environment("development", {}).effective_hosts
    assert "testserver" not in SecuritySettings.from_environment("production", {}).effective_hosts


@pytest.mark.parametrize("changes", [
    {"allowed_hosts": ("*",)}, {"allowed_hosts": ("https://example.com",)}, {"allowed_hosts": ("exa mple.com",)},
    {"trusted_proxies": ("not-an-ip",)}, {"trusted_proxies": ("10.0.0.0/99",)},
    {"cors_origins": ("*",)}, {"cors_origins": ("https://example.com/path",)}, {"cors_origins": ("https://*.example.com",)},
    {"app_env": "production", "cors_origins": ("http://portal.example.com",)}])
def test_invalid_security_configuration_is_refused(changes):
    with pytest.raises(ValidationError):
        settings(**changes)


# ------------------------------------------------------------------ headers

def test_every_response_class_of_the_real_app_carries_the_security_headers():
    app = create_app(auth_settings=AuthSettings(mode="disabled", app_env="test"))

    @app.get("/boom")
    def boom():
        raise RuntimeError("synthetic")

    with TestClient(app, raise_server_exceptions=False) as client:
        for response in (client.get("/"), client.get("/auth/missing"), client.get("/auth/dev/token"),
                         client.post("/auth/dev/login", json={"nope": 1}), client.get("/boom"),
                         client.get("/", headers={"Host": "evil.example"}), client.options("/")):
            assert_hardened(response)


def test_existing_values_are_never_overwritten_and_sensitive_paths_stay_uncached():
    app = FastAPI()
    from fastapi.responses import JSONResponse

    @app.get("/api/documents/x")
    def custom():
        return JSONResponse({"ok": 1}, headers={"X-Frame-Options": "SAMEORIGIN"})
    install_inner(app, settings())
    app.add_middleware(ResponseSecurityMiddleware)
    install_outer(app, settings())
    with TestClient(app) as client:
        response = client.get("/api/documents/x")
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff" and response.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------ HSTS, hosts, proxies

def test_hsts_only_over_effective_https():
    plain = TestClient(probe_app(settings()))
    assert_hardened(plain.get("/echo"))
    direct = TestClient(probe_app(settings()), base_url="https://testserver")
    assert_hardened(direct.get("/echo"), hsts=True)
    assert direct.get("/echo").headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    via_proxy = TestClient(probe_app(settings(trusted_proxies=PROXY)), client=("10.0.0.5", 5000))
    assert_hardened(via_proxy.get("/echo", headers={"X-Forwarded-Proto": "https"}), hsts=True)
    stranger = TestClient(probe_app(settings(trusted_proxies=PROXY)), client=("203.0.113.7", 5000))
    assert_hardened(stranger.get("/echo", headers={"X-Forwarded-Proto": "https"}))  # spoofed proto ignored


def test_host_header_must_be_listed_and_production_defaults_to_loopback_only():
    app = probe_app(settings(app_env="production", allowed_hosts=("portal.example.com",)))
    with TestClient(app, base_url="http://portal.example.com") as client:
        assert client.get("/echo").status_code == 200
        bad = client.get("/echo", headers={"Host": "evil.example"})
        assert bad.status_code == 400 and bad.text == "Invalid host header"
        assert_hardened(bad)
    bare = probe_app(settings(app_env="production"))
    assert TestClient(bare, base_url="http://127.0.0.1").get("/echo").status_code == 200
    assert TestClient(bare, base_url="http://portal.example.com").get("/echo").status_code == 400


@pytest.mark.parametrize("peer,headers,client,scheme", [
    ("203.0.113.7", {"X-Forwarded-For": "1.2.3.4"}, "203.0.113.7", "http"),                  # untrusted peer: ignored
    ("10.0.0.5", {"X-Forwarded-For": "198.51.100.9"}, "198.51.100.9", "http"),
    ("10.0.0.5", {"X-Forwarded-For": "1.1.1.1, 198.51.100.9"}, "198.51.100.9", "http"),     # client-supplied left entry
    ("10.0.0.5", {"X-Forwarded-For": "198.51.100.9, 10.0.0.9"}, "198.51.100.9", "http"),    # chained trusted proxy
    ("10.0.0.5", {"X-Forwarded-For": "10.0.0.8, 10.0.0.9"}, "10.0.0.8", "http"),            # every hop trusted
    ("10.0.0.5", {"X-Forwarded-For": "garbage"}, "10.0.0.5", "http"),                       # unparsable: ignored
    ("10.0.0.5", {"X-Forwarded-For": "198.51.100.9, junk"}, "10.0.0.5", "http"),
    ("10.0.0.5", {}, "10.0.0.5", "http"),
    ("10.0.0.5", {"X-Forwarded-Proto": "https, http"}, "10.0.0.5", "http"),                 # closest hop wins
    ("10.0.0.5", {"X-Forwarded-Proto": "ftp"}, "10.0.0.5", "http"),
    ("10.0.0.5", {"X-Forwarded-Proto": "HTTPS"}, "10.0.0.5", "https")])
def test_forwarded_headers_are_trusted_only_from_listed_proxies(peer, headers, client, scheme):
    app = probe_app(settings(trusted_proxies=PROXY))
    seen = TestClient(app, client=(peer, 5000)).get("/echo", headers=headers).json()
    assert seen == {"client": client, "scheme": scheme}


def test_without_trusted_proxies_nothing_is_ever_forwarded():
    app = probe_app(settings())
    seen = TestClient(app, client=("10.0.0.5", 5000)).get(
        "/echo", headers={"X-Forwarded-For": "1.2.3.4", "X-Forwarded-Proto": "https"}).json()
    assert seen == {"client": "10.0.0.5", "scheme": "http"}


# ------------------------------------------------------------------ HTTPS posture

def test_https_required_redirects_reads_rejects_writes_and_exempts_loopback_probe():
    app = probe_app(settings(https_required=True, trusted_proxies=PROXY))
    outside = TestClient(app, client=("203.0.113.7", 1), base_url="http://testserver")
    moved = outside.get("/echo?x=1", follow_redirects=False)
    assert moved.status_code == 308 and moved.headers["location"] == "https://testserver/echo?x=1"
    assert_hardened(moved)
    refused = outside.post("/api/documents/probe", content=b"x")
    assert refused.status_code == 400 and refused.json() == {"detail": "HTTPS required"}
    assert app.state.calls["count"] == 0
    assert TestClient(app, client=("127.0.0.1", 1)).get("/health").status_code == 200  # container probe
    assert TestClient(app, client=("127.0.0.1", 1)).get("/echo", follow_redirects=False).status_code == 308
    proxied = TestClient(app, client=("10.0.0.5", 1))
    assert proxied.get("/echo", headers={"X-Forwarded-Proto": "https"}).status_code == 200
    assert proxied.get("/echo", headers={"X-Forwarded-Proto": "http"}, follow_redirects=False).status_code == 308


# ------------------------------------------------------------------ CORS

def test_cors_is_off_by_default_and_exact_when_configured():
    closed = TestClient(probe_app(settings()))
    assert "access-control-allow-origin" not in closed.get("/echo", headers={"Origin": "https://portal.example.com"}).headers
    config = settings(cors_origins=("https://portal.example.com",))
    open_for_one = TestClient(probe_app(config))
    allowed = open_for_one.options("/echo", headers={"Origin": "https://portal.example.com",
                                                      "Access-Control-Request-Method": "GET",
                                                      "Access-Control-Request-Headers": "authorization"})
    assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == "https://portal.example.com"
    assert "access-control-allow-credentials" not in allowed.headers
    other = open_for_one.options("/echo", headers={"Origin": "https://evil.example",
                                                    "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in other.headers
    assert_hardened(other)


# ------------------------------------------------------------------ API documentation

@pytest.mark.parametrize("env,override,expected", [
    ("production", None, 404), ("unspecified", None, 404), ("production", False, 404),
    ("test", None, 200), ("development", None, 200), ("production", True, 200)])
def test_api_docs_are_available_only_in_development_or_when_explicitly_enabled(env, override, expected):
    config = SecuritySettings(app_env=env, enable_docs=override)
    app = create_app(auth_settings=AuthSettings(mode="disabled", app_env=env), security_settings=config)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert client.get(path).status_code == expected, path
        if expected == 200:
            assert_hardened(client.get("/docs"), docs=True)
            assert client.get("/openapi.json").headers["content-security-policy"] == API_CSP


# ------------------------------------------------------------------ request body limits

def test_declared_oversize_is_rejected_before_the_route_runs_and_keeps_response_policy():
    app = probe_app(settings())
    client = TestClient(app)
    big = client.post("/api/documents/probe", content=b"x" * (MIB + 1))
    assert big.status_code == 413 and big.json() == {"detail": "Request body too large"}
    assert big.headers["cache-control"] == "no-store"
    assert_hardened(big)
    assert app.state.calls["count"] == 0
    assert client.post("/api/documents/probe", content=b"x" * MIB).json() == {"bytes": MIB}


def test_streamed_oversize_without_content_length_is_rejected_while_reading():
    app = probe_app(settings())

    def chunks():
        for _ in range(MIB // 65536 + 2):
            yield b"x" * 65536
    response = TestClient(app).post("/api/documents/probe", content=chunks())
    assert response.status_code == 413 and response.json() == {"detail": "Request body too large"}


@pytest.mark.parametrize("path,limit", [("/api/documents/7/revise", 5 * MIB)])
def test_revision_path_has_its_own_ceiling(path, limit):
    app = probe_app(settings())
    client = TestClient(app)
    assert client.post(path, content=b"x" * limit).json() == {"bytes": limit}
    assert client.post(path, content=b"x" * (limit + 1)).status_code == 413


def test_upload_path_accepts_a_20_mib_file_and_refuses_more_than_its_ceiling():
    app = probe_app(settings())
    client = TestClient(app)
    ok = client.post("/api/documents/upload", files={"file": ("a.pdf", b"x" * (20 * MIB), "application/pdf")})
    assert ok.status_code == 200 and ok.json() == {"bytes": 20 * MIB}
    too_big = client.post("/api/documents/upload", files={"file": ("a.pdf", b"x" * (22 * MIB), "application/pdf")})
    assert too_big.status_code == 413
    assert app.state.calls["count"] == 1
