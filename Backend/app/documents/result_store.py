"""Legacy tenant results are retained for audit, never used for new answers.

Use execute_with_result_store + the independent Core PostgresResultStore.
Historical rows/schema are deliberately retained, not copied or deleted.
"""
from app.usage.service import UsageError


def save(*args, **kwargs):
    raise UsageError(503, "legacy_tenant_result_store_disabled")


def load(*args, **kwargs):
    raise UsageError(503, "legacy_tenant_result_store_disabled")
