"""Real login/RBAC and SQL boundary tests on the run's isolated databases only."""
import json
import sys
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient

from safety import CENTRAL, PURPOSE, target, write_json, validate_runtime_url
from server import make_app


def verify(folder, state):
    from app.models import User, Department, RoleAssignment, DepartmentMembership
    from app.credentials import CredentialStore
    from app.runtime_security import restrict_runtime_engine
    from app.auth.models import DevelopmentSession
    mapping = json.loads((folder / "mapping.json").read_text())
    secrets = json.loads((folder / "secrets.json").read_text())
    url = make_url(json.loads((folder / "api/url.json").read_text())["url"])
    host, port = target(state)
    validate_runtime_url(url, host, port)
    bootstrap = create_engine(url.set(username="poc2_bootstrap", password=secrets["database"]["poc2_bootstrap"]), hide_parameters=True)
    runtime = restrict_runtime_engine(create_engine(url, hide_parameters=True))
    checks = {}
    with bootstrap.connect() as c:
        assert c.execute(text("SELECT run_id,purpose FROM _poc2_identity")).one() == (state["run_id"], PURPOSE)
    with Session(runtime) as s:
        assert s.scalar(select(func.count()).select_from(User)) == 6
        assert set(s.scalars(select(Department.name))) == {"Experimental Resources Department", "Other Synthetic Department"}
        assert set(s.scalars(select(User.email))) == {f"poc2-{who}@example.invalid" for who in mapping["users"]}
        assert set(s.scalars(select(Department.status))) == ({"provisioning"} if state["identity_only"] else {"active"})
        assert s.scalar(select(func.count()).select_from(RoleAssignment)) == 6
        assert s.scalar(select(func.count()).select_from(DepartmentMembership)) == 5
    checks["six_synthetic_users_and_two_new_departments"] = True
    app = make_app(runtime, state["run_id"])
    with TestClient(app) as client:
        assert client.get("/auth/me").status_code == 401
        assert client.get("/auth/me", headers={"X-User-ID": mapping["users"]["admin"]}).status_code == 401
        assert client.get("/auth/me", headers={"Authorization": "Bearer forged"}).status_code == 401
        checks["missing_and_forged_identity_denied"] = True
        headers = {}
        for actor in mapping["users"]:
            response = client.post("/auth/dev/login", json={"email": f"poc2-{actor}@example.invalid", "password": secrets["accounts"][actor]})
            assert response.status_code == 200, "synthetic_login_failed"
            value = response.json()
            headers[actor] = {"Authorization": "Bearer " + value["access_token"]}
            me = client.get("/auth/me", headers=headers[actor])
            assert me.status_code == 200 and me.json()["id"] == mapping["users"][actor]
            assert me.headers["cache-control"] == "no-store"
        checks["six_real_expiring_core_logins"] = True
        with Session(runtime) as s:
            assert all(row.expires_at > row.created_at for row in s.scalars(select(DevelopmentSession)))
        assert client.get("/admin/users", headers=headers["admin"]).status_code == 200
        assert client.get("/admin/roles", headers=headers["admin"]).status_code == 200
        for who in ("employee", "head", "deputy", "other", "denied"):
            assert client.get("/admin/users", headers=headers[who]).status_code == 403
        checks["global_admin_and_department_role_separation"] = True
        primary, other = (state["tenants"][k]["id"] for k in ("primary", "other"))
        def probe(actor, department, capability):
            return client.get(f"/poc/permissions/{department}/{capability}", headers=headers[actor]).status_code
        if state["identity_only"]:
            assert all(probe(actor, primary, "documents:read") == 403 for actor in headers)
            checks["pending_tenants_fail_closed"] = True
        else:
            assert probe("employee", primary, "documents:read") == 200
            assert probe("employee", primary, "documents:upload") == 403
            for actor in ("head", "deputy"):
                assert probe(actor, primary, "documents:upload") == 200
                assert probe(actor, primary, "documents:manage_permissions") == 200
            assert probe("other", primary, "documents:read") == 403
            assert probe("employee", other, "documents:read") == 403
            assert probe("denied", primary, "documents:read") == 403
            assert probe("other", other, "documents:read") == 200
            checks["active_tenant_role_and_department_permission_matrix"] = True
        for path in ("/api/documents/ask", "/api/documents/upload"):
            assert client.post(path, headers=headers["head"]).status_code == 503
        checks["document_and_model_routes_disabled"] = True
        token = headers["employee"]
        assert client.post("/auth/logout", headers=token).status_code == 204
        assert client.get("/auth/me", headers=token).status_code == 401
        checks["session_revocation"] = True
    identities = [("central", runtime)]
    for name, item in state["tenants"].items():
        cred = CredentialStore(folder / "api/tenants").get(item["database"].upper())
        engine = restrict_runtime_engine(create_engine(url.set(database=item["database"], username=cred.username, password=cred.password), hide_parameters=True))
        identities.append((name, engine))
    try:
        for name, engine in identities:
            with engine.connect() as c:
                assert not any(c.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one())
                assert c.scalar(text("SELECT count(*) FROM pg_database WHERE datallowconn AND datname<>current_database() AND has_database_privilege(current_user,oid,'CONNECT')")) == 0
                if name != "central":
                    assert c.scalar(text("SELECT department_id FROM _provisioning_identity")) == state["tenants"][name]["id"]
                    if not state["identity_only"]:
                        assert c.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
                        distance = c.scalar(text("SELECT '[1,0,0]'::vector <-> '[0,1,0]'::vector"))
                        assert abs(float(distance) - 2 ** 0.5) < 1e-6
                        checks[name + "_pgvector_runtime_distance"] = True
                        assert c.scalar(text("SELECT count(*) FROM documents")) == 0
                        assert c.scalar(text("SELECT count(*) FROM question_results")) == 0
            for query in ("CREATE SCHEMA forbidden_phase2a", "CREATE ROLE forbidden_phase2a"):
                with engine.connect() as c:
                    try: c.execute(text(query))
                    except DBAPIError as error: assert error.orig.sqlstate == "42501"
                    else: raise AssertionError("runtime_ddl_allowed")
            checks[name + "_least_privilege_and_no_cross_database_connect"] = True
        assert not {"app.documents.file_processor", "sentence_transformers", "openai", "google.genai"}.intersection(sys.modules)
        checks["no_document_data_or_provider_activity"] = True
        report = {"run_id": state["run_id"], "backend": state["backend"],
            "scope": "Phase2A steps 1-2 only", "identity_checks_passed": True,
            "full_environment_ready": not state["identity_only"], "checks": checks,
            "blocked": ["pgvector_tenant_schema_and_positive_department_RBAC"] if state["identity_only"] else [],
            "provider": {"configured": False, "requests": 0, "future_candidate": "Humain; compatibility not assessed"}}
        write_json(folder / "verification.json", report)
    finally:
        for _, engine in identities: engine.dispose()
        bootstrap.dispose()
