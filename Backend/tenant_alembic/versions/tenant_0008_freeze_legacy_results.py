"""Freeze historical tenant answers without deleting or copying them.

New answers belong exclusively to the independent Core result store.
"""
from alembic import op
revision = "tenant_0008"
down_revision = "tenant_0007"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""CREATE FUNCTION public.reject_legacy_question_result_change() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN RAISE EXCEPTION 'Legacy tenant results are read-only' USING ERRCODE='23514'; END $$""")
    op.execute("CREATE TRIGGER legacy_question_results_rows BEFORE INSERT OR UPDATE OR DELETE ON public.question_results FOR EACH ROW EXECUTE FUNCTION public.reject_legacy_question_result_change()")
    op.execute("CREATE TRIGGER legacy_question_results_truncate BEFORE TRUNCATE ON public.question_results FOR EACH STATEMENT EXECUTE FUNCTION public.reject_legacy_question_result_change()")

def downgrade():
    raise RuntimeError("Legacy result rollback requires an explicit recovery plan")
