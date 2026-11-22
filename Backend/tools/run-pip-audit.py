"""Audit the current Python environment with pip-audit and an expiring waiver list.

Fails (exit 1) on any known vulnerability that is not covered by a valid, unexpired waiver in
Backend/security/audit-waivers.json. A waiver needs an id (any alias of the advisory counts), the package, a
reason and an expiry date, so an accepted risk cannot silently become permanent. Exit 2 means the audit itself
could not run (for example no network); that is never reported as a pass.

Usage: python Backend/tools/run-pip-audit.py        (run inside the environment to audit)
"""
import argparse
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

WAIVERS = Path(__file__).resolve().parents[1] / "security" / "audit-waivers.json"
REQUIRED = ("id", "package", "reason", "expires")


def load_waivers(path, today):
    """Return (waivers, problems). Expired or malformed waivers are problems, never silently ignored."""
    problems, waivers = [], []
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ["waiver file is missing or is not valid JSON"]
    if not isinstance(entries, list):
        return [], ["waiver file must contain a JSON list"]
    for index, entry in enumerate(entries):
        label = f"waiver #{index + 1}"
        if not isinstance(entry, dict) or any(not str(entry.get(key, "")).strip() for key in REQUIRED):
            problems.append(f"{label} needs non-empty {', '.join(REQUIRED)}")
            continue
        try:
            expires = date.fromisoformat(entry["expires"])
        except ValueError:
            problems.append(f"{label} ({entry['id']}) has an invalid expiry date")
            continue
        if expires < today:
            problems.append(f"{label} ({entry['id']}) expired on {entry['expires']}")
            continue
        waivers.append(entry)
    return waivers, problems


def evaluate(report, waivers):
    """Return (blocking, waived_ids_used). report is pip-audit's JSON document."""
    waived = {}
    for entry in waivers:
        waived[entry["id"]] = entry
    blocking, used = [], set()
    for dependency in report.get("dependencies", []):
        for vulnerability in dependency.get("vulns", []):
            ids = {vulnerability.get("id", "")} | set(vulnerability.get("aliases", []))
            match = next((waived[i] for i in ids if i in waived and waived[i]["package"] == dependency["name"]), None)
            if match:
                used.add(match["id"])
                continue
            blocking.append((dependency["name"], dependency["version"], vulnerability.get("id", "?"),
                             ",".join(vulnerability.get("fix_versions", [])) or "no fix yet"))
    return blocking, used


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--waivers", default=str(WAIVERS))
    args = parser.parse_args(argv)
    waivers, problems = load_waivers(args.waivers, date.today())
    process = subprocess.run([sys.executable, "-m", "pip_audit", "--progress-spinner", "off", "--format", "json"],
                             capture_output=True, text=True)
    try:
        report = json.loads(process.stdout)
    except ValueError:
        print("pip-audit could not complete (no valid report); this is not a pass.")
        return 2
    blocking, used = evaluate(report, waivers)
    for problem in problems:
        print("WAIVER PROBLEM:", problem)
    unused = [w["id"] for w in waivers if w["id"] not in used]
    if unused:
        print("note: waivers no longer matching a finding (remove them):", ", ".join(unused))
    for name, version, identifier, fix in blocking:
        print(f"VULNERABLE: {name} {version} {identifier} (fix: {fix})")
    audited = len(report.get("dependencies", []))
    if blocking or problems:
        print(f"FAILED: {len(blocking)} unwaived finding(s), {len(problems)} waiver problem(s) across {audited} packages")
        return 1
    print(f"OK: {audited} packages audited, no unwaived vulnerabilities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
