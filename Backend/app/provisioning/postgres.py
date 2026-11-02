"""PostgreSQL resource adapter. Never drops, renames or adopts unmanaged resources."""
import re
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from .errors import ProvisioningError


def identities(database, credential_key):
    if not re.fullmatch(r"dept_[0-9a-f]{32}", database) or credential_key != database.upper():
        raise ProvisioningError("unmanaged_database_identity")
    return database + "_owner", database + "_app"


class PostgresProvisioner:
    def __init__(self, admin_url):
        self.url = make_url(admin_url)

    def connect(self, database=None, credentials=None):
        try:
            return psycopg.connect(
                host=self.url.host, port=self.url.port or 5432,
                dbname=database or self.url.database,
                user=credentials.username if credentials else self.url.username,
                password=credentials.password if credentials else self.url.password,
                connect_timeout=10, autocommit=True,
                **{k: v for k, v in self.url.query.items() if k not in {"connect_timeout"}},
            )
        except psycopg.Error as exc:
            denied = exc.sqlstate == "42501" or "permission denied for database" in str(exc)
            # psycopg connection tracebacks may include conninfo/password locals.
            # Never let those frames escape this boundary, even to a test runner.
            raise ProvisioningError("database_connect_denied" if denied else "database_connection_failed") from None

    def ensure_resources(self, database, owner, credentials, department_id):
        marker = "ai-platform:department:" + department_id
        with self.connect() as conn:
            provisioner = conn.execute("SELECT current_user").fetchone()[0]
            with conn.transaction():
                for name, login in [(owner, False), (credentials.username, True)]:
                    row = conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin,shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
                    if row is None:
                        password = sql.SQL(" PASSWORD {} ").format(sql.Literal(credentials.password)) if login else sql.SQL("")
                        conn.execute(sql.SQL("CREATE ROLE {} {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS {}").format(sql.Identifier(name), sql.SQL("LOGIN" if login else "NOLOGIN"), password))
                        conn.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(sql.Identifier(name), sql.Literal(marker)))
                    elif any(row[:5]) or row[5] != login or row[6] != marker:
                        raise ProvisioningError("role_ownership_or_privilege_conflict")
                conn.execute(sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET TRUE").format(sql.Identifier(owner), sql.Identifier(provisioner)))
            row = conn.execute("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s", (database,)).fetchone()
            if row is None:
                # CREATE DATABASE cannot run in a transaction. The previously
                # committed owner marker is the recovery proof for this window.
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(sql.Identifier(database), sql.Identifier(owner)))
            elif row[0] != owner or row[1] not in (None, marker):
                raise ProvisioningError("database_ownership_conflict")
            conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(database), sql.Literal(marker)))
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(credentials.username)))

    def migration_url(self, database):
        return self.url.set(drivername="postgresql+psycopg", database=database)

    def grant_runtime(self, database, owner, credentials):
        with self.connect(database) as conn, conn.transaction():
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
            conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(credentials.username)))
            rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
            for (table,) in rows:
                grant = "SELECT" if table in {"alembic_version", "_provisioning_identity"} else "SELECT, INSERT, UPDATE, DELETE"
                conn.execute(sql.SQL("GRANT {} ON TABLE public.{} TO {}").format(sql.SQL(grant), sql.Identifier(table), sql.Identifier(credentials.username)))
            conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(credentials.username)))
            conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(sql.Identifier(credentials.username)))
            conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(sql.Identifier(credentials.username)))

    def verify(self, database, owner, credentials, department_id, revision, required_tables):
        with self.connect(database, credentials) as conn:
            identity = conn.execute("SELECT current_database(),current_user").fetchone()
            if identity != (database, credentials.username):
                raise ProvisioningError("connectivity_identity_mismatch")
            row = conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
            if any(row) or conn.execute("SELECT pg_has_role(current_user,%s,'MEMBER')", (owner,)).fetchone()[0]:
                raise ProvisioningError("runtime_elevated")
            if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=current_user)").fetchone()[0]:
                raise ProvisioningError("runtime_role_membership")
            if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE r.rolname=current_user AND n.nspname='public')").fetchone()[0]:
                raise ProvisioningError("runtime_object_owner")
            if conn.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE') OR has_database_privilege(current_user,current_database(),'TEMP') OR has_schema_privilege(current_user,'public','CREATE')").fetchone()[0]:
                raise ProvisioningError("runtime_ddl_privilege")
            # Cluster ACL policy is an operator prerequisite; never modify HR,
            # Finance or any other database to make this check pass.
            if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datallowconn AND datname<>current_database() AND has_database_privilege(current_user,oid,'CONNECT'))").fetchone()[0]:
                raise ProvisioningError("cross_database_connect")
            if conn.execute("SELECT department_id FROM public._provisioning_identity WHERE singleton=1").fetchone() != (department_id,):
                raise ProvisioningError("tenant_identity_conflict")
            if conn.execute("SELECT version_num FROM public.alembic_version").fetchone() != (revision,):
                raise ProvisioningError("migration_revision_mismatch")
            for table in required_tables:
                if not conn.execute("SELECT has_table_privilege(current_user,%s,'SELECT')", ("public." + table,)).fetchone()[0]:
                    raise ProvisioningError("required_table_permission_missing")
            # Negative checks execute in rolled-back transactions. Do not just
            # trust the absence of role flags.
            for statement in ["CREATE SCHEMA _provisioning_denied", "CREATE TABLE public._provisioning_denied(id int)", "CREATE TEMP TABLE _provisioning_denied(id int)", "UPDATE public._provisioning_identity SET department_id=department_id"]:
                try:
                    with conn.transaction(force_rollback=True):
                        conn.execute(statement)
                        raise ProvisioningError("runtime_negative_check_failed")
                except psycopg.errors.InsufficientPrivilege:
                    pass
