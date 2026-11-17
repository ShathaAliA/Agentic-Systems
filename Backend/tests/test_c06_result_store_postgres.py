"""Real C06 storage tests; only the fresh C03 runner-owned database is accepted."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import uuid4

from alembic import command as migrate
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.conversation_store.postgres import PostgresResultStore, canonical
from app.usage.external_results import StoredQuestionResult, PreparedQuestionResult, execute_with_result_store
from app.usage.models import UsageEntry, UsageRequest
from app.usage.service import UsageError
from app.usage import service
from c06_store_fixtures import answer
from test_question_adapter import command
from test_usage import usage, configure

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("RUN_AUTH_POSTGRES_TESTS") != "1", reason="Fresh isolated C06 runner required")]
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def engines():
    assert os.getenv("AUTH_TEST_BACKUP_ACK") == "1"
    url = make_url(json.loads(Path(os.environ["AUTH_TEST_ADMIN_URL_FILE"]).read_text(encoding="utf-8-sig"))["url"])
    assert url.database == "c06_result_test" and url.username == "auth_test_owner"
    native = os.getenv("AUTH_TEST_NATIVE_POSTGRES") == "1"
    if native:
        assert url.host == "127.0.0.1" and url.port not in (5432, 5435, 55435, 55436)
    else:
        assert url.host.startswith("ai-auth-test-")
    report = Path(os.environ.get("AUTH_TEST_REPORT_DIR", "/reports"))
    # The runner captured/restored-readable backups before this fixture can mutate SQL.
    assert (report / ("before.dump" if native else "auth-before.dump")).is_file()
    assert (report / ("globals-before.sql" if native else "auth-globals-before.sql")).is_file()
    admin = create_engine(url, hide_parameters=True)
    runtime = None
    try:
        with admin.connect() as c:
            assert c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
            assert c.scalar(text("SELECT current_database()")) == "c06_result_test"
            owners = c.execute(text("SELECT datname,pg_get_userbyid(datdba),datacl::text FROM pg_database ORDER BY datname")).all()
            (report / "c06-owners-acls-before.json").write_text(json.dumps([list(r) for r in owners]), encoding="utf-8")
        with admin.begin() as c:
            c.exec_driver_sql("CREATE ROLE c06_store_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            c.exec_driver_sql("GRANT USAGE,CREATE ON SCHEMA public TO c06_store_owner")
        cfg = Config(str(BACKEND / "result_store_alembic.ini"))
        legacy = None
        for target in ("c06_0001", "c06_0002", "head", "head"):
            with admin.begin() as c:
                c.exec_driver_sql("SET LOCAL ROLE c06_store_owner")
                c.execute(text("SELECT set_config('app.c06_user_id','migration-history',true)"))
                cfg.attributes.update(connection=c, expected_database="c06_result_test", store_migration_approved=True)
                migrate.upgrade(cfg, target)
                if legacy is None:
                    item = record(user="migration-history")
                    c.execute(text("""INSERT INTO c06_results
                        (request_id,owner_id,fingerprint,outcome,schema_version,payload,payload_hash)
                        VALUES(:id,:owner,:fp,:outcome,1,CAST(:payload AS jsonb),:hash)"""),
                        dict(id=item.request_id, owner=item.user_id, fp=item.fingerprint, outcome=item.outcome,
                             payload=canonical(item.payload), hash=sha256(canonical(item.payload).encode()).hexdigest()))
                    c.execute(text("INSERT INTO public.c06_result_sources VALUES (:id,:owner,0,CAST(:source AS jsonb))"),
                        dict(id=item.request_id,owner=item.user_id,source=canonical(item.payload['sources'][0])))
                    legacy_sources = c.execute(text("SELECT * FROM c06_result_sources WHERE owner_id='migration-history'")).all()
                    legacy = c.execute(text("SELECT * FROM c06_results WHERE owner_id='migration-history'")).all()
                else:
                    assert c.execute(text("SELECT * FROM c06_results WHERE owner_id='migration-history'")).all() == legacy
                    assert c.execute(text("SELECT * FROM c06_result_sources WHERE owner_id='migration-history'")).all() == legacy_sources
        password = secrets.token_urlsafe(32)
        with psycopg.connect(host=url.host, port=url.port or 5432, dbname=url.database,
                             user=url.username, password=url.password, autocommit=True) as c:
            c.execute(sql.SQL("CREATE ROLE c06_test_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Literal(password)))
            # Only this newly created test cluster's databases are involved.
            for database in ("c06_result_test", "postgres", "template1"):
                c.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
            c.execute("GRANT CONNECT ON DATABASE c06_result_test TO c06_test_api")
            c.execute("REVOKE TEMPORARY ON DATABASE c06_result_test FROM PUBLIC")
            c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            c.execute("GRANT USAGE ON SCHEMA public TO c06_test_api")
            c.execute("GRANT SELECT ON c06_store_identity,c06_store_version,c06_result_sources TO c06_test_api")
            c.execute("GRANT SELECT,INSERT ON c06_results,c06_conversations,c06_turns TO c06_test_api")
        runtime = create_engine(url.set(username="c06_test_api", password=password), hide_parameters=True)
        with admin.connect() as c:
            after = c.execute(text("SELECT datname,pg_get_userbyid(datdba),datacl::text FROM pg_database ORDER BY datname")).all()
            assert [(r[0], r[1]) for r in after] == [(r[0], r[1]) for r in owners]
            (report / "c06-owners-acls-after.json").write_text(json.dumps([list(r) for r in after]), encoding="utf-8")
        yield admin, runtime
    finally:
        if runtime is not None: runtime.dispose()
        admin.dispose()


def store(engine, user="employee", authorize=lambda *_: True):
    # Synthetic explicit callback; never a default application source adapter.
    return PostgresResultStore(engine, expected_database="c06_result_test",
                               authenticated_user_id=user, source_authorizer=authorize)


def record(user="employee", outcome="answered"):
    payload = answer()
    if outcome == "no_answer":
        payload.update(status="no_answer", sources=[], answer_kind="system_notice", answer="لا توجد إجابة.")
    return StoredQuestionResult(user, payload["question_request_id"], "a" * 64, outcome, payload)


def test_store_migrations_and_no_central_schema(engines):
    admin, _ = engines
    with admin.connect() as c:
        assert c.scalar(text("SELECT version_num FROM c06_store_version")) == "c06_0003"
        tables = set(c.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
        assert tables == {"c06_store_version", "c06_store_identity", "c06_results", "c06_result_sources", "c06_conversations", "c06_turns"}


def test_reopen_sources_and_revocation(engines):
    _, runtime = engines
    item = record()
    store(runtime).save_once(item)
    runtime.dispose()  # Next read must reopen real connections.
    loaded = store(runtime).load_authorized(item.user_id, item.request_id)
    assert loaded == item
    with pytest.raises(UsageError, match="question_source_access_revoked"):
        store(runtime, authorize=lambda *_: False).load_authorized(item.user_id, item.request_id)
    assert store(runtime, user="outsider").load_authorized("outsider", item.request_id) is None


def test_runtime_privileges_and_row_isolation(engines):
    admin, runtime = engines
    for statement in ("UPDATE c06_results SET outcome=outcome", "DELETE FROM c06_results",
                      "TRUNCATE c06_results CASCADE", "ALTER TABLE c06_results ADD bad int",
                      "CREATE TABLE forbidden(id int)", "CREATE TEMP TABLE forbidden(id int)",
                      "CREATE ROLE forbidden_role", "SELECT * FROM pg_authid"):
        with runtime.begin() as c, pytest.raises(DBAPIError) as error:
            c.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"
    with runtime.begin() as c:
        assert not any(c.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one())
        assert c.scalar(text("SELECT count(*) FROM c06_results")) == 0  # no owner context
        c.execute(text("SELECT set_config('app.c06_user_id','outsider',true)"))
        assert c.scalar(text("SELECT count(*) FROM c06_results WHERE owner_id='employee'")) == 0
    for database in ("postgres", "template1"):
        with pytest.raises(DBAPIError):
            other = create_engine(runtime.url.set(database=database), hide_parameters=True)
            try:
                with other.connect(): pass
            finally: other.dispose()
    with pytest.raises(UsageError, match="privileged_identity"):
        store(admin).save_once(record())


def test_database_trigger_prevents_history_mutation_even_with_owner_sql(engines):
    admin, _ = engines
    for statement in ("UPDATE c06_results SET outcome=outcome", "DELETE FROM c06_result_sources",
                      "TRUNCATE c06_results CASCADE"):
        with admin.begin() as c, pytest.raises(DBAPIError) as error:
            c.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"


def test_atomic_source_failure_rolls_back_result(engines):
    admin, runtime = engines
    item = record()
    with admin.begin() as c:
        c.exec_driver_sql("CREATE TRIGGER forced_test_failure BEFORE INSERT ON public.c06_result_sources FOR EACH ROW EXECUTE FUNCTION public.c06_refuse_mutation()")
    try:
        with pytest.raises(UsageError, match="result_store_unavailable"):
            store(runtime).save_once(item)
    finally:
        with admin.begin() as c:
            c.exec_driver_sql("DROP TRIGGER forced_test_failure ON public.c06_result_sources")
    assert store(runtime).load_authorized(item.user_id, item.request_id) is None


def test_concurrent_writes_have_one_immutable_winner(engines):
    _, runtime = engines
    item = record()
    barrier = Barrier(2)
    def write(_):
        barrier.wait(timeout=10)
        try: store(runtime).save_once(item); return "saved"
        except UsageError as e:
            assert e.code == "immutable_result_conflict"
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == ["conflict", "saved"]


def test_authorizer_cannot_mutate_delivered_payload(engines):
    _, runtime = engines
    item = record()
    store(runtime).save_once(item)
    def authorize(user, payload):
        payload["answer"] = "untrusted replacement"
        return True
    assert store(runtime, authorize=authorize).load_authorized(item.user_id, item.request_id).payload == item.payload


@pytest.mark.parametrize("failure", [None, "lost_ack", "finish", "abrupt"])
def test_u1_replay_failure_boundaries_and_no_central_content(engines, usage, failure):
    _, runtime = engines
    central, _, _ = usage  # separate in-memory synthetic ledger; no real central DB.
    configure(central)
    adapter = store(runtime)
    saved = adapter.save_once
    if failure == "lost_ack":
        def lose_ack(item): saved(item); raise TimeoutError("synthetic ack lost")
        adapter.save_once = lose_ack
    def process(context):
        return PreparedQuestionResult("answered", answer(context.request_id))
    process = Mock(side_effect=process)
    def invoke():
        return execute_with_result_store(central, authenticated_user_id="employee", command=command(),
            estimated_micro_usd=10, authorize_content=lambda *_: True, process=process, store=adapter)
    if failure in ("finish", "abrupt"):
        error = UsageError(503, "synthetic_finish_failure") if failure == "finish" else RuntimeError("synthetic crash")
        with patch.object(service, "finish", side_effect=error):
            if failure == "abrupt":
                with pytest.raises(RuntimeError): invoke()
            else: assert invoke().response is None
    else:
        first = invoke()
        if failure is None: assert first.response is not None
    replay = invoke()
    assert process.call_count == 1
    if failure: assert replay.response is None and replay.receipt.state in ("running", "uncertain")
    else: assert replay.receipt.replayed and replay.response is not None
    with Session(central) as c:
        assert c.scalar(select(func.sum(UsageEntry.question_delta))) == -1
        assert "مدة الحفظ" not in str(c.scalar(select(UsageRequest)).__dict__)


def test_no_answer_persists_and_schema_rejects_corruption_before_save(engines):
    _, runtime = engines
    item = record(outcome="no_answer")
    store(runtime).save_once(item)
    assert store(runtime).load_authorized(item.user_id, item.request_id) == item
    invalid = record()
    invalid.payload["sources"][0]["document_id"] = True
    with pytest.raises(UsageError, match="invalid_stored_answer"):
        store(runtime).save_once(invalid)


def test_cannot_append_sources_to_an_already_committed_result(engines):
    _, runtime = engines
    item = record()
    store(runtime).save_once(item)
    with runtime.begin() as c, pytest.raises(DBAPIError) as error:
        c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
        c.execute(text("""INSERT INTO c06_result_sources(request_id,owner_id,ordinal,source)
            VALUES (:request,'employee',1,'{}')"""), {"request": item.request_id})
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("corruption", ["shape", "digest", "source_index"])
def test_corrupt_raw_rows_are_never_delivered(engines, corruption):
    admin, runtime = engines
    item = record(outcome="no_answer" if corruption == "digest" else "answered")
    payload = {"bad": True,"sources":[]} if corruption == "shape" else item.payload
    # New disposable test rows deliberately simulate data imported incorrectly.
    # No UPDATE/DELETE or bypass is added to the actual adapter.
    with admin.begin() as c:
        if corruption == "source_index": c.exec_driver_sql("ALTER TABLE public.c06_results DISABLE TRIGGER derive_result_sources")
        c.execute(text("""INSERT INTO c06_results
            (request_id,owner_id,fingerprint,outcome,schema_version,payload,payload_hash)
            VALUES(:request,'employee',:fp,:outcome,1,CAST(:payload AS jsonb),:hash)"""),
            dict(request=item.request_id, fp=item.fingerprint, outcome=item.outcome,
                 payload=json.dumps(payload), hash=(sha256(canonical(payload).encode()).hexdigest()
                                                   if corruption == "source_index" else "b" * 64)))
        if corruption == "source_index": c.exec_driver_sql("ALTER TABLE public.c06_results ENABLE TRIGGER derive_result_sources")
    with pytest.raises(UsageError, match="question_result_integrity_error"):
        store(runtime).load_authorized("employee", item.request_id)
