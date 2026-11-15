"""Safety properties of test discovery and reporting, without infrastructure."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("core_test_runner", ROOT / "Backend/tools/run-core-tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize("target", ["document_results", "documents", "document_provisioning"])
def test_document_fixture_graph_resolves_without_infrastructure(target):
    environment = runner.clean_environment(os.environ)
    environment["RUN_AUTH_POSTGRES_TESTS"] = "1"
    # --setup-plan resolves the actual fixture graph but executes no fixture or SQL.
    result = subprocess.run([sys.executable, "-m", "pytest",
        f"Backend/tests/test_{target}_postgres.py", "--isolated-postgres", "--setup-plan",
        "-q", "-p", "no:cacheprovider"], cwd=ROOT, env=environment,
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("cwd", [ROOT, ROOT / "Backend"])
def test_default_collection_uses_safe_suite_from_either_directory(cwd):
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=cwd, env=runner.clean_environment(os.environ), capture_output=True, text=True)
    assert result.returncode == 0
    assert "test_core_access.py::" in result.stdout
    assert "test_core_lifecycle_postgres.py::" in result.stdout
    assert "Backend/test_department_provisioning.py::" not in result.stdout
    assert "Backend/test_database_router.py::" not in result.stdout


def test_explicit_legacy_path_is_refused_before_import():
    # A harmless canary stands in for the dangerous legacy scripts. Never invoke
    # the actual scripts to prove that their import side effects are prevented.
    with tempfile.TemporaryDirectory(prefix=".test-safety-", dir=ROOT / "Backend") as directory:
        path = Path(directory).resolve()
        assert path.parent == ROOT / "Backend"  # Bound automatic recursive cleanup.
        marker = path / "imported"
        script = path / "test_canary.py"
        script.write_text("from pathlib import Path\nPath(__file__).with_name('imported').touch()\n", encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "pytest", str(script), "--collect-only", "-p", "no:cacheprovider"],
            cwd=ROOT, env=runner.clean_environment(os.environ), capture_output=True, text=True)
        assert result.returncode != 0
        assert "Only Backend/tests" in result.stderr
        assert not marker.exists()


def test_backend_only_build_context_keeps_discovery_safe(tmp_path):
    import shutil
    backend = tmp_path / "backend"
    (backend / "tests").mkdir(parents=True)
    for name in ("pytest.ini", "conftest.py"):
        shutil.copyfile(ROOT / "Backend" / name, backend / name)
    (backend / "test_legacy.py").write_text(
        "from pathlib import Path\nPath(__file__).with_name('unsafe-import').touch()\n", encoding="utf-8")
    (backend / "tests/test_safe.py").write_text("def test_safe():\n    assert True\n", encoding="utf-8")
    (backend / "tests/test_development_provisioning.py").write_text(
        "def test_live():\n    raise AssertionError('Live test executed unexpectedly')\n", encoding="utf-8")
    env = runner.clean_environment(os.environ)
    env.update(RUN_DEVELOPMENT_PROVISIONING="1", DEV_BACKUP_ACK="1")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=backend, env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert "1 passed, 1 skipped" in result.stdout
    assert not (backend / "unsafe-import").exists()


def test_inherited_live_and_isolated_flags_cannot_enable_database_tests():
    env = runner.clean_environment(os.environ)
    env.update(RUN_AUTH_POSTGRES_TESTS="1", RUN_DEVELOPMENT_PROVISIONING="1", DEV_BACKUP_ACK="1",
               AUTH_TEST_ADMIN_URL_FILE="nonexistent-canary-file", DEV_STATE_DIR="nonexistent-canary-directory")
    result = subprocess.run([sys.executable, "-m", "pytest", "Backend/tests/test_authentication_postgres.py",
        "Backend/tests/test_development_provisioning.py", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert "3 skipped" in result.stdout
    assert "ERROR" not in result.stdout


def test_environment_scrubs_credentials_and_disables_dotenv():
    clean = runner.clean_environment({"PATH": "keep", "RUN_AUTH_POSTGRES_TESTS": "1", "DEV_BACKUP_ACK": "1",
        "DATABASE_URL": "canary", "PGPASSWORD": "canary", "OPENAI_API_KEY": "canary",
        "TENANT_CONTRACT_FILE": "canary", "PYTEST_ADDOPTS": "canary", "ANTHROPIC_API_KEY": "canary"})
    assert not any(v == "canary" for v in clean.values())
    assert clean["PATH"] == "keep" and clean["PYTHON_DOTENV_DISABLED"] == "1"


def test_reports_redact_tokens_dsns_and_named_passwords(tmp_path):
    token = "dev_" + "a" * 43
    secret = "synthetic-private-value"
    text = f"Bearer {token} postgresql+psycopg://user:{secret}@localhost/db password='{secret}'"
    xml = tmp_path / "raw.xml"
    xml.write_text('<testsuites><testsuite tests="1" failures="1"><system-out>' + text + '</system-out></testsuite></testsuites>', encoding="utf-8")
    result = runner.record_result("failure", SimpleNamespace(stdout=text, returncode=1), xml, tmp_path)
    assert result["status"] == "failed"
    for file in (tmp_path / "failure.xml", tmp_path / "failure.log"):
        content = file.read_text(encoding="utf-8")
        assert secret not in content and token not in content


@pytest.mark.parametrize("xml,code,status", [(None, 0, "blocked"),
    ('<testsuites><testsuite tests="2" skipped="2"/></testsuites>', 0, "failed"),
    ('<testsuites><testsuite tests="1" failures="1"/></testsuites>', 0, "failed"),
    ('<testsuites><testsuite tests="1"/></testsuites>', 1, "failed")])
def test_empty_skipped_failed_or_nonzero_results_cannot_be_green(tmp_path, xml, code, status):
    path = tmp_path / "source.xml"
    if xml: path.write_text(xml, encoding="utf-8")
    result = runner.record_result("canary", SimpleNamespace(stdout="", returncode=code), path, tmp_path, isolated=True)
    assert result["status"] == status


def test_orchestrator_continues_and_reports_failure_without_spawning_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "GROUPS", ("first", "second"))
    monkeypatch.setattr(runner, "restrict_directory", lambda p: p.mkdir(parents=True))
    attempts = []
    def unavailable(*args, **kwargs):
        attempts.append(args[0])
        raise FileNotFoundError("synthetic executable unavailable")
    monkeypatch.setattr(runner.subprocess, "run", unavailable)
    args = argparse.Namespace(suite="integration", backend="native", pwsh="synthetic-missing", postgres_bin="unused")
    assert runner.run(args) == 1 and len(attempts) == 2
    summary = json.loads(next((tmp_path / ".local/core-tests").glob("*/summary.json")).read_text())
    assert summary["status"] == "failed"
    assert summary["live_provisioning"] == "excluded"
    assert all(r["status"] == "blocked" for r in summary["results"])
