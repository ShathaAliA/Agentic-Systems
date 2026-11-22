"""Derive immutable source rows at result creation; no transaction-ID seal."""
from alembic import op

revision = "c06_0003"
down_revision = "c06_0002"
branch_labels = None
depends_on = None


def upgrade():
    c = op.get_bind()
    # Definer code must never be owned by an elevated bootstrap identity.
    unsafe = c.exec_driver_sql("""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
        WHERE pg_has_role(current_user,r.oid,'MEMBER') AND
        (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
         OR r.rolbypassrls OR left(r.rolname,3)='pg_'))""").scalar_one()
    owned = c.exec_driver_sql("""SELECT count(*)=2 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN ('c06_results','c06_result_sources')
          AND c.relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
          AND c.relrowsecurity AND c.relforcerowsecurity""").scalar_one()
    if unsafe or not owned:
        raise RuntimeError("C06 sealing migration requires the limited existing schema owner")
    # DDL locks + one migration transaction: no unforced-RLS state is committed.
    # The owner must inspect ALL historical rows, not only one RLS user context.
    for table in ('c06_results','c06_result_sources'):
        op.execute(f'ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY')
    corrupt = c.exec_driver_sql("""SELECT EXISTS(SELECT 1 FROM public.c06_results r
        WHERE CASE WHEN jsonb_typeof(r.payload->'sources') IS DISTINCT FROM 'array' THEN true
        ELSE jsonb_array_length(r.payload->'sources')>30 OR
          (SELECT COALESCE(jsonb_agg(s.source ORDER BY s.ordinal),'[]'::jsonb)
             FROM public.c06_result_sources s WHERE s.request_id=r.request_id)
             IS DISTINCT FROM r.payload->'sources' END)
        OR EXISTS(SELECT 1 FROM public.c06_result_sources s JOIN public.c06_results r USING(request_id)
           WHERE r.owner_id<>s.owner_id OR r.payload->'sources'->s.ordinal IS DISTINCT FROM s.source)""").scalar_one()
    if corrupt:
        raise RuntimeError("C06 historical source mismatch requires explicit recovery review")
    for table in ('c06_results','c06_result_sources'):
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE OR REPLACE FUNCTION public.c06_sources_same_transaction()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF pg_trigger_depth()<>2 OR NOT EXISTS(
                SELECT 1 FROM public.c06_results r WHERE r.request_id=NEW.request_id
                AND r.owner_id=NEW.owner_id
                AND r.payload->'sources'->NEW.ordinal = NEW.source) THEN
                RAISE EXCEPTION 'C06 result is sealed' USING ERRCODE='42501';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE FUNCTION public.c06_derive_result_sources() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$
        BEGIN
            IF jsonb_typeof(NEW.payload->'sources') IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'C06 sources must be an array' USING ERRCODE='23514';
            END IF;
            IF jsonb_array_length(NEW.payload->'sources')>30 THEN
                RAISE EXCEPTION 'C06 source limit exceeded' USING ERRCODE='23514';
            END IF;
            INSERT INTO public.c06_result_sources(request_id,owner_id,ordinal,source)
                SELECT NEW.request_id,NEW.owner_id,(ordinality-1)::integer,value
                FROM jsonb_array_elements(NEW.payload->'sources') WITH ORDINALITY;
            RETURN NEW;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION public.c06_derive_result_sources() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.c06_sources_same_transaction() FROM PUBLIC")
    op.execute("""CREATE TRIGGER derive_result_sources AFTER INSERT ON public.c06_results
        FOR EACH ROW EXECUTE FUNCTION public.c06_derive_result_sources()""")


def downgrade():
    raise RuntimeError("Sealed history downgrade requires an explicit recovery plan")
