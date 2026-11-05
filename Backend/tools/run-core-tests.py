"""One reproducible, isolated Core test entry point. No application imports."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
GROUPS = ("authentication", "departments", "users", "role_policies", "usage", "core_access", "core_lifecycle")


def clean_environment(source):
    result = dict(source)
    for key in list(result):
        upper = key.upper()
        if (upper.startswith(("RUN_", "AUTH_TEST_", "DEV_", "PG", "OPENAI", "GEMINI", "AZURE", "DATABASE_", "CENTRAL_MIGRATION_", "PROVISIONING_", "DEPARTMENT_", "PYTEST_"))
                or upper in {"DB_HOST", "DB_PORT", "AUTH_MODE", "APP_ENV", "PYTHONPATH", "TENANT_CONTRACT_FILE"}
                or upper.endswith(("_API_KEY", "_PASSWORD", "_TOKEN", "_SECRET"))):
            result.pop(key)
    result.update(APP_ENV="test", AUTH_MODE="disabled", PYTHONIOENCODING="utf-8", PYTHON_DOTENV_DISABLED="1")
    return result


def redact(value):
    value = re.sub(r"dev_[A-Za-z0-9_-]{43}", "[REDACTED_TOKEN]", value)
    value = re.sub(r"postgres(?:ql)?(?:\+\w+)?://[^\s<>\"']+", "[REDACTED_DSN]", value, flags=re.I)
    value = re.sub(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}", "[REDACTED_KEY]", value)
    value = re.sub(r"(?i)((?:password|api_key|access_token)[\"']?\s*[:=]\s*[\"'])[^\"'\r\n]+", r"\1[REDACTED]", value)
    return value


def restrict_directory(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["pwsh", "-NoProfile", "-File", str(ROOT / "Backend" / "tools" / "restrict-test-directory.ps1"),
                        "-Path", str(path)], check=True, capture_output=True)
    else:
        path.mkdir(mode=0o700)


def result_counts(xml):
    root = ET.fromstring(xml)
    suites = list(root.iter("testsuite"))
    if not suites:
        raise ValueError("Missing test-suite results")
    return {key: sum(int(s.get(key, "0")) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")}


def private_xml_from_output(output):
    matches = re.findall(r"(?:Retained isolated cluster and private recovery artifacts:|Private recovery artifacts:) ([^\r\n]+)", output)
    if not matches:
        raise ValueError("Missing isolated runner recovery path")
    state = Path(matches[-1].strip()).resolve()
    allowed = [(ROOT / ".local" / part).resolve() for part in ("native-auth-tests", "auth-tests")]
    if not any(parent in state.parents for parent in allowed):
        raise ValueError("Unexpected test result directory")
    return state / "results.xml"


def record_result(name, process, xml, directory, *, isolated=False):
    # Output is held in memory until redacted. Original private cluster reports
    # stay private; only redacted copies belong to the aggregate report.
    output = redact(process.stdout or "")
    (directory / (name + ".log")).write_text(output, encoding="utf-8")
    counts = None
    try:
        content = redact(xml.read_text(encoding="utf-8"))
        counts = result_counts(content)
        (directory / (name + ".xml")).write_text(content, encoding="utf-8")
        successful = (process.returncode == 0 and counts["tests"] > counts["skipped"]
                      and not counts["failures"] and not counts["errors"]
                      and (not isolated or not counts["skipped"]))
        status = "passed" if successful else "failed"
    except (OSError, ValueError, ET.ParseError):
        status = "blocked"
    print(f"{name}: {status}" + (f" {counts}" if counts else " (no valid result report)"), flush=True)
    if status != "passed":
        print(output[-6000:], flush=True)
    return {"name": name, "status": status, "exit_code": process.returncode, "counts": counts}


def run(args):
    report = ROOT / ".local" / "core-tests" / uuid.uuid4().hex
    restrict_directory(report)
    environment = clean_environment(os.environ)
    results = []
    jobs = []
    if args.suite in {"all", "unit"}:
        xml = report / "unit.xml"
        jobs.append(("unit", [sys.executable, "-m", "pytest", "-q", "Backend/tests", "-m", "not integration",
                              "-p", "no:cacheprovider", "--junitxml=" + str(xml), "--tb=short"], xml, False))
    if args.suite in {"all", "integration"}:
        script = "test-auth-postgres-native.ps1" if args.backend == "native" else "test-auth-postgres.ps1"
        for group in GROUPS:
            command = [args.pwsh, "-NoProfile", "-File", str(ROOT / "Backend" / "tools" / script),
                       "-TestFile", f"tests/test_{group}_postgres.py"]
            if args.backend == "native":
                command += ["-Python", sys.executable, "-PostgresBin", args.postgres_bin]
            else:
                command += ["-Docker", args.docker, "-Image", args.image]
            jobs.append((group, command, None, True))
    for name, command, xml, isolated in jobs:
        print(f"Starting {name} ({args.backend if isolated else 'in-memory'})", flush=True)
        try:
            process = subprocess.run(command, cwd=ROOT, env=environment,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace")
            if isolated:
                try:
                    xml = private_xml_from_output(process.stdout)
                except ValueError:
                    xml = report / "missing-result.xml"
            results.append(record_result(name, process, xml, report, isolated=isolated))
        except OSError:
            # No exception text, environment or command dump can expose a secret.
            print(f"{name}: blocked (test executable unavailable)", flush=True)
            results.append({"name": name, "status": "blocked", "counts": None})
    successful = bool(results) and all(r["status"] == "passed" for r in results)
    summary = {"status": "passed" if successful else "failed", "backend": args.backend,
               "live_provisioning": "excluded", "results": results}
    (report / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Overall: {summary['status']}; redacted reports: {report}", flush=True)
    return 0 if successful else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "unit", "integration"), default="all")
    parser.add_argument("--backend", choices=("native", "docker"), default="native")
    parser.add_argument("--pwsh", default="pwsh")
    parser.add_argument("--postgres-bin", default=r"C:\Program Files\PostgreSQL\18\bin")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--image", default="ai-platform-day2:dev")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
