"""Phase 2A targets and process boundaries. No Phase 1 imports or environment files."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

if not __debug__:
    raise RuntimeError("phase2a_requires_non_optimized_python")

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
STATE = REPO / ".local/poc-phase2"
CENTRAL = "poc2_central"
PURPOSE = "synthetic-phase2a-identity-only"


def clean_environment():
    # Positive allow-list: vendor keys (including Humain), DB URLs, proxies,
    # Docker contexts and development flags cannot leak into child processes.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATHEXT",
            "PSMODULEPATH", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONIOENCODING="utf-8",
               APP_ENV="test", AUTH_MODE="disabled")
    return env


def command(args, *, env=None, timeout=90, input=None):
    # Temporary files avoid inherited-pipe hangs from Windows background services.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = subprocess.run([str(a) for a in args], input=input,
            stdin=None if input is not None else subprocess.DEVNULL,
            stdout=out, stderr=err, env=env or clean_environment(), timeout=timeout)
        out.seek(0)
        if result.returncode:
            raise RuntimeError("phase2a_command_failed")
        return out.read()


def private_file(path, text):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:
        stream.write(text)


def write_json(path, data):
    if is_link(path):
        raise ValueError("symlink_state_rejected")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_link(path):
    return path.is_symlink() or path.is_junction()


def validate_runtime_url(url, host, port):
    # psycopg query parameters can override the host/port in a parsed URL.
    if (url.drivername != "postgresql+psycopg" or url.query
            or url.host != host or url.port != port
            or url.database != CENTRAL or url.username != "poc2_api"):
        raise ValueError("invalid_isolated_runtime_url")


def load(run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("generated_run_id_required")
    folder = STATE / run_id
    for parent in (REPO / ".local", STATE, folder):
        if is_link(parent):
            raise ValueError("symlink_state_rejected")
    if folder.resolve().parent != STATE.resolve():
        raise ValueError("state_outside_phase2")
    for file in folder.rglob("*"):
        if is_link(file):
            raise ValueError("symlink_state_rejected")
    state = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    if state["run_id"] != run_id or state["purpose"] != PURPOSE or state["project"] != "ai-poc2-" + run_id:
        raise ValueError("run_identity_mismatch")
    expected = {"primary", "other"}
    if set(state["tenants"]) != expected:
        raise ValueError("unexpected_tenants")
    for item in state["tenants"].values():
        uuid.UUID(item["id"])
        if not re.fullmatch(r"dept_[0-9a-f]{32}", item["database"]):
            raise ValueError("unmanaged_tenant_database")
    if any(len({item[key] for item in state["tenants"].values()}) != 2
           for key in ("id", "database")):
        raise ValueError("distinct_tenants_required")
    return folder, state


def target(state):
    if state["backend"] == "docker":
        return "database", 5432
    if state["backend"] == "native" and type(state.get("pg_port")) is int and 1024 < state["pg_port"] < 65536 and state["pg_port"] != 5435:
        return "127.0.0.1", state["pg_port"]
    raise ValueError("unsafe_database_target")
