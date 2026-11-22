"""Gate 4.5 restore-drill verification. Runs only inside the guarded Docker restore step of
tools/test-auth-postgres.ps1 -RestoreDrill, against a NEW database restored from the
throwaway test cluster's own dump. Never a project database."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.conversation_store.postgres import PostgresResultStore
from app.usage.external_results import StoredQuestionResult
from app.usage.service import UsageError
from c06_store_fixtures import answer

DRILL_FILE = os.getenv("RESTORE_DRILL_FILE", "/reports/restore-drill.json")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("RUN_AUTH_POSTGRES_TESTS") != "1" or not Path(DRILL_FILE).is_file(),
    reason="Only the guarded Docker restore drill supplies a restored database")]

TABLES = ("c06_results", "c06_result_sources", "c06_conversations", "c06_turns", "c06_question_intents",
          "c06_handoffs", "c06_handoff_events", "c06_handoff_messages", "c06_handoff_reads", "c06_handoff_access_log")


@pytest.fixture(scope="module")
def drill():
    cfg = json.loads(Path(DRILL_FILE).read_text(encoding="utf-8-sig"))
    source, admin, runtime = (create_engine(cfg[k], hide_parameters=True)
                              for k in ("source_admin", "restored_admin", "restored_runtime"))
    name = cfg["restored_database"]
    assert source.url.database == "c06_result_test" and admin.url.database == name == runtime.url.database
    assert name.startswith("c06_restored_") and admin.url.host.startswith("ai-auth-test-")
    try:
        yield source, admin, runtime, name
    finally:
        for engine in (source, admin, runtime):
            engine.dispose()


def bound(runtime, name, user):
    return PostgresResultStore(runtime, expected_database=name, authenticated_user_id=user,
                               source_authorizer=lambda *_: True)


def owners(admin):
    with admin.connect() as c:
        return list(c.scalars(text("SELECT DISTINCT owner_id FROM public.c06_results ORDER BY 1")))


def test_restored_rows_match_the_source_exactly_and_are_not_empty(drill):
    source, admin, _, _ = drill
    for table in TABLES:
        query = text(f"SELECT count(*), COALESCE(md5(string_agg(t::text, E'\\n' ORDER BY t::text)),'') FROM public.{table} t")
        with source.connect() as a, admin.connect() as b:
            before, after = a.execute(query).one(), b.execute(query).one()
        assert tuple(before) == tuple(after), table
    with admin.connect() as c:
        for table in ("c06_results", "c06_conversations", "c06_turns", "c06_question_intents"):
            assert c.scalar(text(f"SELECT count(*) FROM public.{table}")) > 0, table


def test_restored_schema_identity_and_security_flags_are_preserved(drill):
    _, admin, _, name = drill
    with admin.connect() as c:
        assert c.scalar(text("SELECT version_num FROM public.c06_store_version")) == "c06_0005"
        assert c.execute(text("SELECT current_database(), kind, version FROM public.c06_store_identity")).one() == (name, "conversation_results", 1)
        flags = c.execute(text("""SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class
            WHERE relname = ANY(:t) AND relkind='r'"""), {"t": list(TABLES)}).all()
        assert len(flags) == len(TABLES) and all(flag for _, flag in flags), flags
        for table in TABLES:  # the immutability trigger survived the restore
            assert c.scalar(text("""SELECT count(*) FROM pg_trigger WHERE tgrelid=CAST(:t AS regclass)
                AND tgname='refuse_mutation'"""), {"t": "public." + table}) == 1 or table == "c06_result_sources"
        assert c.scalar(text("SELECT to_regprocedure('public.c06_derive_result_sources()') IS NOT NULL"))


def test_restored_store_passes_the_runtime_fail_closed_checks_and_every_result_verifies(drill):
    _, admin, runtime, name = drill
    verified = 0
    for owner in owners(admin):
        store = bound(runtime, name, owner)
        with store.connection() as c:  # ownership, grants, sealing function, identity
            ids = list(c.scalars(text("SELECT request_id FROM public.c06_results WHERE owner_id=:o"), {"o": owner}))
        for request_id in ids:
            record = store.load_authorized(owner, str(request_id))  # validates hash and source rows
            assert record is not None and record.user_id == owner
            verified += 1
    assert verified > 0


def test_restored_store_still_refuses_mutation_and_hides_other_owners(drill):
    _, admin, runtime, name = drill
    owner = owners(admin)[0]
    store = bound(runtime, name, owner)
    for table in ("c06_turns", "c06_question_intents", "c06_results"):
        for statement in (f"UPDATE public.{table} SET owner_id=owner_id", f"DELETE FROM public.{table}", f"TRUNCATE public.{table}"):
            with pytest.raises(UsageError):
                with store.connection() as c:
                    c.execute(text(statement))
    stranger = bound(runtime, name, "restore-stranger-" + uuid4().hex[:8])
    with stranger.connection() as c:
        for table in ("c06_results", "c06_conversations", "c06_turns", "c06_question_intents"):
            assert c.scalar(text(f"SELECT count(*) FROM public.{table}")) == 0


def test_restored_store_accepts_new_writes_without_special_handling(drill):
    _, admin, runtime, name = drill
    owner = owners(admin)[0]
    store = bound(runtime, name, owner)
    payload = answer()
    item = StoredQuestionResult(owner, payload["question_request_id"], "b" * 64, "answered", payload)
    store.save_once(item)
    assert store.load_authorized(owner, item.request_id) == item
    with store.connection() as c:  # a new intent for an existing conversation, derived rows included
        conversation = c.scalar(text("SELECT id FROM public.c06_conversations WHERE owner_id=:o LIMIT 1"), {"o": owner})
        assert conversation is not None
        c.execute(text("""INSERT INTO public.c06_question_intents
            (id,conversation_id,owner_id,request_key,fingerprint,context_revision,question)
            VALUES(CAST(:id AS uuid),:conv,:o,:k,:fp,'restore:0','سؤال بعد الاستعادة')"""),
            dict(id=str(uuid4()), conv=conversation, o=owner, k="post-restore-" + uuid4().hex, fp="c" * 64))
