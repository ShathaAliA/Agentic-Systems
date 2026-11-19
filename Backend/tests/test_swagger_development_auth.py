"""Real HTTP authentication against synthetic in-memory data; no external DB."""
from datetime import timedelta
import secrets

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.auth.models import DevelopmentCredential, DevelopmentSession
from app.auth.service import digest
from app.main import create_app
from app.models import User, utc_now
from test_authentication import auth, auth_test_app


def form_login(client, password, username="employee@example.invalid", **kwargs):
    return client.post("/auth/dev/token", data={"username": username, "password": password}, **kwargs)


def test_swagger_advertises_relative_development_flow_without_persisting_tokens():
    app = create_app(auth_settings=AuthSettings(mode="disabled", app_env="test"))
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
        scheme = spec["components"]["securitySchemes"]["DevelopmentSession"]
        assert scheme["type"] == "oauth2"
        assert scheme["flows"]["password"]["tokenUrl"] == "auth/dev/token"
        assert spec["paths"]["/auth/me"]["get"]["security"] == [{"DevelopmentSession": []}]
        docs = client.get("/docs")
        assert docs.status_code == 200
        assert '"persistAuthorization": true' not in docs.text


def test_form_session_is_opaque_and_logout_revokes_only_that_session(auth):
    client, engine, password = auth
    form = form_login(client, password)
    assert form.status_code == 200
    assert form.headers["cache-control"] == "no-store"
    token = form.json()["access_token"]
    assert form.json()["production_ready"] is False
    assert form.json()["authentication_provider"] == "development_password"
    headers = {"Authorization": "Bearer " + token, "X-User-ID": "admin"}
    assert client.get("/auth/me", headers=headers).json()["id"] == "employee"
    with Session(engine) as session:
        assert session.scalar(select(DevelopmentSession.token_digest)) == digest(token)
    other = client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": password})
    assert other.status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + other.json()["access_token"]}).status_code == 200


@pytest.mark.parametrize("state", ["expired", "revoked", "inactive", "unknown", "rotated"])
def test_form_session_rechecks_identity_and_expiry(auth, state):
    client, engine, password = auth
    response = form_login(client, password)
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    with Session(engine) as session:
        record = session.scalar(select(DevelopmentSession))
        if state == "expired": record.expires_at = utc_now() - timedelta(seconds=1)
        if state == "revoked": record.revoked_at = utc_now()
        if state == "inactive": session.get(User, "employee").status = "inactive"
        if state == "unknown": session.delete(session.get(User, "employee"))
        if state == "rotated": session.get(DevelopmentCredential, "employee").version = "rotated"
        session.commit()
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_form_and_json_login_share_rate_limit_across_app_instances(auth):
    client, engine, password = auth
    wrong = secrets.token_urlsafe(24)
    for _ in range(4):
        assert client.post("/auth/dev/login", json={"email": "employee@example.invalid", "password": wrong}).status_code == 401
    assert form_login(client, wrong).status_code == 401
    with TestClient(auth_test_app(engine)) as other:
        response = form_login(other, password, headers={"X-Forwarded-For": "192.0.2.12"})
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
        assert response.headers["cache-control"] == "no-store"


def test_form_identity_errors_are_indistinguishable(auth):
    client, engine, password = auth
    with Session(engine) as session:
        session.get(User, "outsider").status = "inactive"
        session.commit()
    responses = [form_login(client, candidate, username) for username, candidate in (
        ("absent@example.invalid", password), ("outsider@example.invalid", password),
        ("employee@example.invalid", secrets.token_urlsafe(24)))]
    assert all(r.status_code == 401 for r in responses)
    assert responses[0].json() == responses[1].json() == responses[2].json()


def test_form_token_cannot_expand_department_or_global_permissions(auth):
    client, _, password = auth
    token = form_login(client, password).json()["access_token"]
    headers = {"Authorization": "Bearer " + token, "X-User-ID": "admin", "X-Role": "Admin"}
    assert client.get("/test/departments/hr", headers=headers).status_code == 200
    for path in ("/test/departments/finance", "/test/departments/legal", "/test/global"):
        assert client.get(path, headers=headers).status_code == 403


@pytest.mark.parametrize("data", [
    {"username": "x"}, {"username": "x", "password": "secret-canary"},
    {"username": "employee@example.invalid", "password": "secret-canary" * 20},
    {"username": "employee@example.invalid", "password": "secret-canary", "grant_type": "client_credentials"},
])
def test_invalid_forms_do_not_echo_credentials(auth, data):
    client, _, _ = auth
    response = client.post("/auth/dev/token", data=data)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request data"}
    assert "secret-canary" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_form_identity_claims_do_not_select_a_user(auth):
    client, _, password = auth
    response = client.post("/auth/dev/token", data={"username": "employee@example.invalid", "password": password,
                                                   "user_id": "admin", "role": "Admin", "scope": "users:manage"})
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.get("/auth/me", headers=headers).json()["id"] == "employee"
    assert client.get("/test/global", headers=headers).status_code == 403


def test_disabled_form_login_does_not_connect_to_database():
    app = create_app(auth_settings=AuthSettings(mode="disabled", app_env="production"))
    with TestClient(app) as client:
        response = form_login(client, secrets.token_urlsafe(24))
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
