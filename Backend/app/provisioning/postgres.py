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


FOUNDATION = frozenset({"alembic_version", "_provisioning_identity"})
TABLE_OPERATIONS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
SEQUENCE_OPERATIONS = ("USAGE", "SELECT", "UPDATE")


def permission_policy(tables, sequences, table_privileges, sequence_privileges):
    """Internal reviewed policy interface; never inferred from document models."""
    if table_privileges is None:
        if tables - FOUNDATION:
            raise ProvisioningError("runtime_grant_policy_missing")
        table_privileges = {}
    def checked(policy, existing, allowed):
        if not isinstance(policy, dict):
            raise ProvisioningError("runtime_grant_policy_invalid")
        result = {}
        for name, operations in policy.items():
            if (not isinstance(name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name)
                    or name not in existing or not isinstance(operations, (tuple, list, set, frozenset))
                    or any(not isinstance(op, str) or op not in allowed for op in operations)
                    or len(set(operations)) != len(operations)):
                raise ProvisioningError("runtime_grant_policy_invalid")
            result[name] = frozenset(operations)
        return result
    return (checked(table_privileges, tables - FOUNDATION, {"SELECT", "INSERT", "UPDATE", "DELETE"}),
            checked(sequence_privileges if sequence_privileges is not None else {}, sequences, {"USAGE", "SELECT"}))


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
        expected_owner, expected_runtime = identities(database, database.upper())
        if owner != expected_owner or credentials.username != expected_runtime:
            raise ProvisioningError("unmanaged_database_identity")
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

    def _policy(self, conn, table_privileges, sequence_privileges):
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
        sequences = {r[0] for r in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public'").fetchall()}
        table_policy, sequence_policy = permission_policy(tables, sequences, table_privileges, sequence_privileges)
        return tables, sequences, table_policy, sequence_policy

    def _verify_object_acl(self, conn, kind, name, runtime, allowed):
        # Include future PostgreSQL privilege types and GRANT OPTION, rather
        # than relying solely on the known has_*_privilege operation lists.
        excessive = conn.execute("""
            SELECT EXISTS(SELECT 1 FROM pg_class object
                CROSS JOIN LATERAL (
                    SELECT entry.* FROM aclexplode(COALESCE(object.relacl,
                        acldefault(%s::"char",object.relowner))) entry
                    UNION ALL
                    SELECT entry.* FROM pg_attribute attribute
                        CROSS JOIN LATERAL aclexplode(attribute.attacl) entry
                        WHERE attribute.attrelid=object.oid
                ) acl
                WHERE object.oid=to_regclass(%s)
                AND acl.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname=%s))
                AND (acl.is_grantable OR NOT (acl.privilege_type=ANY(%s))))
        """, (kind, "public." + name, runtime, list(allowed))).fetchone()[0]
        if excessive:
            raise ProvisioningError("runtime_permission_mismatch")

    def _verify_policy(self, conn, owner, runtime, policy):
        tables, sequences, table_policy, sequence_policy = policy
        for table in tables:
            allowed = {"SELECT"} if table in FOUNDATION else table_policy.get(table, frozenset())
            self._verify_object_acl(conn, "r", table, runtime, allowed)
            for operation in TABLE_OPERATIONS:
                actual = conn.execute("SELECT has_table_privilege(%s,%s,%s)", (runtime, "public." + table, operation)).fetchone()[0]
                if actual != (operation in allowed):
                    raise ProvisioningError("runtime_permission_mismatch")
                if operation in {"SELECT", "INSERT", "UPDATE", "REFERENCES"} and operation not in allowed:
                    if conn.execute("SELECT has_any_column_privilege(%s,%s,%s)", (runtime, "public." + table, operation)).fetchone()[0]:
                        raise ProvisioningError("runtime_permission_mismatch")
        for sequence in sequences:
            self._verify_object_acl(conn, "S", sequence, runtime, sequence_policy.get(sequence, frozenset()))
            for operation in SEQUENCE_OPERATIONS:
                actual = conn.execute("SELECT has_sequence_privilege(%s,%s,%s)", (runtime, "public." + sequence, operation)).fetchone()[0]
                if actual != (operation in sequence_policy.get(sequence, frozenset())):
                    raise ProvisioningError("runtime_permission_mismatch")
        if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a WHERE d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=%s) AND d.defaclnamespace IN (0,(SELECT oid FROM pg_namespace WHERE nspname='public')) AND d.defaclobjtype IN ('r','S') AND a.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname=%s)))", (owner, runtime)).fetchone()[0]:
            raise ProvisioningError("runtime_default_privileges")

    def grant_runtime(self, database, owner, credentials, *, table_privileges=None, sequence_privileges=None):
        expected_owner, expected_runtime = identities(database, database.upper())
        if owner != expected_owner or credentials.username != expected_runtime:
            raise ProvisioningError("unmanaged_database_identity")
        with self.connect(database) as conn, conn.transaction():
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
            policy = self._policy(conn, table_privileges, sequence_privileges)
            tables, sequences, table_policy, sequence_policy = policy
            conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(credentials.username)))
            # Reconcile only this managed application's grants; never revoke
            # another identity's legitimate rights or silently repair PUBLIC leaks.
            for kind in ("TABLES", "SEQUENCES"):
                conn.execute(sql.SQL("REVOKE ALL ON ALL {} IN SCHEMA public FROM {}").format(sql.SQL(kind), sql.Identifier(credentials.username)))
                conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON {} FROM {}").format(sql.SQL(kind), sql.Identifier(credentials.username)))
            for table in sorted(tables):
                operations = {"SELECT"} if table in FOUNDATION else table_policy.get(table, frozenset())
                if operations:
                    conn.execute(sql.SQL("GRANT {} ON TABLE public.{} TO {}").format(sql.SQL(", ".join(sorted(operations))), sql.Identifier(table), sql.Identifier(credentials.username)))
            for sequence, operations in sequence_policy.items():
                if operations:
                    conn.execute(sql.SQL("GRANT {} ON SEQUENCE public.{} TO {}").format(sql.SQL(", ".join(sorted(operations))), sql.Identifier(sequence), sql.Identifier(credentials.username)))
            self._verify_policy(conn, owner, credentials.username, policy)

    def verify(self, database, owner, credentials, department_id, revision, required_tables, *, table_privileges=None, sequence_privileges=None):
        expected_owner, expected_runtime = identities(database, database.upper())
        if owner != expected_owner or credentials.username != expected_runtime:
            raise ProvisioningError("unmanaged_database_identity")
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
            self._verify_policy(conn, owner, credentials.username, self._policy(conn, table_privileges, sequence_privileges))
            # Negative checks execute in rolled-back transactions. Do not just
            # trust the absence of role flags.
            for statement in ["CREATE SCHEMA _provisioning_denied", "CREATE TABLE public._provisioning_denied(id int)", "CREATE TEMP TABLE _provisioning_denied(id int)", "UPDATE public._provisioning_identity SET department_id=department_id"]:
                try:
                    with conn.transaction(force_rollback=True):
                        conn.execute(statement)
                        raise ProvisioningError("runtime_negative_check_failed")
                except psycopg.errors.InsufficientPrivilege:
                    pass
