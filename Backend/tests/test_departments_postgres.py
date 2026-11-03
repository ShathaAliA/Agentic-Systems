"""Department API integration in the backed-up, isolated auth test cluster only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import secrets
from threading import Event
from types import SimpleNamespace
import uuid
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.auth.enroll import set_password
from app.credentials import CredentialStore
from app.departments.models import CreationGrant, DepartmentAudit
from app.main import create_app
from app.models import Department, DepartmentDatabase, DepartmentMembership, Role, RoleAssignment, User
from app.provisioning.worker import Worker
from test_authentication import login_headers
from test_authentication_postgres import isolated_postgres, pytestmark


@pytest.fixture(scope="module")
def departments_pg(isolated_postgres):
    admin, runtime, password = isolated_postgres
    with admin.begin() as c:
        # Reviewed additional application grants; no UPDATE to registry status.
        c.execute(text("GRANT INSERT ON departments,department_databases,department_memberships,role_assignments TO auth_test_api"))
        c.execute(text("GRANT UPDATE(archived_at,archived_by) ON departments TO auth_test_api"))
        c.execute(text("GRANT UPDATE(status) ON department_memberships TO auth_test_api"))
        c.execute(text("GRANT UPDATE(claim_token,next_retry_at,verified_contract) ON department_databases TO auth_test_api"))
        c.execute(text("GRANT SELECT ON department_databases TO auth_test_api"))
        c.execute(text("GRANT SELECT,INSERT ON department_creation_grants TO auth_test_api"))
        c.execute(text("GRANT UPDATE(head_user_id,state,department_id,consumed_at,revoked_at) ON department_creation_grants TO auth_test_api"))
        c.execute(text("GRANT SELECT,INSERT ON department_audit_events TO auth_test_api"))
    with Session(admin) as s:
        s.add(Role(id="manager", name="Department Manager", scope="department"))
        s.commit()
        # Migration backfilled the pre-existing failed department without guessing its head.
        grant = s.scalar(select(CreationGrant).where(CreationGrant.department_id == "existing-department"))
        assert grant.state == "consumed" and grant.head_user_id is None
        assert s.get(Department, "existing-department").status == "failed"
    app = create_app(central_engine=runtime, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        administrator = login_headers(client, password, "admin")
        yield admin, runtime, app, client, administrator


def add_head(admin, client):
    identity = str(uuid.uuid4())
    password = secrets.token_urlsafe(24)
    email = f"{identity}@example.invalid"
    with Session(admin) as s:
        s.add(User(id=identity, name="Synthetic Head", email=email))
        s.commit()
        set_password(s, email, password)
    response = client.post("/auth/dev/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return identity, {"Authorization": "Bearer " + response.json()["access_token"]}


def test_concurrent_consumption_creates_exactly_one_department(departments_pg):
    admin, runtime, app, client, administrator = departments_pg
    head, headers = add_head(admin, client)
    grant = client.post("/admin/department-creation-grants", headers=administrator,
        json={"head_user_id": head, "name": "Concurrent " + head}).json()
    assert grant["state"] == "pending"
    def attempt(_):
        with TestClient(app) as separate:
            return separate.post("/departments/from-grant", headers=headers, json={}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, range(4)))
    assert outcomes.count(202) == 1 and outcomes.count(409) == 3
    with Session(admin) as s:
        row = s.get(CreationGrant, grant["id"])
        assert row.state == "consumed"
        assert s.scalar(select(func.count()).select_from(RoleAssignment).where(RoleAssignment.user_id == head)) == 1
        assert s.scalar(select(func.count()).select_from(DepartmentMembership).where(DepartmentMembership.user_id == head)) == 1
        assert s.scalar(select(func.count()).select_from(DepartmentAudit).where(
            DepartmentAudit.grant_id == grant["id"], DepartmentAudit.action == "department_registered")) == 1


def test_concurrent_name_reservations_and_conflicting_admin_creation(departments_pg):
    admin, _, app, client, administrator = departments_pg
    first, _ = add_head(admin, client)
    second, _ = add_head(admin, client)
    name = "Reserved " + str(uuid.uuid4())
    def attempt(head):
        with TestClient(app) as separate:
            return separate.post("/admin/department-creation-grants", headers=administrator,
                json={"head_user_id": head, "name": name if head == first else name.upper()}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [first, second]))
    assert sorted(results) == [201, 409]
    assert client.post("/admin/departments", headers=administrator, json={"name": name}).status_code == 409


def test_audit_permission_failure_rolls_back_every_registration_write(departments_pg):
    admin, _, _, client, administrator = departments_pg
    head, headers = add_head(admin, client)
    grant = client.post("/admin/department-creation-grants", headers=administrator,
        json={"head_user_id": head, "name": "Rollback " + head}).json()
    with admin.begin() as c:
        c.execute(text("REVOKE INSERT ON department_audit_events FROM auth_test_api"))
    try:
        assert client.post("/departments/from-grant", headers=headers, json={}).status_code == 503
    finally:
        with admin.begin() as c:
            c.execute(text("GRANT INSERT ON department_audit_events TO auth_test_api"))
    with Session(admin) as s:
        row = s.get(CreationGrant, grant["id"])
        assert row.state == "pending" and row.department_id is None
        assert s.scalar(select(func.count()).select_from(Department).where(Department.name == row.name)) == 0
        assert s.scalar(select(func.count()).select_from(RoleAssignment).where(RoleAssignment.user_id == head)) == 0


def test_archiving_during_worker_verification_cannot_reactivate(departments_pg, tmp_path):
    admin, _, _, client, administrator = departments_pg
    identity = client.post("/admin/departments", headers=administrator,
        json={"name": "Archive race " + str(uuid.uuid4())}).json()["id"]
    started, release = Event(), Event()
    adapter = Mock()
    adapter.migration_url.return_value = "test-only"
    def verify(*args):
        started.set()
        assert release.wait(10)
    adapter.verify.side_effect = verify
    migrator = SimpleNamespace(available=True, contract_id="synthetic-contract", required_tables=("synthetic",),
                               migrate=Mock(return_value="synthetic_revision"))
    worker = Worker(admin, adapter, CredentialStore(tmp_path), migrator)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.run, identity)
        try:
            assert started.wait(10)
            response = client.post(f"/admin/departments/{identity}/archive", headers=administrator)
            assert response.status_code == 200 and response.json()["status"] == "archived"
        finally:
            release.set()
        assert future.result(timeout=10) == "archived"
    assert worker.run(identity, retry_failed=True) == "archived"
    adapter.ensure_resources.assert_called_once()
    with Session(admin) as s:
        registry = s.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == identity))
        assert registry.status != "active" and registry.verified_contract is None and registry.claim_token is None


def test_runtime_cannot_edit_audit_delete_history_or_activate_registry(departments_pg):
    _, runtime, _, _, _ = departments_pg
    for statement in ("UPDATE department_audit_events SET action='forged'", "DELETE FROM department_audit_events",
                      "DELETE FROM department_creation_grants", "DELETE FROM departments",
                      "UPDATE department_databases SET status='active'", "UPDATE departments SET status='active'",
                      "UPDATE department_creation_grants SET name='forged'"):
        with runtime.connect() as c, pytest.raises(DBAPIError) as error:
            c.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"


def test_concurrent_grants_to_same_head_and_revoke_consume_race(departments_pg):
    admin, _, app, client, administrator = departments_pg
    head, headers = add_head(admin, client)
    def issue(index):
        with TestClient(app) as separate:
            return separate.post("/admin/department-creation-grants", headers=administrator,
                json={"head_user_id": head, "name": f"Single owner {head} {index}"}).status_code
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(issue, range(3)))
    assert sorted(results) == [201, 409, 409]
    grant = client.get("/me/department-creation-grant", headers=headers).json()
    def consume():
        with TestClient(app) as separate:
            return separate.post("/departments/from-grant", headers=headers, json={}).status_code
    def revoke():
        with TestClient(app) as separate:
            return separate.post(f"/admin/department-creation-grants/{grant['id']}/revoke", headers=administrator).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        consumption, revocation = pool.submit(consume), pool.submit(revoke)
        outcomes = (consumption.result(), revocation.result())
    assert outcomes in {(202, 409), (403, 200)}
    with Session(admin) as s:
        row = s.get(CreationGrant, grant["id"])
        assert (row.state, row.department_id is not None) in {("consumed", True), ("revoked", False)}


def test_admin_direct_head_and_existing_department_binding_with_narrow_grants(departments_pg):
    admin, _, _, client, administrator = departments_pg
    direct_head, direct_headers = add_head(admin, client)
    direct = client.post("/admin/departments", headers=administrator,
        json={"name": "Direct " + direct_head, "head_user_id": direct_head})
    assert direct.status_code == 202
    assert client.get("/me/department", headers=direct_headers).json()["id"] == direct.json()["id"]
    bound_head, bound_headers = add_head(admin, client)
    unowned = client.post("/admin/departments", headers=administrator,
        json={"name": "Unowned " + bound_head}).json()["id"]
    bound = client.post(f"/admin/departments/{unowned}/head", headers=administrator,
        json={"head_user_id": bound_head})
    assert bound.status_code == 200
    assert client.get("/me/department", headers=bound_headers).json()["id"] == unowned
    assert client.post("/admin/department-creation-grants", headers=administrator,
        json={"head_user_id": bound_head, "name": "Another " + bound_head}).status_code == 409
