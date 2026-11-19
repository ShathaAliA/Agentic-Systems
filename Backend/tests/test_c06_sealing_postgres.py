"""Gate 4.1: legacy proof and sealing regressions on fresh C03 clusters only."""
import json
from hashlib import sha256
from uuid import uuid4
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from test_c06_result_store_postgres import engines, record, pytestmark, BACKEND
from app.conversation_store.postgres import canonical


def new_database(admin, prefix):
    name = prefix + uuid4().hex[:16]
    assert admin.url.database == "c06_result_test"
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.connection.driver_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return create_engine(admin.url.set(database=name), hide_parameters=True)


def test_legacy_txid_seal_collision_is_reproduced_with_restricted_role(engines):
    admin, runtime = engines
    legacy = new_database(admin, "c06_legacy_")
    cfg = Config(str(BACKEND / "result_store_alembic.ini"))
    with legacy.begin() as c:
        cfg.attributes.update(connection=c, expected_database=legacy.url.database, store_migration_approved=True)
        command.upgrade(cfg,"c06_0002")
        c.exec_driver_sql("GRANT USAGE ON SCHEMA public TO c06_test_api")
        c.exec_driver_sql("GRANT SELECT,INSERT ON c06_results,c06_result_sources TO c06_test_api")
    api=create_engine(runtime.url.set(database=legacy.url.database),hide_parameters=True)
    item=record(outcome="no_answer")
    try:
        with api.begin() as c:
            c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
            future=c.scalar(text("SELECT txid_current()"))+2
            c.execute(text("""INSERT INTO c06_results(request_id,owner_id,fingerprint,outcome,
                schema_version,payload,payload_hash,writer_txid)
                VALUES(:id,'employee',:fp,'no_answer',1,CAST(:payload AS jsonb),:hash,:future)"""),
                dict(id=item.request_id,fp=item.fingerprint,payload=canonical(item.payload),
                    hash=sha256(canonical(item.payload).encode()).hexdigest(),future=future))
        with api.begin() as c:
            assert c.scalar(text("SELECT txid_current()"))==future-1
        with api.begin() as c:
            c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
            assert c.scalar(text("SELECT txid_current()"))==future
            c.execute(text("INSERT INTO c06_result_sources VALUES (:id,'employee',0,'{}')"),dict(id=item.request_id))
        with api.begin() as c:
            c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
            assert c.scalar(text("SELECT count(*) FROM c06_result_sources WHERE request_id=:id"),dict(id=item.request_id))==1
            assert item.payload['sources']==[]  # Sealed index changed; delivery integrity must reject it.
    finally:
        api.dispose();legacy.dispose()


from copy import deepcopy
from sqlalchemy.exc import DBAPIError
from app.conversation_store.postgres import PostgresResultStore
from app.usage.service import UsageError
import pytest
import os
import subprocess


def bind(engine, user="employee"):
    return PostgresResultStore(engine,expected_database=engine.url.database,
        authenticated_user_id=user,source_authorizer=lambda *_:True)


def test_database_derives_exact_source_rows_without_runtime_insert(engines):
    admin,runtime=engines
    item=record();item.payload['sources']*=3
    bind(runtime).save_once(item)
    with admin.connect() as c:
        rows=c.execute(text("SELECT ordinal,source FROM c06_result_sources WHERE request_id=:id ORDER BY ordinal"),dict(id=item.request_id)).all()
        assert rows==list(enumerate(item.payload['sources']))
        assert not c.scalar(text("SELECT has_any_column_privilege('c06_test_api','public.c06_result_sources','INSERT')"))
        assert c.scalar(text("SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls FROM pg_roles r JOIN pg_proc p ON p.proowner=r.oid WHERE p.proname='c06_derive_result_sources'")) is False
    assert bind(runtime).load_authorized('employee',item.request_id)==item
    empty=record(outcome='no_answer');bind(runtime).save_once(empty)
    with admin.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM c06_result_sources WHERE request_id=:id"),dict(id=empty.request_id))==0


