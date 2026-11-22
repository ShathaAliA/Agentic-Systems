"""Supply-chain controls: audit waiver logic and structural rules for workflows and Dependabot."""
from datetime import date
import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_pip_audit", ROOT / "Backend/tools/run-pip-audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
TODAY = date(2026, 11, 22)


def report(*findings):
    """findings: (name, version, id, aliases, fix_versions)."""
    deps = {}
    for name, version, identifier, aliases, fixes in findings:
        deps.setdefault((name, version), []).append(dict(id=identifier, aliases=aliases, fix_versions=fixes))
    return {"dependencies": [dict(name=n, version=v, vulns=vs) for (n, v), vs in deps.items()] +
            [dict(name="clean", version="1", vulns=[])]}


def waiver(**changes):
    return {"id": "CVE-1", "package": "pkg", "reason": "not reachable", "expires": "2026-12-31", **changes}


def write(tmp_path, entries):
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_unwaived_findings_block_and_clean_reports_pass():
    blocking, used = audit.evaluate(report(("pkg", "1.0", "CVE-1", ["GHSA-x"], ["1.1"])), [])
    assert blocking == [("pkg", "1.0", "CVE-1", "1.1")] and used == set()
    assert audit.evaluate(report(), [])[0] == []
    assert audit.evaluate(report(("pkg", "1.0", "CVE-9", [], [])), [])[0][0][3] == "no fix yet"


def test_valid_waiver_matches_by_id_or_alias_and_only_for_its_package():
    entry = waiver()
    blocking, used = audit.evaluate(report(("pkg", "1.0", "PYSEC-1", ["CVE-1"], [])), [entry])
    assert blocking == [] and used == {"CVE-1"}
    other_package = audit.evaluate(report(("other", "1.0", "CVE-1", [], [])), [entry])
    assert len(other_package[0]) == 1 and other_package[1] == set()


@pytest.mark.parametrize("entry,expected", [
    (waiver(expires="2026-11-21"), "expired on 2026-11-21"),
    (waiver(expires="soon"), "invalid expiry date"),
    (waiver(reason=" "), "needs non-empty"),
    ({"id": "CVE-1"}, "needs non-empty")])
def test_expired_or_malformed_waivers_are_problems_not_silent_passes(tmp_path, entry, expected):
    waivers, problems = audit.load_waivers(write(tmp_path, [entry]), TODAY)
    assert waivers == [] and expected in problems[0]


def test_waiver_file_errors_fail_closed(tmp_path):
    assert audit.load_waivers(tmp_path / "missing.json", TODAY)[1]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert audit.load_waivers(bad, TODAY)[1]
    assert audit.load_waivers(write(tmp_path, {"not": "a list"}), TODAY)[1]


def test_committed_waiver_file_is_valid_and_has_no_expired_entries():
    waivers, problems = audit.load_waivers(audit.WAIVERS, date.today())
    assert problems == [], problems


def workflows():
    return sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))


@pytest.mark.parametrize("path", workflows(), ids=lambda p: p.name)
def test_workflows_pin_actions_by_sha_and_stay_least_privilege(path):
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"^\s*-?\s*uses:\s*(\S+)", text, re.M):
        assert re.search(r"@[0-9a-f]{40}$", match.group(1)), f"{path.name}: unpinned action {match.group(1)}"
    document = yaml.load(text, Loader=yaml.BaseLoader)
    triggers = document.get("on") or document.get(True) or {}
    assert "pull_request_target" not in triggers
    assert document["permissions"] == {"contents": "read"}


def test_security_workflow_runs_both_scans_and_gitleaks_is_checksum_verified():
    text = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    document = yaml.load(text, Loader=yaml.BaseLoader)
    assert set(document["jobs"]) == {"secret-scan", "dependency-audit"}
    assert "fetch-depth: 0" in text and "sha256sum -c" in text and "--redact" in text
    assert "run-pip-audit.py" in text and "pip-audit==" in text and "--upgrade pip" in text


def test_dependabot_covers_python_actions_and_docker_against_backend():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {u["package-ecosystem"]: u for u in config["updates"]}
    assert set(ecosystems) == {"pip", "github-actions", "docker"}
    assert all(u["target-branch"] == "Backend" and u["schedule"]["interval"] == "weekly" for u in ecosystems.values())
