from sqlalchemy import event
import logging

logger = logging.getLogger(__name__)

def restrict_runtime_engine(engine):
    @event.listens_for(engine, "connect")
    def verify(dbapi_connection, _record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute("""
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
                    EXISTS(SELECT 1 FROM pg_roles inherited
                        WHERE pg_has_role(current_user,inherited.oid,'MEMBER')
                        AND (inherited.rolsuper OR inherited.rolcreatedb
                             OR inherited.rolcreaterole OR inherited.rolreplication OR inherited.rolbypassrls
                             OR left(inherited.rolname,3)='pg_')),
                    EXISTS(SELECT 1 FROM pg_database
                        WHERE datname=current_database()
                        AND pg_has_role(current_user,datdba,'MEMBER'))
                FROM pg_roles WHERE rolname=current_user
            """)
            if any(cursor.fetchone()):
                # تحذير بدل توقف
                logger.warning("WARNING: Using administrative credentials in FastAPI runtime. This is not recommended for production.")
        dbapi_connection.rollback()
    return engine