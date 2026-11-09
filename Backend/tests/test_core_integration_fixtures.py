"""Synthetic partner-facing manifest examples, not an approved document schema."""
import copy
import json
from pathlib import Path

import pytest

from app.provisioning.tenant_migrations import TenantMigrator

CASES = json.loads((Path(__file__).parent / "fixtures/core_tenant_contract_cases.json").read_text())


def read(tmp_path, data):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return TenantMigrator.from_contract(path)


def test_accepted_manifest_has_explicit_policy_but_is_not_proof_of_migration(tmp_path):
    migrator = read(tmp_path, CASES["accepted"])
    assert migrator.available
    assert migrator.required_revision == "contract_test_0002"
    assert migrator.runtime_privileges() == {
        "table_privileges": {"contract_records": ["INSERT", "SELECT"]},
        "sequence_privileges": {"contract_records_id_seq": ["USAGE"]}}


@pytest.mark.parametrize("case", CASES["rejected"], ids=lambda case: case["name"])
def test_partner_manifest_fail_closed_cases(tmp_path, case):
    data = copy.deepcopy(CASES["accepted"])
    data.update(case.get("set", {}))
    if "remove" in case:
        data.pop(case["remove"])
    migrator = read(tmp_path, data)
    assert not migrator.available
    assert migrator.error_code in {"tenant_contract_invalid", "tenant_contract_unavailable",
                                    "runtime_grant_policy_invalid"}