def test_new_seal_rejects_append_even_with_matching_counter_and_legacy_insert_grant(engines):
    admin,runtime=engines;item=record()
    with admin.begin() as c:c.exec_driver_sql("GRANT INSERT ON c06_result_sources TO c06_test_api")
    try:
        with runtime.begin() as c:
            c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
            future=c.scalar(text("SELECT txid_current()"))+2
            c.execute(text("""INSERT INTO c06_results(request_id,owner_id,fingerprint,outcome,schema_version,payload,payload_hash,writer_txid)
                VALUES(:id,'employee',:fp,'answered',1,CAST(:payload AS jsonb),:hash,:future)"""),
                dict(id=item.request_id,fp=item.fingerprint,payload=canonical(item.payload),
                     hash=sha256(canonical(item.payload).encode()).hexdigest(),future=future))
        with runtime.begin() as c:assert c.scalar(text("SELECT txid_current()"))==future-1
        with runtime.begin() as c,pytest.raises(DBAPIError) as error:
            c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
            assert c.scalar(text("SELECT txid_current()"))==future
            c.execute(text("INSERT INTO c06_result_sources VALUES (:id,'employee',1,'{}')"),dict(id=item.request_id))
        assert error.value.orig.sqlstate=='42501'
        with pytest.raises(UsageError,match='sealing_configuration_invalid'):
            bind(runtime).load_authorized('employee',item.request_id)
    finally:
        with admin.begin() as c:c.exec_driver_sql("REVOKE INSERT ON c06_result_sources FROM c06_test_api")
    assert bind(runtime).load_authorized('employee',item.request_id)==item


def test_owner_cannot_append_or_change_sources_using_ordinary_dml(engines):
    admin,runtime=engines;item=record();bind(runtime).save_once(item)
    for statement in ("INSERT INTO c06_result_sources VALUES (:id,'employee',1,'{}')",
                      "UPDATE c06_result_sources SET source='{}' WHERE request_id=:id",
                      "DELETE FROM c06_result_sources WHERE request_id=:id"):
        with admin.begin() as c,pytest.raises(DBAPIError) as error:
            c.execute(text(statement),dict(id=item.request_id))
        assert error.value.orig.sqlstate=='42501'
    with runtime.begin() as c,pytest.raises(DBAPIError):
        c.exec_driver_sql('SELECT public.c06_derive_result_sources()')


def test_sealed_child_constraint_failure_rolls_back_parent(engines):
    admin,runtime=engines;item=record()
    # Invalid raw SQL payload, not accepted by the Python DTO. The DB must still
    # roll back the result when its derived child violates a constraint.
    item.payload['sources']=[17]
    with runtime.begin() as c,pytest.raises(DBAPIError) as error:
        c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
        c.execute(text("""INSERT INTO c06_results(request_id,owner_id,fingerprint,outcome,schema_version,payload,payload_hash)
            VALUES(:id,'employee',:fp,'answered',1,CAST(:payload AS jsonb),:hash)"""),
            dict(id=item.request_id,fp=item.fingerprint,payload=canonical(item.payload),hash=sha256(canonical(item.payload).encode()).hexdigest()))
    assert error.value.orig.sqlstate=='23514'
    with admin.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM c06_results WHERE request_id=:id"),dict(id=item.request_id))==0


def test_schema_owner_with_elevated_role_is_rejected_before_sealing_ddl(engines):
    admin,_=engines;legacy=new_database(admin,'c06_bad_owner_')
    cfg=Config(str(BACKEND/'result_store_alembic.ini'))
    try:
        with legacy.begin() as c:
            cfg.attributes.update(connection=c,expected_database=legacy.url.database,store_migration_approved=True)
            command.upgrade(cfg,'c06_0002')
        with pytest.raises(RuntimeError,match='limited existing schema owner'):
            with legacy.begin() as c:
                cfg.attributes.update(connection=c)
                command.upgrade(cfg,'head')
        with legacy.connect() as c:
            assert c.scalar(text('SELECT version_num FROM c06_store_version'))=='c06_0002'
    finally:legacy.dispose()


