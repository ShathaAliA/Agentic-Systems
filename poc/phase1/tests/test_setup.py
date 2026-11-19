import hashlib
import json
from pathlib import Path
import pytest
import pymupdf
from docx import Document
from openpyxl import load_workbook
import yaml
from dataset import ROOT, manifest, allowed
from control import load, clean_environment
from environment import seed

DATA = json.loads((ROOT / "ground-truth.json").read_text(encoding="utf-8"))
DOCS = {row["artifact_id"]: row for row in DATA["documents"]}
USERS = {row["id"]: row for row in DATA["users"]}


def test_authored_fixture_is_current_and_complete():
    expected = manifest()
    actual = json.loads(json.dumps(DATA))
    for doc in actual["documents"]:
        doc.pop("sha256")
        doc.pop("byte_size")
    assert actual == expected
    assert len(DOCS) == 7 and len(USERS) == 5
    assert [c["id"] for c in DATA["test_cases"]] == [f"TC{n:02}" for n in range(1, 21)]
    assert all(c["execution_status"] == "not_run_phase1" for c in DATA["test_cases"])


@pytest.mark.parametrize("artifact", list(DOCS))
def test_seven_real_files_and_hashes(artifact):
    doc = DOCS[artifact]
    path = ROOT / doc["file"]
    assert path.is_file() and path.resolve().parent == (ROOT / "documents").resolve()
    assert path.stat().st_size == doc["byte_size"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == doc["sha256"]
    if path.suffix == ".pdf":
        with pymupdf.open(path) as pdf:
            assert len(pdf) == 1
            if doc.get("scanned"):
                assert pdf[0].get_text() == "" and pdf[0].get_images()
            else:
                assert pdf[0].get_text().strip()
    elif path.suffix == ".docx":
        assert Document(path).paragraphs[1].text == doc["expected_text"]
    elif path.suffix == ".xlsx":
        book = load_workbook(path, read_only=True)
        try:
            sheet = book["البدلات"]
            assert sheet["A2"].value == "مدينة س" and sheet["B2"].value == 150
            assert sheet["B3"].value == 200 and sheet["C2"].value == "نعم"
        finally:
            book.close()
    else:
        assert path.read_text(encoding="utf-8").splitlines()[2] == doc["expected_text"]


@pytest.mark.parametrize("case", DATA["test_cases"], ids=lambda c: c["id"])
def test_case_evidence_and_audience_oracle(case):
    actor = USERS[case["actor_id"]]
    expected = case["expected"]
    for reference in expected["evidence"]:
        doc = DOCS[reference["artifact_id"]]
        assert allowed(actor, doc)
        evidence = next(e for e in doc["evidence"] if e["id"] == reference["evidence_id"])
        assert evidence["quote"] in doc["expected_text"] and evidence["location"]
    for forbidden in expected.get("forbidden_artifacts", []):
        assert not allowed(actor, DOCS[forbidden])
    if expected["outcome"] in {"deny", "abstain_no_evidence"}:
        assert expected["evidence"] == []
    if expected["outcome"] == "conflict":
        assert expected["must_not_choose_winner"] and len(expected["evidence"]) == 2


def test_public_document_requires_service_not_cross_tenant_db_access():
    case = DATA["test_cases"][17]
    assert case["expected"]["must_not_grant_tenant_database_access"] is True
    assert case["expected"]["execution_blockers"] == ["shared_knowledge_access_not_implemented"]
    assert allowed(USERS["poc-other"], DOCS["D1-v2"])
    assert not allowed(USERS["poc-other"], DOCS["D2"])
    assert not allowed(USERS["poc-employee"], DOCS["D4"])


def test_compose_cannot_attach_existing_ports_or_volumes():
    config = yaml.safe_load((ROOT / "compose.yml").read_text())
    assert len(config["services"]) == 1
    postgres = config["services"]["postgres"]
    assert "ports" not in postgres and "container_name" not in postgres
    assert config["networks"]["isolated"]["internal"] is True
    assert not config["volumes"]["data"].get("external")
    assert "name" not in config["volumes"]["data"]
    assert "${POC_PROJECT:?" in config["name"]
    assert postgres["restart"] == "no"
    assert set(postgres["secrets"]) == {"bootstrap_password", "registry_password", "tenant_password"}


@pytest.mark.parametrize("bad_id", ["", "legal", "hr", "finance", "../existing", "a"*31, "F"*32])
def test_controller_rejects_unmanaged_run_ids(bad_id):
    with pytest.raises(ValueError):
        load(bad_id)


def test_inherited_company_configuration_is_not_used(monkeypatch):
    for key in ["PGHOST", "PGPORT", "DATABASE_URL", "PROVISIONING_ADMIN_URL", "OPENAI_API_KEY", "DOCKER_HOST", "POC_PROJECT"]:
        monkeypatch.setenv(key, "NON_SECRET_TEST_CANARY")
        assert key not in clean_environment()


def test_ground_truth_contains_no_credential_fields():
    def inspect(value):
        if isinstance(value, dict):
            assert not {"password", "password_hash", "url", "api_key", "token"} & set(value)
            for child in value.values(): inspect(child)
        elif isinstance(value, list):
            for child in value: inspect(child)
    inspect(DATA)


@pytest.mark.parametrize("contaminated_cluster", [True, False])
def test_seed_refuses_existing_resources_before_any_writes(contaminated_cluster):
    calls = []
    def execute(db, statement):
        calls.append(statement)
        if "json_agg(datname" in statement:
            names = ["poc_registry", "postgres", "template0", "template1"]
            if contaminated_cluster: names.append("existing-company-database")
            return json.dumps(names)
        return "1"  # Nonempty public schema in an otherwise expected cluster.
    with pytest.raises(RuntimeError, match="newly created|populated"):
        seed(execute, DATA, "unused-run", {})
    assert all(statement.startswith("SELECT") for statement in calls)
