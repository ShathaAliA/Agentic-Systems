"""Owner-scoped append-only conversations and result-referencing turns."""
from alembic import op

revision = "c06_0002"
down_revision = "c06_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE public.c06_conversations(
        id uuid PRIMARY KEY, owner_id varchar(36) NOT NULL,
        creation_key varchar(80) NOT NULL, mode text NOT NULL CHECK(mode IN ('department','orchestrator')),
        departments jsonb NOT NULL CHECK(jsonb_typeof(departments)='array'),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id), UNIQUE(owner_id,creation_key))""")
    op.execute("""CREATE TABLE public.c06_turns(
        id uuid PRIMARY KEY, conversation_id uuid NOT NULL, owner_id varchar(36) NOT NULL,
        sequence bigint NOT NULL CHECK(sequence>0), request_id uuid NOT NULL UNIQUE,
        fingerprint char(64) NOT NULL, question text NOT NULL CHECK(length(trim(question)) BETWEEN 1 AND 4000),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(conversation_id,sequence),
        FOREIGN KEY(conversation_id,owner_id) REFERENCES public.c06_conversations(id,owner_id),
        FOREIGN KEY(request_id,owner_id) REFERENCES public.c06_results(request_id,owner_id))""")
    op.execute("CREATE INDEX c06_conversation_owner_page ON public.c06_conversations(owner_id,created_at,id)")
    for table in ("c06_conversations", "c06_turns"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY owner_scope ON public.{table} USING
            (owner_id=current_setting('app.c06_user_id',true)) WITH CHECK
            (owner_id=current_setting('app.c06_user_id',true))""")
        op.execute(f"CREATE TRIGGER refuse_mutation BEFORE UPDATE OR DELETE OR TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION c06_refuse_mutation()")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")


def downgrade():
    raise RuntimeError("Immutable conversation history requires an explicit recovery plan")
