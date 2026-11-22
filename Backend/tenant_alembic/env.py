"""Isolated result store migration interface: caller supplies a scoped connection."""
from alembic import context

if connection is None or context.is_offline_mode():
    raise RuntimeError("Explicit isolated store migration connection required")

context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
    