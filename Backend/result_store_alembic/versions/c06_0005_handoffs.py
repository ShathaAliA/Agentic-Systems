"""Human handoff: insert-only, multi-party tables with row-level security and database-enforced state rules."""
from alembic import op

revision = "c06_0005"
down_revision = "c06_0004"
branch_labels = None
depends_on = None

PARTY_SELECT = ("requester_id = public.c06_actor() OR public.c06_is_staff(department_id) "
                "OR public.c06_is_audit()")


def upgrade():
    c = op.get_bind()
    owned = c.exec_driver_sql("""SELECT count(*)=5 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN
          ('c06_results','c06_result_sources','c06_conversations','c06_turns','c06_question_intents')
          AND c.relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)""").scalar_one()
    if not owned:
        raise RuntimeError("C06 handoff migration requires the limited existing schema owner")

    # Context set by trusted code per transaction (set_config(..., true)). Read-only helpers, invoker rights.
    op.execute("""CREATE FUNCTION public.c06_actor() RETURNS text LANGUAGE sql STABLE
        SET search_path=pg_catalog AS $$ SELECT current_setting('app.c06_user_id', true) $$""")
    op.execute("""CREATE FUNCTION public.c06_is_staff(dept text) RETURNS boolean LANGUAGE sql STABLE
        SET search_path=pg_catalog AS $$
        SELECT dept = ANY(string_to_array(COALESCE(current_setting('app.c06_staff_departments', true), ''), ',')) $$""")
    op.execute("""CREATE FUNCTION public.c06_is_audit() RETURNS boolean LANGUAGE sql STABLE
        SET search_path=pg_catalog AS $$ SELECT COALESCE(current_setting('app.c06_audit', true), '') = 'on' $$""")

    op.execute("""CREATE TABLE public.c06_handoffs(
        id uuid PRIMARY KEY, requester_id varchar(36) NOT NULL,
        department_id varchar(36) NOT NULL CHECK(department_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,35}$'),
        conversation_id uuid NOT NULL, turn_sequence bigint NOT NULL CHECK(turn_sequence>0),
        request_key varchar(80) NOT NULL CHECK(request_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]*$'),
        summary text NOT NULL CHECK(length(trim(summary)) BETWEEN 1 AND 4000),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(requester_id,request_key), UNIQUE(conversation_id,turn_sequence,department_id),
        UNIQUE(id,requester_id,department_id),
        FOREIGN KEY(conversation_id,requester_id) REFERENCES public.c06_conversations(id,owner_id),
        FOREIGN KEY(conversation_id,turn_sequence) REFERENCES public.c06_turns(conversation_id,sequence))""")
    op.execute("""CREATE TABLE public.c06_handoff_events(
        id uuid PRIMARY KEY, handoff_id uuid NOT NULL, requester_id varchar(36) NOT NULL,
        department_id varchar(36) NOT NULL, seq integer NOT NULL DEFAULT 0 CHECK(seq>0),
        kind text NOT NULL CHECK(kind IN ('created','claimed','transferred','closed','reopened')),
        actor_id varchar(36) NOT NULL, assignee_id varchar(36),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(handoff_id,seq),
        CHECK((kind IN ('claimed','transferred'))=(assignee_id IS NOT NULL)),
        FOREIGN KEY(handoff_id,requester_id,department_id)
            REFERENCES public.c06_handoffs(id,requester_id,department_id))""")
    op.execute("""CREATE TABLE public.c06_handoff_messages(
        id uuid PRIMARY KEY, handoff_id uuid NOT NULL, requester_id varchar(36) NOT NULL,
        department_id varchar(36) NOT NULL, sequence integer NOT NULL DEFAULT 0 CHECK(sequence>0),
        author_id varchar(36) NOT NULL, author_role text NOT NULL CHECK(author_role IN ('requester','staff')),
        body text NOT NULL CHECK(length(trim(body)) BETWEEN 1 AND 8000),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(handoff_id,sequence),
        FOREIGN KEY(handoff_id,requester_id,department_id)
            REFERENCES public.c06_handoffs(id,requester_id,department_id))""")
    op.execute("""CREATE TABLE public.c06_handoff_reads(
        id uuid PRIMARY KEY, handoff_id uuid NOT NULL, requester_id varchar(36) NOT NULL,
        department_id varchar(36) NOT NULL, reader_id varchar(36) NOT NULL,
        up_to_sequence integer NOT NULL CHECK(up_to_sequence>=0), created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(handoff_id,requester_id,department_id)
            REFERENCES public.c06_handoffs(id,requester_id,department_id))""")
    op.execute("""CREATE TABLE public.c06_handoff_access_log(
        id uuid PRIMARY KEY, actor_id varchar(36) NOT NULL, action text NOT NULL CHECK(action IN ('list','read')),
        handoff_id uuid, department_id varchar(36), created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX c06_handoffs_department ON public.c06_handoffs(department_id,created_at,id)")
    op.execute("CREATE INDEX c06_handoffs_requester ON public.c06_handoffs(requester_id,created_at,id)")
    op.execute("CREATE INDEX c06_handoff_reads_lookup ON public.c06_handoff_reads(handoff_id,reader_id,created_at)")

    tables = ("c06_handoffs", "c06_handoff_events", "c06_handoff_messages", "c06_handoff_reads",
              "c06_handoff_access_log")
    for table in tables:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE TRIGGER refuse_mutation BEFORE UPDATE OR DELETE OR TRUNCATE
            ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION c06_refuse_mutation()""")
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")

    # Visibility: the requester, staff of the department, or an audited Admin read.
    for table in ("c06_handoffs", "c06_handoff_events", "c06_handoff_messages"):
        op.execute(f"CREATE POLICY party_read ON public.{table} FOR SELECT USING ({PARTY_SELECT})")
    op.execute("""CREATE POLICY requester_insert ON public.c06_handoffs FOR INSERT
        WITH CHECK (requester_id = public.c06_actor())""")
    op.execute("""CREATE POLICY actor_insert ON public.c06_handoff_events FOR INSERT WITH CHECK (
        actor_id = public.c06_actor() AND (
            (kind = 'created' AND requester_id = public.c06_actor())
            OR (kind <> 'created' AND public.c06_is_staff(department_id))))""")
    op.execute("""CREATE POLICY author_insert ON public.c06_handoff_messages FOR INSERT WITH CHECK (
        author_id = public.c06_actor() AND (
            (author_role = 'requester' AND requester_id = public.c06_actor())
            OR (author_role = 'staff' AND public.c06_is_staff(department_id))))""")
    op.execute("CREATE POLICY own_read ON public.c06_handoff_reads FOR SELECT USING (reader_id = public.c06_actor())")
    op.execute("""CREATE POLICY own_insert ON public.c06_handoff_reads FOR INSERT WITH CHECK (
        reader_id = public.c06_actor() AND (requester_id = public.c06_actor() OR public.c06_is_staff(department_id)))""")
    op.execute("CREATE POLICY audit_read ON public.c06_handoff_access_log FOR SELECT USING (public.c06_is_audit())")
    op.execute("""CREATE POLICY audit_insert ON public.c06_handoff_access_log FOR INSERT
        WITH CHECK (actor_id = public.c06_actor() AND public.c06_is_audit())""")

    # Every handoff gets its `created` event atomically.
    op.execute("""CREATE FUNCTION public.c06_handoff_created() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$
        BEGIN
            INSERT INTO public.c06_handoff_events(id,handoff_id,requester_id,department_id,kind,actor_id)
            VALUES (gen_random_uuid(),NEW.id,NEW.requester_id,NEW.department_id,'created',NEW.requester_id);
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER created_event AFTER INSERT ON public.c06_handoffs
        FOR EACH ROW EXECUTE FUNCTION public.c06_handoff_created()""")

    # State machine and sequence under a per-handoff advisory lock (one winner for concurrent claims).
    op.execute("""CREATE FUNCTION public.c06_handoff_event_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$
        DECLARE previous record;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended('c06_handoff:' || NEW.handoff_id::text, 0));
            SELECT seq, kind INTO previous FROM public.c06_handoff_events
                WHERE handoff_id = NEW.handoff_id ORDER BY seq DESC LIMIT 1;
            IF NOT FOUND THEN
                IF NEW.kind <> 'created' THEN
                    RAISE EXCEPTION 'C06 handoff must start with created' USING ERRCODE='23514';
                END IF;
                NEW.seq := 1;
            ELSE
                IF NOT ((previous.kind IN ('created','reopened') AND NEW.kind = 'claimed')
                     OR (previous.kind IN ('claimed','transferred') AND NEW.kind IN ('transferred','closed'))
                     OR (previous.kind = 'closed' AND NEW.kind = 'reopened')) THEN
                    RAISE EXCEPTION 'C06 handoff transition refused' USING ERRCODE='23514';
                END IF;
                NEW.seq := previous.seq + 1;
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER event_guard BEFORE INSERT ON public.c06_handoff_events
        FOR EACH ROW EXECUTE FUNCTION public.c06_handoff_event_guard()""")

    op.execute("""CREATE FUNCTION public.c06_handoff_message_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$
        DECLARE latest record; assignee text;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended('c06_handoff:' || NEW.handoff_id::text, 0));
            SELECT kind INTO latest FROM public.c06_handoff_events
                WHERE handoff_id = NEW.handoff_id ORDER BY seq DESC LIMIT 1;
            IF NOT FOUND OR latest.kind = 'closed' THEN
                RAISE EXCEPTION 'C06 handoff is closed' USING ERRCODE='23514';
            END IF;
            IF NEW.author_role = 'staff' THEN
                SELECT assignee_id INTO assignee FROM public.c06_handoff_events
                    WHERE handoff_id = NEW.handoff_id AND kind IN ('claimed','transferred')
                    ORDER BY seq DESC LIMIT 1;
                IF latest.kind NOT IN ('claimed','transferred') OR assignee IS DISTINCT FROM NEW.author_id THEN
                    RAISE EXCEPTION 'C06 handoff reply needs the current assignee' USING ERRCODE='23514';
                END IF;
            END IF;
            NEW.sequence := COALESCE((SELECT max(sequence) FROM public.c06_handoff_messages
                WHERE handoff_id = NEW.handoff_id), 0) + 1;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER message_guard BEFORE INSERT ON public.c06_handoff_messages
        FOR EACH ROW EXECUTE FUNCTION public.c06_handoff_message_guard()""")

    op.execute("""CREATE FUNCTION public.c06_handoff_read_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$
        BEGIN
            IF NEW.up_to_sequence > COALESCE((SELECT max(sequence) FROM public.c06_handoff_messages
                    WHERE handoff_id = NEW.handoff_id), 0) THEN
                RAISE EXCEPTION 'C06 read receipt is past the last message' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER read_guard BEFORE INSERT ON public.c06_handoff_reads
        FOR EACH ROW EXECUTE FUNCTION public.c06_handoff_read_guard()""")
    for name in ("c06_handoff_created", "c06_handoff_event_guard", "c06_handoff_message_guard", "c06_handoff_read_guard"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC")


def downgrade():
    raise RuntimeError("Immutable handoff history requires an explicit recovery plan")
