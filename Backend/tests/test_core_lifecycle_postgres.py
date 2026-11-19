"""Cross-module workflows; all users, receipts and departments are synthetic."""
from pathlib import Path
import secrets
import uuid
from unittest.mock import Mock
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.auth.config import AuthSettings
from app.auth.enroll import set_password
from app.credentials import CredentialStore
from app.main import create_app
from app.models import Department, DepartmentDatabase
from app.provisioning.worker import Worker
from app.provisioning.tenant_migrations import UnavailableTenantContract
from app.usage.processor import ProviderReceipt, run_once
from test_authentication import auth_test_app, login_headers
from test_authentication_postgres import isolated_postgres, pytestmark
from test_users import seed_user_admin
from test_usage import POLICY


@pytest.fixture(scope="module")
def lifecycle(isolated_postgres):
    bootstrap, runtime, password = isolated_postgres
    deputy = seed_user_admin(bootstrap)
    statements = [
        "GRANT INSERT ON users,departments,department_databases,department_memberships,role_assignments,role_permissions TO auth_test_api",
        "GRANT UPDATE(status) ON users,department_memberships TO auth_test_api",
        "GRANT DELETE ON role_assignments,role_permissions TO auth_test_api",
        "GRANT UPDATE(archived_at,archived_by) ON departments TO auth_test_api",
        "GRANT UPDATE(claim_token,next_retry_at,verified_contract) ON department_databases TO auth_test_api",
        "GRANT SELECT ON department_databases TO auth_test_api",
        "GRANT SELECT,INSERT ON department_creation_grants,department_audit_events,user_audit_events,role_policy_audit_events TO auth_test_api",
        "GRANT UPDATE(head_user_id,state,department_id,consumed_at,revoked_at) ON department_creation_grants TO auth_test_api",
        "GRANT SELECT,INSERT,UPDATE ON role_policies TO auth_test_api",
        "GRANT SELECT,INSERT ON usage_policy,usage_requests,usage_provider_attempts,usage_entries,usage_policy_audit TO auth_test_api",
        "GRANT UPDATE(enabled,timezone,monthly_questions,daily_questions,rate_count,rate_seconds,monthly_budget_micro_usd,version) ON usage_policy TO auth_test_api",
        "GRANT UPDATE(state,outcome,held_micro_usd,claim_id,started_at,closed_at) ON usage_requests TO auth_test_api",
        "GRANT UPDATE(state,actual_micro_usd,receipt_key,charged_month,input_tokens,output_tokens,settled_at) ON usage_provider_attempts TO auth_test_api",
    ]
    with bootstrap.begin() as c:
        for statement in statements: c.execute(text(statement))
    with TestClient(create_app(central_engine=runtime, auth_settings=AuthSettings(mode="development_password", app_env="test"))) as client:
        yield bootstrap, runtime, client, login_headers(client, password, "admin"), deputy


def new_identity(lifecycle):
    bootstrap, _, c, admin, _ = lifecycle
    email = uuid.uuid4().hex + "@example.invalid"
    result = c.post("/admin/users", headers=admin, json={"name": "Synthetic workflow", "email": email})
    assert result.status_code == 201
    password = secrets.token_urlsafe(24)
    with Session(bootstrap) as s: set_password(s, email, password)
    def login():
        response = c.post("/auth/dev/login", json={"email": email, "password": password})
        assert response.status_code == 200
        return {"Authorization": "Bearer " + response.json()["access_token"]}
    return result.json()["id"], login


