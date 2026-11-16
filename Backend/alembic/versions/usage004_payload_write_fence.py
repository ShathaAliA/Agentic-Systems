"""Prevent new Central business payload writes; retain legacy data and metadata updates.

Forward-only compatibility fence. Applied history is unchanged. Legacy content
requires a separate reviewed retention/transfer plan; never purge/copy it here.
"""
from alembic import op
revision = "usage004_fence"
down_revision = "usage003_merge"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""CREATE FUNCTION public.reject_new_usage_payload() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.result_payload IS NOT NULL THEN
                    RAISE EXCEPTION 'Central business payload writes are disabled' USING ERRCODE='23514';
                END IF;
            ELSIF NEW.result_payload::jsonb IS DISTINCT FROM OLD.result_payload::jsonb THEN
                RAISE EXCEPTION 'Central business payload writes are disabled' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("CREATE TRIGGER usage_payload_fence BEFORE INSERT OR UPDATE ON public.usage_requests FOR EACH ROW EXECUTE FUNCTION public.reject_new_usage_payload()")

def downgrade():
    raise RuntimeError("Payload fence rollback requires an explicit recovery plan")
