"""Failure-path tests never invoke a real Docker process."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location("core_smoke", Path(__file__).resolve().parents[1] / "tools/check-core-container.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def test_unavailable_engine_never_creates_or_removes_containers(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(smoke.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="engine unavailable"):
        smoke.check("synthetic-image")
    assert len(calls) == 1 and calls[0][1] == "info"


def test_health_failure_cleans_only_owned_volume_free_container(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        kind = command[1]
        return SimpleNamespace(returncode=1 if kind == "exec" else 0,
                               stdout="linux" if kind == "info" else "")
    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="health"):
        smoke.check("synthetic-image")
    created = next(c for c in calls if c[1] == "run")
    name = created[created.index("--name") + 1]
    assert name.startswith("core-smoke-")
    assert "--network" in created and "none" in created
    assert not {"-v", "--volume", "--mount", "-p", "--publish"} & set(created)
    assert calls[-1] == ["docker", "rm", "-f", name]
