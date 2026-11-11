"""Phase 2A control-plane safety; never starts services or imports Phase 1."""
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).parents[2]
P2 = ROOT / "poc/phase2"
sys.path.insert(0, str(P2))
import safety as p2_safety
import control as p2_control
from server import make_app


def test_optimized_python_cannot_disable_verification_gates():
    result = subprocess.run([sys.executable, "-O", "-c",
        "import sys; sys.path.insert(0,sys.argv[1]); import safety", str(P2)],
        env=p2_safety.clean_environment(), capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "phase2a_requires_non_optimized_python" in result.stderr


@pytest.mark.parametrize("suffix", ["?host=outside.invalid", "?port=5435", "?service=operator"])
def test_runtime_url_rejects_connection_parameter_overrides(suffix):
    from sqlalchemy.engine import make_url
    base = "postgresql+psycopg://poc2_api@database:5432/poc2_central"
    p2_safety.validate_runtime_url(make_url(base), "database", 5432)
    with pytest.raises(ValueError, match="invalid_isolated_runtime_url"):
        p2_safety.validate_runtime_url(make_url(base + suffix), "database", 5432)


@pytest.mark.parametrize("owned", [True, False])
def test_failure_stops_only_verified_owned_project_and_retains_state(tmp_path, monkeypatch, owned):
    state = {"phase": "starting"}
    monkeypatch.setattr(p2_control, "load", lambda run: (tmp_path, state))
    calls = []
    class Docker:
        def owned(self):
            if not owned:
                raise ValueError("container_identity_mismatch")
        def call(self, *args):
            calls.append(args)
    p2_control.retain_failed_run(Docker(), "a" * 32)
    report = json.loads((tmp_path / "run.json").read_text())
    assert report["phase"] == "failed" and report["verified"] is False
    assert report["stopped"] is owned
    assert calls == ([("stop",)] if owned else [])
    assert report.get("cleanup_requires_review", False) is not owned


def test_junction_state_is_rejected_before_reading_run(tmp_path, monkeypatch):
    monkeypatch.setattr(p2_safety, "STATE", tmp_path)
    monkeypatch.setattr(Path, "is_junction", lambda path: path == tmp_path)
    with pytest.raises(ValueError, match="symlink_state_rejected"):
        p2_safety.load("a" * 32)


def test_child_environment_drops_all_vendor_and_database_credentials(monkeypatch):
    for name in ("HUMAIN_API_KEY", "OPENAI_API_KEY", "DATABASE_URL", "PGPASSWORD", "DOCKER_HOST", "HTTP_PROXY"):
        monkeypatch.setenv(name, "PRIVATE_ENV_CANARY")
    env = p2_safety.clean_environment()
    assert "PRIVATE_ENV_CANARY" not in env.values()
    assert env["PYTHON_DOTENV_DISABLED"] == "1"


def test_generated_runs_are_private_unique_and_never_point_to_phase1(tmp_path, monkeypatch):
    state_root = tmp_path / "new-phase2"
    monkeypatch.setattr(p2_control, "STATE", state_root)
    monkeypatch.setattr(p2_safety, "STATE", state_root)
    def prepare(args, **kwargs):
        if "-Path" in args:
            Path(args[-1]).mkdir(parents=True)
        return b""
    monkeypatch.setattr(p2_control, "command", prepare)
    one, two = p2_control.initialize(), p2_control.initialize()
    assert one != two
    folder, state = p2_safety.load(one)
    assert state["phase"] == "new" and state["backend"] is None
    assert len(json.loads((folder / "secrets.json").read_text())["accounts"]) == 6
    assert {item["database"] for item in state["tenants"].values()}.isdisjoint({"poc_registry", "poc_experimental", "hr_db", "finance_db"})
    state["backend"], state["pg_port"] = "native", 5435
    with pytest.raises(ValueError, match="unsafe_database"):
        p2_safety.target(state)
    with pytest.raises(ValueError): p2_safety.load("../phase1")


def test_compose_has_no_development_or_phase1_mounts_and_separates_secrets():
    data = yaml.safe_load((P2 / "compose.yml").read_text())
    db, api, tools = (data["services"][name] for name in ("database", "api", "tools"))
    assert "ports" not in db and data["networks"]["internal"]["internal"] is True
    assert db["networks"] == tools["networks"] == ["internal"]
    assert api["networks"] == ["internal", "loopback"]
    assert all(not volume.get("external", False) for volume in data["volumes"].values())
    assert api["ports"][0].startswith("127.0.0.1:")
    assert "secrets" not in api and api["read_only"] is True
    assert api["tmpfs"] == ["/tmp:rw,noexec,nosuid,size=64m,mode=1777"]
    assert all("phase1" not in value and not value.startswith("../..:") for value in api["volumes"])
    assert any("/api:/run/poc2-api:ro" in value for value in api["volumes"])
    assert api["cap_drop"] == ["ALL"] and tools["cap_drop"] == ["ALL"]
    assert api["build"]["target"] == "runtime" and tools["build"]["target"] == "test"
    assert not any("LLM" in key or "API_KEY" in key for key in api["environment"])


def test_core_auth_is_real_and_document_routes_are_blocked():
    from app.models import Base
    from test_authentication import seed_auth_users, login_headers
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    password = seed_auth_users(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE _poc2_identity(run_id text,purpose text)"))
        connection.execute(text("INSERT INTO _poc2_identity VALUES (:run,:purpose)"),
            {"run": "a" * 32, "purpose": p2_safety.PURPOSE})
    with pytest.raises(RuntimeError, match="wrong_phase2_run_identity"):
        make_app(engine, "b" * 32)
    with TestClient(make_app(engine, "a" * 32)) as client:
        assert client.get("/auth/me", headers={"X-User-ID": "admin"}).status_code == 401
        employee = login_headers(client, password)
        assert client.get("/poc/permissions/hr/documents:read", headers=employee).status_code == 200
        assert client.get("/poc/permissions/finance/documents:read", headers=employee).status_code == 403
        assert client.get("/poc/permissions/hr/documents:upload", headers=employee).status_code == 403
        assert client.post("/api/documents/ask", headers=employee).status_code == 503
        assert client.post("/api/documents/upload", headers=employee).status_code == 503
        assert client.get("/poc/status").json()["llm_enabled"] is False
        assert client.get("/poc/status").headers["cache-control"] == "no-store"
    engine.dispose()