def test_user_membership_policy_and_session_lifecycle(lifecycle):
    _, runtime, c, admin, deputy = lifecycle
    identity, login = new_identity(lifecycle)
    original = login()
    assert c.put(f"/admin/users/{identity}/memberships/hr", headers=admin, json={"status": "active"}).status_code == 200
    assert c.get("/auth/me", headers=original).status_code == 401
    assert c.post(f"/admin/users/{identity}/roles", headers=admin, json={"role_id": deputy, "department_id": "hr"}).status_code == 201
    scoped = login()
    with TestClient(auth_test_app(runtime)) as probe:
        assert probe.get("/test/departments/hr", headers=scoped).status_code == 200
        assert probe.get("/test/departments/finance", headers=scoped).status_code == 403
    assert c.put(f"/admin/roles/{deputy}/permissions", headers=admin, json={"expected_version": 1, "permissions": []}).status_code == 200
    assert c.get("/me/usage", headers=scoped).status_code == 401
    with TestClient(auth_test_app(runtime)) as probe:
        assert probe.get("/test/departments/hr", headers=login()).status_code == 403
    assert c.patch(f"/admin/users/{identity}/status", headers=admin, json={"status": "inactive"}).status_code == 200
    assert c.get("/auth/me", headers=original).status_code == 401
    assert c.patch(f"/admin/users/{identity}/status", headers=admin, json={"status": "active"}).status_code == 200
    assert c.get("/auth/me", headers=login()).status_code == 200
    history = c.get(f"/admin/users/{identity}/audit", headers=admin).json()
    assert {e["action"] for e in history} >= {"user_created", "membership_changed", "role_assigned", "user_status_changed"}


def test_granted_department_fails_closed_without_document_schema(lifecycle, tmp_path):
    bootstrap, _, c, admin, _ = lifecycle
    identity, login = new_identity(lifecycle)
    name = "Synthetic workflow " + uuid.uuid4().hex
    assert c.post("/admin/department-creation-grants", headers=admin, json={"head_user_id": identity, "name": name}).status_code == 201
    token = login()
    response = c.post("/departments/from-grant", headers=token, json={})
    assert response.status_code == 202
    department = response.json()["id"]
    assert response.json()["status"] == "provisioning"
    assert c.post("/departments/from-grant", headers=token, json={}).status_code == 409
    resources = Mock()
    # Only registry transitions use the isolated bootstrap connection. No physical
    # tenant database, real contract, or original development worker is invoked.
    worker = Worker(bootstrap, resources, CredentialStore(tmp_path / "credentials"), UnavailableTenantContract())
    assert worker.run(department) == "failed"
    resources.ensure_resources.assert_not_called()
    detail = c.get(f"/admin/departments/{department}", headers=admin).json()
    assert detail["status"] == "failed" and detail["last_error_code"] == "tenant_contract_unavailable"
    assert c.post(f"/admin/departments/{department}/archive", headers=admin).json()["status"] == "archived"
    assert c.post("/departments/from-grant", headers=token, json={}).status_code == 409
    with Session(bootstrap) as s:
        assert s.get(Department, "legal").status == "failed"
        assert s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == department)).status == "failed"


def test_authenticated_usage_processing_receipt_and_replay(lifecycle):
    _, runtime, c, admin, _ = lifecycle
    identity, login = new_identity(lifecycle)
    token = login()
    policy = c.put("/admin/usage/policy", headers=admin, json={**POLICY, "expected_version": 0})
    assert policy.status_code == 200
    calls = []
    def processor(context):
        def receipt(key):
            calls.append(key)
            return ProviderReceipt("synthetic-" + key, 7)
        context.perform("lookup", "fake-provider", "fake-model", 10, receipt)
        return "no_answer"
    first = run_once(runtime, identity, "workflow-request", "a" * 64, 10, processor)
    replay = run_once(runtime, identity, "workflow-request", "a" * 64, 10, processor)
    assert first.state == "completed" and replay.replayed and len(calls) == 1
    summary = c.get("/me/usage", headers=token).json()
    assert summary["monthly_used_or_held"] == 1 and summary["spent_micro_usd"] == 7
    rows = c.get("/me/usage/ledger", headers=token).json()
    assert rows and {r["user_id"] for r in rows} == {identity}
    assert c.get("/admin/usage/summary", headers=token).status_code == 403
    company = c.get("/admin/usage/summary", headers=admin).json()
    assert company["spent_micro_usd"] == 7 and company["held_micro_usd"] == 0
