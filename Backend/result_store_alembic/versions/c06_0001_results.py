"""Independent C06 result store, never a Central or tenant migration."""
from alembic import op

revision = "c06_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE c06_store_identity(
        singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
        kind text NOT NULL CHECK(kind='conversation_results'), version integer NOT NULL CHECK(version=1))""")
    op.execute("INSERT INTO c06_store_identity VALUES(true,'conversation_results',1)")
    op.execute("""CREATE TABLE c06_results(
        request_id uuid PRIMARY KEY, owner_id varchar(36) NOT NULL,
        fingerprint char(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
        outcome text NOT NULL CHECK(outcome IN ('answered','no_answer')),
        schema_version integer NOT NULL CHECK(schema_version=1),
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
        payload_hash char(64) NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
        created_at timestamptz NOT NULL DEFAULT now(), writer_txid bigint NOT NULL DEFAULT txid_current(),
        UNIQUE(request_id,owner_id))""")
    op.execute("""CREATE TABLE c06_result_sources(
        request_id uuid NOT NULL, owner_id varchar(36) NOT NULL,
        ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 29),
        source jsonb NOT NULL CHECK(jsonb_typeof(source)='object'),
        PRIMARY KEY(request_id,ordinal), FOREIGN KEY(request_id,owner_id)
            REFERENCES c06_results(request_id,owner_id) ON DELETE RESTRICT)""")
    op.execute("""CREATE FUNCTION c06_refuse_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'C06 immutable history' USING ERRCODE='42501'; END $$""")
    op.execute("""CREATE FUNCTION c06_sources_same_transaction() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.c06_results WHERE request_id=NEW.request_id
                AND owner_id=NEW.owner_id AND writer_txid=txid_current()) THEN
                RAISE EXCEPTION 'C06 result is sealed' USING ERRCODE='42501';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER same_transaction BEFORE INSERT ON c06_result_sources
        FOR EACH ROW EXECUTE FUNCTION c06_sources_same_transaction()""")
    for table in ("c06_results", "c06_result_sources", "c06_store_identity"):
        op.execute(f"CREATE TRIGGER refuse_mutation BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION c06_refuse_mutation()")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    for table in ("c06_results", "c06_result_sources"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY owner_scope ON {table} USING
            (owner_id=current_setting('app.c06_user_id',true)) WITH CHECK
            (owner_id=current_setting('app.c06_user_id',true))""")
    op.execute("REVOKE ALL ON FUNCTION c06_refuse_mutation() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION c06_sources_same_transaction() FROM PUBLIC")


def downgrade():
    raise RuntimeError("Immutable store downgrade requires an explicit recovery plan")
