"""C06 Gate 1: disposable, durable-result contract tests.

This file deliberately contains the entire SQLite store harness. It is NOT an
application store, migration, service registration, or deployment proposal.
All files are created under pytest's tmp_path; no department database is used.
"""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.usage import service
from app.usage.external_results import (
    PreparedQuestionResult,
    StoredQuestionResult,
    execute_with_result_store,
)
from app.usage.models import UsageEntry, UsageRequest
from test_question_adapter import command
from test_usage import configure, usage


class DisposableResultStore:
    """Test-only, file-backed SQLite implementation of the C04 protocol.

    Source checks are a synthetic trusted callback. A real adapter must verify
    current published document versions and audiences through its owner service.
    """

    def __init__(self, path: Path, source_authorizer):
        self.path = path
        self.source_authorizer = source_authorizer
        self.writes = 0
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS synthetic_results (
                    owner_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (outcome IN ('answered', 'no_answer')),
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (owner_id, request_id)
                )
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save_once(self, record):
        if not isinstance(record, StoredQuestionResult):
            raise ValueError("invalid_result")
        if not isinstance(record.payload, dict):
            raise ValueError("invalid_payload")
        sources = record.payload.get("sources")
        if not isinstance(sources, list) or (
            record.outcome == "answered" and not sources
        ) or (record.outcome == "no_answer" and sources):
            raise ValueError("invalid_sources")
        if any(
            not isinstance(source, dict)
            or not isinstance(source.get("department_id"), str)
            or not isinstance(source.get("document_id"), int)
            or not isinstance(source.get("version_id"), str)
            for source in sources
        ):
            raise ValueError("invalid_source_reference")
        # Each call owns its connection and transaction; UNIQUE is enforced
        # by SQLite, not by a Python dictionary or a process-local lock.
        with self.connect() as db:
            try:
                db.execute(
                    """INSERT INTO synthetic_results
                       (owner_id, request_id, fingerprint, outcome, payload_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        record.user_id,
                        record.request_id,
                        record.fingerprint,
                        record.outcome,
                        json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError("immutable_result_conflict") from None
        self.writes += 1

    def load_authorized(self, user_id, request_id):
        with self.connect() as db:
            row = db.execute(
                """SELECT fingerprint, outcome, payload_json FROM synthetic_results
                   WHERE owner_id = ? AND request_id = ?""",
                (user_id, request_id),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[2])
            sources = payload["sources"]
            if not isinstance(sources, list):
                raise ValueError("invalid_sources")
        except (ValueError, KeyError, TypeError):
            raise service.UsageError(503, "question_result_integrity_error") from None
        # Re-evaluated on EVERY load, including a new store instance/replay.
        if self.source_authorizer(user_id, tuple(sources)) is not True:
            raise service.UsageError(403, "question_source_access_revoked")
        return StoredQuestionResult(
            user_id=user_id,
            request_id=request_id,
            fingerprint=row[0],
            outcome=row[1],
            payload=payload,
        )


def payload(answer="PRIVATE_C06_ANSWER", *, sources=None):
    return {
        "answer": answer,
        "sources": sources if sources is not None else [
            {"department_id": "hr", "document_id": 7, "version_id": "v1"}
        ],
    }


def invoke(engine, store, process, **changes):
    args = dict(
        authenticated_user_id="employee",
        command=command(),
        estimated_micro_usd=10,
        authorize_content=lambda *_: True,
        process=process,
        store=store,
    )
    args.update(changes)
    return execute_with_result_store(engine, **args)


def test_c06_durable_replay_across_store_reopen_charges_once(usage, tmp_path):
    engine, _, _ = usage
    configure(engine)
    path = tmp_path / "c06-contract-only.sqlite"
    source_access = Mock(return_value=True)
    store = DisposableResultStore(path, source_access)
    process = Mock(return_value=PreparedQuestionResult("answered", payload()))

    first = invoke(engine, store, process)
    reopened = DisposableResultStore(path, source_access)
    replay = invoke(engine, reopened, process)

    assert first.response == replay.response == payload()
    assert replay.receipt.replayed
    assert process.call_count == 1 and store.writes == 1 and reopened.writes == 0
    assert source_access.call_count == 2
    with Session(engine) as session:
        assert session.scalar(select(func.sum(UsageEntry.question_delta))) == -1
        row = session.scalar(select(UsageRequest))
        assert row.state == "completed"
        assert "PRIVATE_C06_ANSWER" not in str(row.__dict__)


def test_c06_source_revocation_after_restart_denies_replay(usage, tmp_path):
    engine, _, _ = usage
    configure(engine)
    path = tmp_path / "revocation.sqlite"
    store = DisposableResultStore(path, lambda *_: True)
    process = Mock(return_value=PreparedQuestionResult("answered", payload()))
    first = invoke(engine, store, process)

    def revoked(_user, sources):
        assert sources[0]["version_id"] == "v1"
        return False

    reopened = DisposableResultStore(path, revoked)
    with pytest.raises(service.UsageError, match="question_source_access_revoked"):
        invoke(engine, reopened, process)
    assert process.call_count == 1
    with Session(engine) as session:
        assert session.get(UsageRequest, first.receipt.request_id).state == "completed"
        assert session.scalar(select(func.sum(UsageEntry.question_delta))) == -1


def test_c06_wrong_owner_cannot_load_result(tmp_path):
    store = DisposableResultStore(tmp_path / "owner.sqlite", lambda *_: True)
    store.save_once(StoredQuestionResult(
        "employee", "request-1", "a" * 64, "answered", payload()
    ))
    assert store.load_authorized("outsider", "request-1") is None


def test_c06_immutable_conflict_across_connections(tmp_path):
    path = tmp_path / "unique.sqlite"
    first = DisposableResultStore(path, lambda *_: True)
    second = DisposableResultStore(path, lambda *_: True)
    record = StoredQuestionResult("employee", "request-1", "a" * 64, "answered", payload())
    first.save_once(record)
    with pytest.raises(ValueError, match="immutable_result_conflict"):
        second.save_once(replace(record, payload=payload("REPLACEMENT")))
    assert first.load_authorized("employee", "request-1").payload == payload()


def test_c06_concurrent_writers_only_one_commits(tmp_path):
    path = tmp_path / "race.sqlite"
    DisposableResultStore(path, lambda *_: True)
    barrier = Barrier(2)

    def writer(answer):
        store = DisposableResultStore(path, lambda *_: True)
        record = StoredQuestionResult(
            "employee", "request-1", "a" * 64, "answered", payload(answer)
        )
        barrier.wait(timeout=10)
        try:
            store.save_once(record)
            return "committed"
        except ValueError as error:
            assert str(error) == "immutable_result_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, ("FIRST", "SECOND")))
    assert sorted(results) == ["committed", "conflict"]
    saved = DisposableResultStore(path, lambda *_: True).load_authorized(
        "employee", "request-1"
    )
    assert saved.payload["answer"] in {"FIRST", "SECOND"}


def test_c06_lost_save_acknowledgment_never_redispatches(usage, tmp_path):
    engine, _, _ = usage
    configure(engine)
    path = tmp_path / "lost-ack.sqlite"
    store = DisposableResultStore(path, lambda *_: True)
    original = store.save_once

    def lose_ack(record):
        original(record)
        raise TimeoutError("synthetic_ack_lost")

    store.save_once = lose_ack
    process = Mock(return_value=PreparedQuestionResult("answered", payload()))
    first = invoke(engine, store, process)
    reopened = DisposableResultStore(path, lambda *_: True)
    second = invoke(engine, reopened, process)

    assert first.receipt.state == second.receipt.state == "uncertain"
    assert first.response is second.response is None
    assert process.call_count == 1
    with Session(engine) as session:
        row = session.scalar(select(UsageRequest))
        assert row.held_micro_usd == 10
        assert session.scalar(select(func.sum(UsageEntry.question_delta))) == -1


def test_c06_accounting_finish_failure_keeps_durable_result_hidden(usage, tmp_path):
    engine, _, _ = usage
    configure(engine)
    path = tmp_path / "finish-failed.sqlite"
    store = DisposableResultStore(path, lambda *_: True)
    process = Mock(return_value=PreparedQuestionResult("answered", payload()))
    with patch.object(
        service, "finish",
        side_effect=service.UsageError(503, "synthetic_closure_failure"),
    ):
        first = invoke(engine, store, process)
    reopened = DisposableResultStore(path, lambda *_: True)
    replay = invoke(engine, reopened, process)
    assert first.receipt.state == replay.receipt.state == "uncertain"
    assert first.response is replay.response is None
    assert process.call_count == 1
    assert reopened.load_authorized("employee", first.receipt.request_id) is not None


def test_c06_corrupt_fingerprint_blocks_delivery_without_regeneration(usage, tmp_path):
    engine, _, _ = usage
    configure(engine)
    path = tmp_path / "corrupt.sqlite"
    store = DisposableResultStore(path, lambda *_: True)
    process = Mock(return_value=PreparedQuestionResult("answered", payload()))
    first = invoke(engine, store, process)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE synthetic_results SET fingerprint = ? WHERE request_id = ?",
            ("0" * 64, first.receipt.request_id),
        )
    with pytest.raises(service.UsageError, match="question_result_integrity_error"):
        invoke(engine, DisposableResultStore(path, lambda *_: True), process)
    assert process.call_count == 1


@pytest.mark.parametrize("department", ["finance", "legal"])
def test_c06_unauthorized_or_failed_department_never_opens_store(
    usage, tmp_path, department
):
    engine, _, _ = usage
    configure(engine)
    path = tmp_path / "not-created.sqlite"
    process = Mock()
    store = Mock()
    with pytest.raises(service.UsageError, match="question_department_forbidden"):
        invoke(engine, store, process, command=command(department_ids=(department,)))
    process.assert_not_called()
    store.save_once.assert_not_called()
    store.load_authorized.assert_not_called()
    assert not path.exists()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(UsageRequest)) == 0


def test_c06_no_answer_without_sources_is_durable(usage, tmp_path):
    engine, _, _ = usage
    configure(engine)
    path = tmp_path / "no-answer.sqlite"
    store = DisposableResultStore(path, lambda *_: True)
    process = Mock(return_value=PreparedQuestionResult(
        "no_answer", payload("لا توجد إجابة.", sources=[])
    ))
    first = invoke(engine, store, process)
    replay = invoke(engine, DisposableResultStore(path, lambda *_: True), process)
    assert first.receipt.outcome == replay.receipt.outcome == "no_answer"
    assert replay.response == first.response
    assert process.call_count == 1
