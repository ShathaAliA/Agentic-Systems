"""Synthetic ASGI/HTTP probes; never resolve application database settings."""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import OperationalError

from app.auth.config import AuthSettings
from app.main import create_app
from app.response_security import ResponseSecurityMiddleware, sensitive_path


@pytest.mark.parametrize("prefix", ["/auth", "/admin", "/me", "/departments", "/api/documents", "/health/db"])
@pytest.mark.parametrize("suffix", ["", "/", "/nested"])
def test_sensitive_path_boundaries(prefix, suffix):
    assert sensitive_path({"path": prefix + suffix})
    assert not sensitive_path({"path": prefix + "-public" + suffix})


@pytest.mark.parametrize("path,root", [("/gateway/api/documents/1", "/gateway"),
    ("/api/documents/1", "/gateway"), ("/gateway/auth", "/gateway/"),
    ("/gateway/health/db", "/gateway")])
def test_root_path_can_be_present_or_removed_in_asgi_path(path, root):
    assert sensitive_path({"path": path, "root_path": root})


@pytest.mark.parametrize("status", [200, 201, 204, 400, 401, 403, 404, 405, 409, 422, 429, 500, 503])
def test_sensitive_responses_override_cache_without_losing_security_headers(status):
    app = FastAPI()
    app.add_middleware(ResponseSecurityMiddleware)

    @app.get("/api/documents/probe")
    def probe():
        return JSONResponse({"detail": "synthetic"}, status_code=status, headers={
            "Cache-Control": "public, max-age=3600", "Pragma": "cache",
            "WWW-Authenticate": "Bearer", "Retry-After": "42"})

    with TestClient(app) as client:
        response = client.get("/api/documents/probe?query=synthetic")
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["retry-after"] == "42"


def test_core_missing_document_paths_and_method_errors_are_not_cached():
    app = create_app(auth_settings=AuthSettings(mode="disabled", app_env="test"))
    with TestClient(app) as client:
        for path in ("/api/documents", "/api/documents/", "/api/documents/missing?query=synthetic", "/auth/missing"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.headers["cache-control"] == "no-store"
        response = client.get("/auth/dev/token")
        assert response.status_code == 405
        assert response.headers["cache-control"] == "no-store"
        assert "POST" in response.headers["allow"]


def test_redirects_root_path_and_similar_public_paths():
    app = FastAPI()
    app.add_middleware(ResponseSecurityMiddleware)

    @app.get("/api/documents/")
    def probe():
        return {"ok": True}

    @app.get("/api/documents-public")
    def public():
        return JSONResponse({"ok": True}, headers={"Cache-Control": "public, max-age=60"})

    with TestClient(app, root_path="/gateway") as client:
        redirect = client.get("/gateway/api/documents", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["cache-control"] == "no-store"
        assert redirect.headers["location"].endswith("/gateway/api/documents/")
        assert client.get("/gateway/api/documents/").headers["cache-control"] == "no-store"
        assert client.get("/gateway/api/documents-public").headers["cache-control"] == "public, max-age=60"


@pytest.mark.parametrize("kind", ["unexpected", "database", "validation"])
def test_failure_responses_and_application_logs_do_not_disclose_inputs(caplog, kind):
    sentinel = "sensitive-synthetic-canary"
    app = create_app(auth_settings=AuthSettings(mode="disabled", app_env="test"))

    @app.post("/api/documents/security-probe")
    def probe(required: int):
        if kind == "database":
            raise OperationalError("SELECT secret", {"password": sentinel}, RuntimeError(sentinel))
        raise RuntimeError(sentinel)

    with caplog.at_level(logging.ERROR), TestClient(app) as client:
        value = sentinel if kind == "validation" else "1"
        response = client.post("/api/documents/security-probe?required=" + value,
                               headers={"Authorization": "Bearer " + sentinel}, json={"password": sentinel})
    expected = {"unexpected": (500, "Internal server error"), "database": (503, "Database is unavailable"),
                "validation": (422, "Invalid request data")}[kind]
    assert response.status_code == expected[0]
    assert response.json() == {"detail": expected[1]}
    assert response.headers["cache-control"] == "no-store"
    assert sentinel not in response.text
    records = [r for r in caplog.records if r.name.startswith("app.")]
    assert all(sentinel not in r.getMessage() and r.exc_info is None for r in records)
    if kind == "unexpected":
        assert [r.getMessage() for r in records] == ["Unhandled HTTP application failure"]


def test_unexpected_public_failure_is_generic_and_not_cached():
    app = FastAPI()
    app.add_middleware(ResponseSecurityMiddleware)

    @app.get("/public")
    def probe():
        raise RuntimeError("sensitive-synthetic-canary")

    with TestClient(app) as client:
        response = client.get("/public")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["cache-control"] == "no-store"


def test_started_response_failure_is_not_replaced_or_disclosed(caplog):
    messages = []

    async def broken(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
        raise RuntimeError("sensitive-synthetic-canary")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="HTTP response failed after headers were sent") as error:
        asyncio.run(ResponseSecurityMiddleware(broken)({"type": "http", "path": "/api/documents/1"}, receive, send))
    assert error.value.__suppress_context__ is True
    assert sum(m["type"] == "http.response.start" for m in messages) == 1
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert "sensitive-synthetic-canary" not in caplog.text


def test_non_http_lifespan_is_passed_through():
    seen = []

    async def probe(scope, receive, send):
        seen.append(scope["type"])

    asyncio.run(ResponseSecurityMiddleware(probe)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]
