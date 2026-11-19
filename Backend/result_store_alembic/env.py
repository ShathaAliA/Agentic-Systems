"""Store migrations require an explicitly supplied owner connection; no DSN lookup."""
from alembic import context

connection = context.config.attributes.get("connection")
database = context.config.attributes.get("expected_database")
if connection is None or not database or context.is_offline_mode():
    raise RuntimeError("Explicit isolated store migration connection required")
if connection.exec_driver_sql("SELECT current_database()").scalar_one() != database:
    raise RuntimeError("Store migration database mismatch")
if context.config.attributes.get("store_migration_approved") is not True:
    raise RuntimeError("Store migration target requires explicit approval")
context.configure(connection=connection, target_metadata=None, version_table="c06_store_version")
with context.begin_transaction():
    context.run_migrations()
