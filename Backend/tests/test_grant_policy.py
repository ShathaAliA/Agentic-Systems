import pytest
from unittest.mock import Mock
from app.credentials import Credentials
from app.provisioning.errors import ProvisioningError
from app.provisioning.postgres import PostgresProvisioner, permission_policy


@pytest.mark.parametrize("policy,sequences", [(None, None),
    ({"_provisioning_identity": ["UPDATE"]}, {}), ({"probe": ["SELECT", "SELECT"]}, {}),
    ({"probe": ["TRUNCATE"]}, {}), ({"probe": "SELECT"}, {}),
    ({"unknown": ["SELECT"]}, {}), ({"probe": ["select"]}, {}),
    ({"probe": ["SELECT"]}, {"probe_id_seq": ["UPDATE"]}),
    ({"probe": ["SELECT"]}, {"unknown": ["USAGE"]})])
def test_missing_invalid_or_excessive_policy_is_rejected(policy, sequences):
    with pytest.raises(ProvisioningError):
        permission_policy({"probe", "_provisioning_identity"}, {"probe_id_seq"}, policy, sequences)


def test_foundation_and_explicit_deny_have_no_implicit_business_grants():
    assert permission_policy({"_provisioning_identity", "alembic_version"}, set(), None, None) == ({}, {})
    assert permission_policy({"probe"}, {"probe_id_seq"}, {}, None) == ({}, {})
    tables, seq = permission_policy({"probe"}, {"probe_id_seq"}, {"probe": ["SELECT", "INSERT"]}, {"probe_id_seq": ["USAGE"]})
    assert tables == {"probe": {"SELECT", "INSERT"}} and seq == {"probe_id_seq": {"USAGE"}}


@pytest.mark.parametrize("method", ["grant_runtime", "verify"])
def test_unmanaged_grant_or_verification_target_is_rejected_before_connection(method):
    pg = PostgresProvisioner("postgresql://synthetic@localhost/postgres")
    pg.connect = Mock()
    args = ["unmanaged", "unmanaged_owner", Credentials("unmanaged_app", "synthetic-unused")]
    if method == "verify": args += ["synthetic", "tenant_0001", ()]
    with pytest.raises(ProvisioningError, match="unmanaged_database_identity"):
        getattr(pg, method)(*args)
    pg.connect.assert_not_called()
