SAFE_ERROR_CODES = frozenset({
    "migration_contract_missing", "invalid_required_table", "migration_revision_not_pinned",
    "tenant_identity_conflict", "required_table_missing", "migration_revision_mismatch",
    "unmanaged_database_identity", "role_ownership_or_privilege_conflict", "database_ownership_conflict",
    "database_connect_denied", "database_connection_failed", "connectivity_identity_mismatch",
    "runtime_elevated", "runtime_role_membership", "runtime_object_owner", "runtime_ddl_privilege",
    "cross_database_connect", "required_table_permission_missing", "runtime_negative_check_failed",
    "department_not_registered", "department_state_conflict", "attempts_exhausted", "stale_claim",
    "runtime_grant_policy_missing", "runtime_grant_policy_invalid", "runtime_permission_mismatch", "runtime_default_privileges",
    "tenant_contract_unavailable", "infrastructure_error",
})


class ProvisioningError(Exception):
    """Only this bounded code may be persisted/logged; never raw driver errors."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)
