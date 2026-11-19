"""Insert-only durable question intents so running/uncertain questions are auditable."""
from alembic import op

revision = "c06_0004"
down_revision = "c06_0003"
branch_labels = None
depends_on = None


def upgrade():
    c = op.get_bind()
    owned = c.exec_driver_sql("""SELECT count(*)=4 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN
          ('c06_results','c06_result_sources','c06_conversations','c06_turns')
          AND c.relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)""").scalar_one()
    if not owned:
        raise RuntimeError("C06 intents migration requires the limited existing schema owner")
    # One immutable row per owner/request key. The context revision is stored so that a
    # replay after the turn is linked rebuilds the identical command fingerprint.
    op.execute("""CREATE TABLE public.c06_question_intents(
        id uuid PRIMARY KEY, conversation_id uuid NOT NULL, owner_id varchar(36) NOT NULL,
        request_key varchar(80) NOT NULL CHECK(request_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]*$'),
        fingerprint char(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
        context_revision varchar(80) NOT NULL CHECK(length(context_revision) BETWEEN 1 AND 80),
        question text NOT NULL CHECK(length(trim(question)) BETWEEN 1 AND 4000),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(owner_id,request_key),
        FOREIGN KEY(conversation_id,owner_id) REFERENCES public.c06_conversations(id,owner_id))""")
    op.execute("CREATE INDEX c06_intents_conversation ON public.c06_question_intents(conversation_id,created_at)")
    op.execute("ALTER TABLE public.c06_question_intents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.c06_question_intents FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY owner_scope ON public.c06_question_intents USING
        (owner_id=current_setting('app.c06_user_id',true)) WITH CHECK
        (owner_id=current_setting('app.c06_user_id',true))""")
    op.execute("""CREATE TRIGGER refuse_mutation BEFORE UPDATE OR DELETE OR TRUNCATE
        ON public.c06_question_intents FOR EACH STATEMENT EXECUTE FUNCTION c06_refuse_mutation()""")
    op.execute("REVOKE ALL ON public.c06_question_intents FROM PUBLIC")


def downgrade():
    raise RuntimeError("Immutable question intents require an explicit recovery plan")
