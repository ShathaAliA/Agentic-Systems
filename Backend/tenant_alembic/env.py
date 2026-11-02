"""Tenant migration interface: caller supplies an owner-scoped connection."""
from alembic import context

connection = context.config.attributes.get("connection")
if connection is None or context.is_offline_mode():
    raise RuntimeError("Tenant migrations require an explicit owner-scoped connection")

# Do not import or autogenerate another developer's document models here.
context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