def test_full_restore_to_empty_owned_database_preserves_seal(engines):
    admin,runtime=engines
    if os.getenv('AUTH_TEST_NATIVE_POSTGRES')!='1':
        pytest.skip('Focused native restore drill requires the guarded pg_dump/pg_restore binaries')
    from pathlib import Path
    binaries=Path(os.environ['AUTH_TEST_POSTGRES_BIN'])
    assert binaries.is_absolute() and (binaries/'pg_restore.exe').is_file()
    report=Path(os.environ['AUTH_TEST_REPORT_DIR'])
    dump=report/('sealed-'+uuid4().hex+'.dump')
    def cli(binary,*args):
        env=dict(os.environ);env['PGPASSWORD']=admin.url.password
        p=subprocess.run([str(binaries/binary),'-h',admin.url.host,'-p',str(admin.url.port),
            '-U',admin.url.username,*args],env=env,capture_output=True,text=True)
        if p.returncode:
            (report/(binary+'.private-error.txt')).write_text(p.stderr,encoding='utf-8')
        assert p.returncode==0, 'Focused restore command failed; private diagnostics retained'
    item=record();bind(runtime).save_once(item)
    with admin.connect() as c:
        before=c.execute(text('SELECT * FROM c06_results ORDER BY request_id')).all()
        sources=c.execute(text('SELECT * FROM c06_result_sources ORDER BY request_id,ordinal')).all()
    cli('pg_dump.exe','-d',admin.url.database,'-Fc','-f',str(dump))
    target=new_database(admin,'c06_restored_')
    try:
        with target.begin() as c:
            assert c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'"))==0
            c.exec_driver_sql('ALTER SCHEMA public OWNER TO c06_store_owner')
        cli('pg_restore.exe','-d',target.url.database,'--single-transaction','--exit-on-error','--no-owner','--role=c06_store_owner',str(dump))
        with target.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            pg=c.connection.driver_connection
            pg.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(target.url.database)))
            pg.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO c06_test_api').format(sql.Identifier(target.url.database)))
        with target.connect() as c:
            assert c.execute(text('SELECT * FROM c06_results ORDER BY request_id')).all()==before
            assert c.execute(text('SELECT * FROM c06_result_sources ORDER BY request_id,ordinal')).all()==sources
            assert c.scalar(text("SELECT bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class WHERE relname IN ('c06_results','c06_result_sources')"))
        restored=create_engine(runtime.url.set(database=target.url.database),hide_parameters=True)
        try:
            assert bind(restored).load_authorized('employee',item.request_id)==item
            with restored.begin() as c,pytest.raises(DBAPIError) as error:
                c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
                c.execute(text("INSERT INTO c06_result_sources VALUES (:id,'employee',1,'{}')"),dict(id=item.request_id))
            assert error.value.orig.sqlstate=='42501'
            # New writes still derive exact rows after restore; no txid reset or
            # special app bypass is needed for continued operation.
            fresh=record();bind(restored).save_once(fresh)
            assert bind(restored).load_authorized('employee',fresh.request_id)==fresh
        finally:restored.dispose()
    finally:target.dispose()


def test_inconsistent_legacy_history_blocks_upgrade_and_rolls_back_rls_changes(engines):
    admin,_=engines;legacy=new_database(admin,'c06_bad_history_')
    cfg=Config(str(BACKEND/'result_store_alembic.ini'));item=record()
    try:
        with legacy.begin() as c:
            c.exec_driver_sql('GRANT USAGE,CREATE ON SCHEMA public TO c06_store_owner')
            c.exec_driver_sql('SET LOCAL ROLE c06_store_owner')
            cfg.attributes.update(connection=c,expected_database=legacy.url.database,store_migration_approved=True)
            command.upgrade(cfg,'c06_0002')
            c.execute(text("SELECT set_config('app.c06_user_id','employee',true)"))
            c.execute(text("""INSERT INTO c06_results(request_id,owner_id,fingerprint,outcome,schema_version,payload,payload_hash)
                VALUES(:id,'employee',:fp,'answered',1,CAST(:payload AS jsonb),:hash)"""),
                dict(id=item.request_id,fp=item.fingerprint,payload=canonical(item.payload),hash=sha256(canonical(item.payload).encode()).hexdigest()))
        with legacy.connect() as c:before=c.execute(text('SELECT * FROM c06_results')).all()
        with pytest.raises(RuntimeError,match='historical source mismatch'):
            with legacy.begin() as c:
                c.exec_driver_sql('SET LOCAL ROLE c06_store_owner')
                cfg.attributes.update(connection=c)
                command.upgrade(cfg,'head')
        with legacy.connect() as c:
            assert c.scalar(text('SELECT version_num FROM c06_store_version'))=='c06_0002'
            assert c.execute(text('SELECT * FROM c06_results')).all()==before
            assert c.scalar(text("SELECT bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class WHERE relname IN ('c06_results','c06_result_sources')"))
            assert c.scalar(text("SELECT to_regprocedure('public.c06_derive_result_sources()')")) is None
    finally:legacy.dispose()
