from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.conversation_store.postgres import PostgresResultStore, validated
from app.usage.external_results import StoredQuestionResult
from app.usage.service import UsageError
from c06_store_fixtures import answer


def test_store_migration_graph_is_separate_and_cannot_resolve_credentials():
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic import command
    from unittest.mock import patch
    backend = Path(__file__).resolve().parents[1]
    with patch("sqlalchemy.create_engine", side_effect=AssertionError("No engine allowed")):
        scripts = ScriptDirectory(str(backend / "result_store_alembic"))
        assert scripts.get_heads() == ["c06_0002"] and scripts.get_bases() == ["c06_0001"]
        with pytest.raises(RuntimeError, match="Explicit isolated store migration connection required"):
            command.upgrade(Config(str(backend / "result_store_alembic.ini")), "head")


@pytest.mark.parametrize("corruption", ["boolean_id", "missing_version", "page_zero", "unknown_field",
    "no_evidence", "no_answer_evidence", "invalid_scope", "wrong_request", "wrong_outcome", "extra_owner"])
def test_invalid_envelope_is_rejected_before_storage(corruption):
    payload = answer()
    request_id = payload["question_request_id"]
    outcome = "answered"
    if corruption == "boolean_id": payload["sources"][0]["document_id"] = True
    if corruption == "missing_version": del payload["sources"][0]["version_id"]
    if corruption == "page_zero": payload["sources"][0]["location"]["page_number"] = 0
    if corruption == "unknown_field": payload["sources"][0]["secret"] = "canary"
    if corruption == "no_evidence": payload["sources"] = []
    if corruption == "no_answer_evidence": payload["status"] = "no_answer"; outcome = "no_answer"
    if corruption == "invalid_scope": payload["sources"][0]["department_id"] = "hr"
    if corruption == "wrong_request": request_id = "33333333-3333-4333-8333-333333333333"
    if corruption == "wrong_outcome": outcome = "no_answer"
    if corruption == "extra_owner": payload["user_id"] = "outsider"
    with pytest.raises(UsageError, match="invalid_stored_answer"):
        validated(StoredQuestionResult("employee", request_id, "a" * 64, outcome, payload))


def test_missing_authorizer_denies_delivery_without_connecting():
    engine = Mock(dialect=SimpleNamespace(name="postgresql"), url=SimpleNamespace(database="c06_result_test"))
    store = PostgresResultStore(engine, expected_database="c06_result_test", authenticated_user_id="employee")
    with pytest.raises(UsageError, match="source_authorizer_unavailable"):
        store.load_authorized("employee", answer()["question_request_id"])
    engine.begin.assert_not_called()


def test_wrong_database_and_wrong_owner_are_denied_before_connection():
    engine = Mock(dialect=SimpleNamespace(name="postgresql"), url=SimpleNamespace(database="other"))
    with pytest.raises(UsageError, match="configuration_invalid"):
        PostgresResultStore(engine, expected_database="c06_result_test", authenticated_user_id="employee")
    engine.url.database = "c06_result_test"
    store = PostgresResultStore(engine, expected_database="c06_result_test", authenticated_user_id="employee")
    with pytest.raises(UsageError, match="result_owner_forbidden"):
        store.load_authorized("outsider", answer()["question_request_id"])
    engine.begin.assert_not_called()
