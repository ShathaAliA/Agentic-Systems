"""Security boundaries of the dev container configuration, without a daemon/DB."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_api_and_worker_have_separate_secrets_and_write_boundaries():
    services = yaml.safe_load((ROOT / "compose.provisioning.yml").read_text())["services"]
    api, worker = services["backend"], services["provisioning-worker"]
    assert set(api["secrets"]) == {"api_url"}
    assert set(worker["secrets"]) == {"registry_url", "provisioner_url"}
    assert not any("PROVISIONING_" in key or "MIGRATION_" in key for key in api["environment"])
    for service in (api, worker):
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert service["build"]["target"] == "runtime"
        assert service["image"] == "ai-platform-core:dev"
    assert api["volumes"][0]["read_only"] is True
    assert worker["volumes"][1]["read_only"] is True
    assert api["ports"] == ["127.0.0.1:8000:8000"]
    assert "ports" not in worker
    assert "/health'" in api["healthcheck"]["test"][-1]
    assert api["profiles"] == ["backend"] and worker["profiles"] == ["provisioning"]


def test_default_image_excludes_tests_and_runtime_cannot_escalate_user():
    dockerfile = (ROOT / "Backend/Dockerfile").read_text()
    base, test = dockerfile.split("FROM base AS test", 1)
    test, runtime = test.split("FROM base AS runtime", 1)
    assert "requirements-test" not in base + runtime
    assert "requirements-test" in test
    assert "COPY . ." not in runtime
    assert "USER backend" in runtime and "USER root" not in runtime
    assert "--no-proxy-headers" in runtime
    assert "PYTHON_DOTENV_DISABLED=1" in base
    ignored = (ROOT / "Backend/.dockerignore").read_text().splitlines()
    for entry in (".env", ".env.*", ".local", "secrets", "credentials", "*.dump", "*.pem", "*.key", ".git"):
        assert entry in ignored
