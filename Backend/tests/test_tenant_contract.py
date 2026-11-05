import copy
from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.credentials import CredentialStore
from app.models import Base, Department, DepartmentDatabase
from app.provisioning.contract import TenantContract
from app.provisioning.errors import ProvisioningError
from app.provisioning.tenant_migrations import TenantMigrator
from app.provisioning.worker import Worker
from contracts.document_api_draft import QuestionRequest, AnswerResponse

ROOT = Path(__file__).resolve().parents[2]


def payload():
    return {"contract_version": 1, "ready": True, "required_revision": "contract_test_0002",
        "required_tables": ["_provisioning_identity", "contract_records", "contract_events"],
        "table_privileges": {"contract_records": ["SELECT", "INSERT", "UPDATE"], "contract_events": ["SELECT", "INSERT"]},
        "sequence_privileges": {"contract_records_id_seq": ["USAGE", "SELECT"], "contract_events_id_seq": ["USAGE"]}}


def load(tmp_path, data):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return TenantMigrator.from_contract(path)


def test_schema_export_and_unavailable_example_are_consistent():
    schema = TenantContract.model_json_schema()
    schema["$id"] = "urn:ai-platform:tenant-contract:1"
    assert json.loads((ROOT / "Backend/contracts/tenant-v1.schema.json").read_text()) == schema
    example = TenantMigrator.from_contract(ROOT / "Backend/tenant-contract.example.json")
    assert not example.available and example.error_code == "tenant_contract_unavailable"


def test_semantic_hash_ignores_order_notes_and_mutable_aliases(tmp_path):
    data = payload()
    first = load(tmp_path, data)
    data["required_tables"].reverse()
    for operations in data["table_privileges"].values(): operations.reverse()
    data["sequence_privileges"]["contract_records_id_seq"].reverse()
    data["pending"] = "Review note only"
    second = load(tmp_path, data)
    assert first.available and first.contract_id == second.contract_id
    leaked = second.runtime_privileges()
    leaked["table_privileges"]["contract_events"].append("DELETE")
    assert "DELETE" not in second.runtime_privileges()["table_privileges"]["contract_events"]
    assert second.contract_id == first.contract_id


@pytest.mark.parametrize("change", ["table", "sequence", "revision", "required_table"])
def test_security_relevant_changes_change_hash(tmp_path, change):
    data = payload()
    first = load(tmp_path, data)
    if change == "table": data["table_privileges"]["contract_records"].remove("UPDATE")
    if change == "sequence": data["sequence_privileges"]["contract_records_id_seq"].remove("SELECT")
    if change == "revision": data["required_revision"] = "contract_test_0003"
    if change == "required_table":
        data["required_tables"].append("contract_extra")
        data["table_privileges"]["contract_extra"] = ["SELECT"]
    second = load(tmp_path, data)
    assert second.available and second.contract_id != first.contract_id


@pytest.mark.parametrize("case", ["missing_version", "future_version", "bool_version", "string_version", "string_ready",
    "unknown_secret", "path", "missing_policy", "duplicate_tables", "duplicate_ops", "unsafe_name", "global_capability",
    "protected_policy", "sequence_update", "no_identity", "head_alias", "foundation", "no_read", "missing_table_policy"])
def test_invalid_contracts_are_unavailable_without_echoing_input(tmp_path, case):
    data = payload()
    if case == "missing_version": data.pop("contract_version")
    if case == "future_version": data["contract_version"] = 2
    if case == "bool_version": data["contract_version"] = True
    if case == "string_version": data["contract_version"] = "1"
    if case == "string_ready": data["ready"] = "true"
    if case == "unknown_secret": data["password"] = "PRIVATE_CONTRACT_CANARY"
    if case == "path": data["script_location"] = "PRIVATE_CONTRACT_CANARY"
    if case == "missing_policy": data.pop("table_privileges")
    if case == "duplicate_tables": data["required_tables"].append("contract_records")
    if case == "duplicate_ops": data["table_privileges"]["contract_records"].append("SELECT")
    if case == "unsafe_name": data["table_privileges"]["bad;name"] = ["SELECT"]
    if case == "global_capability": data["table_privileges"]["contract_records"] = ["SUPERUSER"]
    if case == "protected_policy": data["table_privileges"]["_provisioning_identity"] = ["UPDATE"]
    if case == "sequence_update": data["sequence_privileges"]["contract_records_id_seq"] = ["UPDATE"]
    if case == "no_identity": data["required_tables"].remove("_provisioning_identity")
    if case == "head_alias": data["required_revision"] = "head"
    if case == "foundation": data["required_revision"] = "tenant_0001"
    if case == "no_read": data["table_privileges"]["contract_events"] = ["INSERT"]
    if case == "missing_table_policy": data["table_privileges"].pop("contract_events")
    contract = load(tmp_path, data)
    assert not contract.available
    assert contract.error_code in {"tenant_contract_invalid", "tenant_contract_version_unsupported", "runtime_grant_policy_invalid"}
    assert "PRIVATE_CONTRACT_CANARY" not in contract.error_code


