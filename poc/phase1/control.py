"""Fresh PoC infrastructure only. No existing DB URLs, ports, secrets or volumes."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
import psycopg
from dataset import ROOT, write_json
from environment import seed, verify

REPO = ROOT.parents[1]
STATE = REPO / ".local/poc-phase1"
DATABASES = {"poc_registry", "poc_experimental"}
ROLES = {"bootstrap": "poc_bootstrap", "registry": "poc_registry_app", "tenant": "poc_tenant_app"}


def clean_environment():
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.startswith(("PG", "DATABASE", "CENTRAL_", "PROVISIONING_", "DEPARTMENT_", "POC_", "DOCKER_")) or upper.endswith(("_PASSWORD", "_TOKEN", "_SECRET", "_API_KEY")):
            env.pop(key)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    return env


def command(args, *, input=None, env=None, timeout=60):
    # Descendant services on Windows may retain pipe handles after the CLI exits.
    # Anonymous temporary output files keep timeout/parent completion bounded.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        options = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
        result = subprocess.run([str(a) for a in args], env=env or clean_environment(),
                                stdout=output, stderr=error, timeout=timeout, **options)
        output.seek(0)
        error.seek(0)
        return subprocess.CompletedProcess(result.args, result.returncode, output.read(), error.read())


def checked(args, **kwargs):
    result = command(args, **kwargs)
    if result.returncode:
        raise RuntimeError("PoC infrastructure command failed (private logs not printed)")
    return result.stdout


def private_file(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        stream.write(value)


def initialize():
    # Private files are generated from scratch; no user or company secret is read.
    run_id = uuid.uuid4().hex
    if STATE.is_symlink() or (REPO / ".local").is_symlink():
        raise ValueError("Private state root must not be a symlink")
    folder = STATE / run_id
    folder.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        checked(["pwsh", "-NoProfile", "-File", REPO / "Backend/tools/restrict-test-directory.ps1", "-Path", folder])
    else:
        folder.mkdir(mode=0o700)
    data = json.loads((ROOT / "ground-truth.json").read_text(encoding="utf-8"))
    passwords = {k: secrets.token_urlsafe(36) for k in ROLES}
    passwords["accounts"] = {u["id"]: secrets.token_urlsafe(24) for u in data["users"]}
    private_file(folder / "private-accounts.json", json.dumps(passwords))
    for key in ROLES:
        private_file(folder / (key + "-password"), passwords[key])
    private_file(folder / "empty.env", "")
    (folder / "docker-config").mkdir(mode=0o700)
    private_file(folder / "docker-config/config.json", '{"auths":{}}')
    write_json(folder / "run.json", dict(run_id=run_id, project="ai-poc1-" + run_id,
                                        phase="new", backend=None))
    print("New private PoC run:", run_id)
    print("State and generated credentials retained privately:", folder)


def load(run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Generated 32-character run ID required")
    folder = STATE / run_id
    if STATE.is_symlink() or (REPO / ".local").is_symlink() or folder.is_symlink() or folder.resolve().parent != STATE.resolve():
        raise ValueError("Invalid state directory")
    for name in ["run.json", "private-accounts.json", "bootstrap-password", "registry-password", "tenant-password", "empty.env"]:
        if (folder / name).is_symlink():
            raise ValueError("State files must not be symlinks")
    state = json.loads((folder / "run.json").read_text())
    if state["run_id"] != run_id or state["project"] != "ai-poc1-" + run_id:
        raise ValueError("PoC state identity mismatch")
    return folder, state


def fixtures():
    return json.loads((ROOT / "ground-truth.json").read_text(encoding="utf-8"))


class Docker:
    def __init__(self, folder, state):
        self.folder, self.state = folder, state
        self.env = clean_environment()
        endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine" if os.name == "nt" else "unix:///var/run/docker.sock"
        client_dir = folder / "docker-config"
        if client_dir.is_symlink() or (client_dir / "config.json").is_symlink():
            raise ValueError("Private Docker config must not be symlinked")
        client_dir.mkdir(mode=0o700, exist_ok=True)
        config = {"auths": {}}
        executable = shutil.which("docker")
        if executable:
            plugins = Path(executable).resolve().parent.parent / "cli-plugins"
            if plugins.is_dir():
                config["cliPluginsExtraDirs"] = [str(plugins)]
        write_json(client_dir / "config.json", config)
        # Explicit local endpoint and fresh empty client config: never load the
        # operator's Docker contexts, registry credentials or global settings.
        self.prefix = ["docker", "--config", client_dir, "--host", endpoint]
        if checked(self.prefix + ["info", "--format", "{{.OSType}}"], timeout=15).strip() != b"linux":
            raise RuntimeError("PoC requires a ready local Linux Docker engine")
        self.env.update(POC_PROJECT=state["project"], POC_RUN_ID=state["run_id"],
                        POC_BOOTSTRAP_FILE=str(folder / "bootstrap-password"),
                        POC_REGISTRY_FILE=str(folder / "registry-password"),
                        POC_TENANT_FILE=str(folder / "tenant-password"))
        self.compose = self.prefix + ["compose", "--env-file", folder / "empty.env", "-f", ROOT / "compose.yml", "--project-name", state["project"]]

    def call(self, *args, **kwargs):
        return checked(self.compose + list(args), env=self.env, **kwargs)

    def owned_container(self):
        identifiers = self.call("ps", "-aq", "postgres").decode().split()
        if len(identifiers) != 1:
            raise RuntimeError("Expected one owned PoC container")
        label = checked(self.prefix + ["inspect", "--format", '{{index .Config.Labels "ai.poc.phase1.run_id"}}', identifiers[0]])
        if label.decode().strip() != self.state["run_id"]:
            raise RuntimeError("Container ownership mismatch")
        return identifiers[0]

    def execute(self, db, query, identity="bootstrap"):
        if db not in DATABASES or identity not in ROLES:
            raise ValueError("Unmanaged PoC identity")
        if identity == "bootstrap":
            args = ["exec", "-T", "postgres", "psql", "-X", "-qAt", "-U", ROLES[identity], "-d", db]
        else:
            # Password remains inside a generated secret file, never CLI/env output.
            shell = (f'export PGPASSWORD="$(cat /run/secrets/{identity}_password)"; '
                     f'exec psql -X -qAt -h 127.0.0.1 -U {ROLES[identity]} -d {db} -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate')
            args = ["exec", "-T", "postgres", "sh", "-c", shell]
        result = command(self.compose + args + ["-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate"], input=query.encode(), env=self.env)
        if result.returncode:
            if b"42501" in result.stderr or b"permission denied for database" in result.stderr:
                raise PermissionError("PoC permission denied")
            raise RuntimeError("PoC SQL failed; statement/credentials withheld")
        return result.stdout.decode().strip()


def docker_up(folder, state):
    if state["phase"] != "new":
        raise RuntimeError("Use a new run; refusing reused infrastructure")
    docker = Docker(folder, state)
    for resource, flags in [("ps", ["-aq"]), ("volume", ["ls", "-q"]), ("network", ["ls", "-q"])]:
        existing = checked(docker.prefix + [resource] + flags + ["--filter", "label=com.docker.compose.project=" + state["project"]])
        if existing.strip():
            raise RuntimeError("Refusing project collision")
    state.update(backend="docker", phase="starting")
    write_json(folder / "run.json", state)
    docker.call("up", "-d", "postgres", timeout=180)
    owned = docker.owned_container()
    for _ in range(60):
        health = checked(docker.prefix + ["inspect", "--format", "{{.State.Health.Status}}", owned]).strip()
        if health == b"healthy":
            break
        time.sleep(1)
    else:
        raise RuntimeError("PoC PostgreSQL did not become healthy")
    # Snapshot this new empty cluster before any role/schema changes.
    (folder / "before.dump").write_bytes(docker.call("exec", "-T", "postgres", "pg_dump", "-U", "poc_bootstrap", "-d", "poc_registry", "-Fc"))
    (folder / "globals-before.sql").write_bytes(docker.call("exec", "-T", "postgres", "pg_dumpall", "-U", "poc_bootstrap", "--globals-only", "--no-role-passwords"))
    (folder / "owners-acls-before.json").write_text(docker.execute("poc_registry", "SELECT json_agg(json_build_array(datname,pg_get_userbyid(datdba),datacl::text)) FROM pg_database"))
    passwords = json.loads((folder / "private-accounts.json").read_text())
    seed(docker.execute, fixtures(), state["run_id"], passwords)
    write_json(folder / "verification.json", verify(docker.execute, fixtures(), state["run_id"]))
    state["phase"] = "ready"
    write_json(folder / "run.json", state)
    print("PoC ready and verified; no published ports, no model calls. Run:", state["run_id"])


def docker_stop(folder, state):
    if state["backend"] != "docker":
        raise RuntimeError("This run does not own Docker infrastructure")
    docker = Docker(folder, state)
    docker.owned_container()
    docker.call("stop", "postgres")
    state["phase"] = "stopped"
    write_json(folder / "run.json", state)
    print("Stopped only this run's PostgreSQL; container, volume and recovery artifacts retained")


def native_test(folder, state, postgres_bin):
    if state["phase"] != "new":
        raise RuntimeError("Native verification requires a new run")
    binaries = Path(postgres_bin)
    suffix = ".exe" if os.name == "nt" else ""
    def pg(name, *args, **kwargs):
        if name == "pg_ctl":
            # A background postmaster can retain inherited pipe handles on
            # Windows. Use a private file and closed stdin, not captured pipes.
            with (folder / "service-control.log").open("ab") as output:
                result = subprocess.run([str(binaries / (name + suffix)), *map(str, args)],
                                        stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                        env=clean_environment(), timeout=60)
            if result.returncode:
                raise RuntimeError("Owned PoC service-control command failed")
            return b""
        return checked([binaries / (name + suffix), *args], **kwargs)
    passwords = json.loads((folder / "private-accounts.json").read_text())
    env = clean_environment()
    env["PGPASSWORD"] = passwords["bootstrap"]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    if port == 5435:
        raise RuntimeError("Refusing development port")
    data_dir = folder / "native-data"
    if data_dir.exists():
        raise RuntimeError("Refusing existing native data")
    state.update(backend="native", phase="starting", port=port)
    write_json(folder / "run.json", state)
    pg("initdb", "-D", data_dir, "-U", "poc_bootstrap", "--pwfile", folder / "bootstrap-password",
       "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", "--encoding=UTF8")
    started = False
    def execute(db, query, identity="bootstrap"):
        if db not in DATABASES or identity not in ROLES:
            raise ValueError("Unmanaged PoC connection")
        try:
            with psycopg.connect(host="127.0.0.1", port=port, dbname=db, user=ROLES[identity],
                                 password=passwords[identity], autocommit=True, connect_timeout=5) as connection:
                cursor = connection.execute(query)
                if cursor.description:
                    value = cursor.fetchone()[0]
                    if isinstance(value, bool): return "t" if value else "f"
                    if isinstance(value, (dict, list)): return json.dumps(value)
                    return str(value)
                return ""
        except psycopg.Error as error:
            if error.sqlstate == "42501" or "permission denied for database" in str(error):
                raise PermissionError("PoC permission denied") from None
            raise RuntimeError("PoC SQL failed; statement/credentials withheld") from None
    try:
        started = True
        pg("pg_ctl", "-D", data_dir, "-l", folder / "postgres.log", "-o", f"-h 127.0.0.1 -p {port}", "-w", "start")
        args = ("-h", "127.0.0.1", "-p", str(port), "-U", "poc_bootstrap")
        pg("createdb", *args, "poc_registry", env=env)
        pg("pg_dump", *args, "-d", "poc_registry", "-Fc", "-f", folder / "before.dump", env=env)
        pg("pg_dumpall", *args, "--globals-only", "--no-role-passwords", "-f", folder / "globals-before.sql", env=env)
        (folder / "owners-acls-before.json").write_text(execute("poc_registry", "SELECT json_agg(json_build_array(datname,pg_get_userbyid(datdba),datacl::text)) FROM pg_database"))
        seed(execute, fixtures(), state["run_id"], passwords)
        report = verify(execute, fixtures(), state["run_id"])
        report["backend"] = "native"
        write_json(folder / "verification.json", report)
        print("Native fresh-cluster Phase 1 setup verification passed")
    finally:
        if started:
            pg("pg_ctl", "-D", data_dir, "-m", "fast", "-w", "stop")
        state["phase"] = "stopped"
        write_json(folder / "run.json", state)
        print("Stopped only the new native cluster; retained private data and recovery artifacts:", folder)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "verify", "stop", "native-test"])
    parser.add_argument("--run")
    parser.add_argument("--postgres-bin", default=r"C:\Program Files\PostgreSQL\18\bin")
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialize()
        else:
            folder, state = load(args.run or "")
            if args.action == "up": docker_up(folder, state)
            elif args.action == "stop": docker_stop(folder, state)
            elif args.action == "native-test": native_test(folder, state, args.postgres_bin)
            else:
                if state["backend"] != "docker" or state["phase"] != "ready":
                    raise RuntimeError("Expected a ready owned Docker run")
                docker = Docker(folder, state)
                docker.owned_container()
                write_json(folder / "verification.json", verify(docker.execute, fixtures(), state["run_id"]))
                print("Owned Docker PoC setup verified")
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        # No raw command, SQL, password, token or exception details in CLI output.
        print("PoC operation failed safely; no existing application resources were selected. Check prerequisites and private run state.")
        raise SystemExit(1)
