"""Create new Phase 2A resources only; no arbitrary DB URL, volume or project input."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import uuid

from safety import CENTRAL, PURPOSE, REPO, ROOT, STATE, clean_environment, command, load, private_file, write_json, is_link


def initialize():
    for directory in (REPO / ".local", STATE):
        if is_link(directory): raise ValueError("symlink_state_rejected")
    run_id = uuid.uuid4().hex
    folder = STATE / run_id
    folder.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        command(["pwsh", "-NoProfile", "-File", REPO / "Backend/tools/restrict-test-directory.ps1", "-Path", folder])
    else: folder.mkdir(mode=0o700)
    (folder / "api").mkdir(mode=0o700)
    passwords = {"database": {role: secrets.token_urlsafe(36) for role in
        ("poc2_bootstrap", "poc2_owner", "poc2_migrator", "poc2_api", "poc2_registry", "poc2_provisioner")},
        "accounts": {actor: secrets.token_urlsafe(24) for actor in ("employee", "head", "deputy", "other", "denied", "admin")}}
    private_file(folder / "secrets.json", json.dumps(passwords))
    private_file(folder / "bootstrap-password", passwords["database"]["poc2_bootstrap"])
    private_file(folder / "empty.env", "")
    (folder / "docker-config").mkdir(mode=0o700)
    config = {"auths": {}}
    executable = shutil.which("docker")
    if executable:
        plugins = Path(executable).resolve().parent.parent / "cli-plugins"
        if plugins.is_dir(): config["cliPluginsExtraDirs"] = [str(plugins)]
    private_file(folder / "docker-config/config.json", json.dumps(config))
    state = {"run_id": run_id, "purpose": PURPOSE, "project": "ai-poc2-" + run_id,
        "backend": None, "phase": "new", "tenants": {
            key: {"id": str(uuid.uuid4()), "database": "dept_" + uuid.uuid4().hex}
            for key in ("primary", "other")}}
    write_json(folder / "run.json", state)
    print("New Phase 2A run:", run_id)
    return run_id


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if port == 5435: return free_port()
    return port


def child(folder, state, operation, identity_only=False):
    env = clean_environment()
    env["PYTHONPATH"] = str(REPO / "Backend")
    args = [sys.executable, ROOT / "bootstrap.py", "--run", state["run_id"], "--operation", operation]
    if identity_only: args.append("--identity-only")
    command(args, env=env, timeout=300)


def native_test(run_id, binaries, identity_only):
    folder, state = load(run_id)
    if state["phase"] != "new": raise ValueError("new_run_required")
    suffix = ".exe" if os.name == "nt" else ""
    binaries = Path(binaries)
    if not (binaries / ("initdb" + suffix)).is_file(): raise ValueError("postgres_binaries_missing")
    state.update(backend="native", phase="starting", pg_port=free_port())
    write_json(folder / "run.json", state)
    def pg(name, *args, env=None):
        return command([binaries / (name + suffix), *args], env=env, timeout=90)
    pg("initdb", "-D", folder / "data", "-U", "poc2_bootstrap", "--pwfile", folder / "bootstrap-password",
       "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", "--encoding=UTF8")
    env = clean_environment()
    env["PGPASSWORD"] = (folder / "bootstrap-password").read_text()
    connection = ["-h", "127.0.0.1", "-p", str(state["pg_port"]), "-U", "poc2_bootstrap"]
    try:
        pg("pg_ctl", "-D", folder / "data", "-l", folder / "postgres.log",
           "-o", f"-h 127.0.0.1 -p {state['pg_port']}", "-w", "start")
        pg("createdb", *connection, CENTRAL, env=env)
        pg("pg_dump", *connection, "-d", CENTRAL, "-Fc", "-f", folder / "before.dump", env=env)
        pg("pg_dumpall", *connection, "--globals-only", "--no-role-passwords", "-f", folder / "globals-before.sql", env=env)
        pg("pg_restore", "--list", folder / "before.dump")
        child(folder, state, "bootstrap", identity_only)
        child(folder, state, "verify")
        print("Verification report:", folder / "verification.json")
    finally:
        pg("pg_ctl", "-D", folder / "data", "-m", "fast", "-w", "stop")
        state = json.loads((folder / "run.json").read_text())
        state["stopped"] = True
        write_json(folder / "run.json", state)
        print("Stopped only the new Phase 2A native cluster; private recovery state:", folder)


class Docker:
    def __init__(self, folder, state):
        self.folder, self.state = folder, state
        endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"
        self.prefix = ["docker", "--config", folder / "docker-config", "--host", endpoint]
        if command(self.prefix + ["info", "--format", "{{.OSType}}"], timeout=12).strip() != b"linux":
            raise RuntimeError("local_linux_engine_required")
        self.env = clean_environment()
        # Compose/buildx subprocesses must use this private run too, rather than
        # creating a .docker directory (including token seeds) in the repository.
        self.env.update(DOCKER_CONFIG=str(folder / "docker-config"),
                        BUILDX_CONFIG=str(folder / "docker-config/buildx"))
        self.env.update(POC2_PROJECT=state["project"], POC2_RUN=state["run_id"],
            POC2_STATE=str(folder), POC2_API_PORT=str(state["api_port"]))
        if os.name != "nt": self.env.update(POC2_UID=str(os.getuid()), POC2_GID=str(os.getgid()))
        self.compose = self.prefix + ["compose", "--profile", "api", "--profile", "tools",
            "--env-file", folder / "empty.env", "-f", ROOT / "compose.yml", "--project-name", state["project"]]

    def call(self, *args, timeout=300):
        return command(self.compose + list(args), env=self.env, timeout=timeout)

    def owned(self):
        identity = self.call("exec", "-T", "database", "psql", "-U", "poc2_bootstrap", "-d", CENTRAL, "-Atc", "SELECT current_database()")
        if identity.strip() != CENTRAL.encode(): raise ValueError("database_identity_mismatch")
        ids = self.call("ps", "-q", "database").decode().split()
        if len(ids) != 1: raise ValueError("container_identity_mismatch")
        label = command(self.prefix + ["inspect", "--format", '{{index .Config.Labels "ai.poc.phase2.run_id"}}', ids[0]]).decode().strip()
        if label != self.state["run_id"]: raise ValueError("container_identity_mismatch")


def docker_up(run_id):
    folder, state = load(run_id)
    if state["phase"] != "new": raise ValueError("new_run_required")
    state.update(backend="docker", api_port=free_port())
    write_json(folder / "run.json", state)
    try:
        docker = Docker(folder, state)  # fail before creating resources if unavailable
    except Exception:
        state["phase"] = "blocked_docker"
        write_json(folder / "run.json", state)
        raise
    existing = command(docker.prefix + ["ps", "-aq", "--filter", "label=com.docker.compose.project=" + state["project"]])
    volumes = command(docker.prefix + ["volume", "ls", "-q", "--filter", "label=com.docker.compose.project=" + state["project"]])
    networks = command(docker.prefix + ["network", "ls", "-q", "--filter", "label=com.docker.compose.project=" + state["project"]])
    if any(value.strip() for value in (existing, volumes, networks)): raise ValueError("resource_collision")
    state["phase"] = "starting"
    write_json(folder / "run.json", state)
    try:
        docker.call("up", "-d", "--wait", "database")
        docker.owned()
        (folder / "before.dump").write_bytes(docker.call("exec", "-T", "database", "pg_dump", "-U", "poc2_bootstrap", "-d", CENTRAL, "-Fc"))
        (folder / "globals-before.sql").write_bytes(docker.call("exec", "-T", "database", "pg_dumpall", "-U", "poc2_bootstrap", "--globals-only", "--no-role-passwords"))
        docker.call("build", "tools", "api", timeout=1800)
        docker.call("run", "--rm", "tools")
        docker.call("run", "--rm", "tools", "--run", run_id, "--operation", "verify")
        docker.call("up", "-d", "api")
        from smoke import smoke
        smoke(run_id)
    except Exception:
        retain_failed_run(docker, run_id)
        raise
    folder, state = load(run_id)
    state["verified"] = True
    write_json(folder / "run.json", state)
    print("Phase 2A Core API (documents/LLM disabled): http://127.0.0.1:" + str(state["api_port"]))


def retain_failed_run(docker, run_id):
    folder, state = load(run_id)
    state.update(phase="failed", verified=False, stopped=False)
    try:
        # Do not stop anything unless the project DB and run label match.
        docker.owned()
        docker.call("stop")
        state["stopped"] = True
    except Exception:
        state["cleanup_requires_review"] = True
    write_json(folder / "run.json", state)


def docker_stop(run_id):
    folder, state = load(run_id)
    if state["backend"] != "docker" or state["phase"] == "new": raise ValueError("owned_run_required")
    docker = Docker(folder, state)
    docker.owned()
    docker.call("stop")  # never down -v, rm, database DROP or shared-engine reset
    state["stopped"] = True
    write_json(folder / "run.json", state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["init", "up", "stop", "native-test"])
    parser.add_argument("--run")
    parser.add_argument("--postgres-bin", default=r"C:\Program Files\PostgreSQL\18\bin")
    parser.add_argument("--identity-only", action="store_true", help="Diagnostic mode: tenants remain provisioning; not full readiness")
    args = parser.parse_args()
    try:
        if args.operation == "init": initialize()
        elif args.operation == "up": docker_up(args.run)
        elif args.operation == "stop": docker_stop(args.run)
        else: native_test(args.run, args.postgres_bin, args.identity_only)
    except Exception:
        raise SystemExit("Phase 2A operation failed; generated state retained. No secrets or external targets printed.") from None