@pytest.mark.parametrize("raw", ['{"contract_version":1,"contract_version":2}', '{"PRIVATE_CANARY":', ' ' * 65537])
def test_malformed_duplicate_and_oversized_contracts_fail_closed(tmp_path, raw):
    path = tmp_path / "invalid.json"
    path.write_text(raw, encoding="utf-8")
    contract = TenantMigrator.from_contract(path)
    assert not contract.available and contract.error_code == "tenant_contract_invalid"


def test_unreadable_contract_fails_closed(tmp_path):
    contract = TenantMigrator.from_contract(tmp_path / "missing.json")
    assert not contract.available and contract.error_code == "tenant_contract_unreadable"


@contextmanager
def unlocked(*args):
    yield True


@pytest.fixture
def worker(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        s.add(Department(id="synthetic_contract", name="Synthetic Contract", status="provisioning"))
        s.add(DepartmentDatabase(department_id="synthetic_contract", database_name="dept_" + "b" * 32,
            credential_key="DEPT_" + "B" * 32, status="provisioning"))
    migrator = load(tmp_path, payload())
    migrator.migrate = Mock(return_value="contract_test_0002")
    pg = Mock()
    result = Worker(engine, pg, CredentialStore(tmp_path / "credentials"), migrator, lock=unlocked)
    yield result, pg
    engine.dispose()


def test_worker_forwards_identical_policy_to_grant_and_verify(worker):
    w, pg = worker
    assert w.run("synthetic_contract") == "active"
    assert pg.grant_runtime.call_args.kwargs == w.migrator.runtime_privileges()
    assert pg.verify.call_args.kwargs == pg.grant_runtime.call_args.kwargs
    with Session(w.engine) as s:
        assert s.scalar(select(DepartmentDatabase)).verified_contract == w.migrator.contract_id


def test_changed_policy_forces_revalidation_but_reordered_policy_is_noop(worker, tmp_path):
    w, pg = worker
    assert w.run("synthetic_contract") == "active"
    assert w.run("synthetic_contract") == "active"
    assert pg.verify.call_count == 1
    data = payload()
    data["table_privileges"]["contract_records"].remove("UPDATE")
    w.migrator = load(tmp_path, data)
    w.migrator.migrate = Mock(return_value="contract_test_0002")
    assert w.run("synthetic_contract") == "active"
    assert pg.verify.call_count == 2


def test_bad_contract_revokes_existing_readiness_without_new_resources(worker, tmp_path):
    w, pg = worker
    assert w.run("synthetic_contract") == "active"
    w.migrator = load(tmp_path, {**payload(), "contract_version": 2})
    assert w.run("synthetic_contract") == "failed"
    assert pg.ensure_resources.call_count == 1
    with Session(w.engine) as s:
        row = s.scalar(select(DepartmentDatabase))
        assert row.verified_contract is None and row.last_error_code == "tenant_contract_version_unsupported"


def test_failed_grant_verification_never_sets_active(worker):
    w, pg = worker
    pg.verify.side_effect = ProvisioningError("runtime_permission_mismatch")
    assert w.run("synthetic_contract") == "failed"
    with Session(w.engine) as s:
        row = s.scalar(select(DepartmentDatabase))
        assert row.status == "failed" and row.verified_contract is None


def test_draft_api_examples_are_valid_and_do_not_trust_identity_or_cost():
    sample = json.loads((ROOT / "Backend/contracts/document-api-examples.json").read_text())
    QuestionRequest.model_validate(sample["request"])
    AnswerResponse.model_validate(sample["response"])
    for key in ("user_id", "role", "cost_micro_usd", "claim_id"):
        with pytest.raises(ValidationError):
            QuestionRequest.model_validate({**sample["request"], key: "untrusted"})
    with pytest.raises(ValidationError):
        AnswerResponse.model_validate({**sample["response"], "sources": []})
    with pytest.raises(ValidationError):
        AnswerResponse.model_validate({**sample["response"], "context_used": ["raw private text"]})
