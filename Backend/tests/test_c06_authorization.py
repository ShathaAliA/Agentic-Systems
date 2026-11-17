"""Offline Core contract tests; no tenant/LLM integration claim."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from app.conversation_store.authorization import (
    bind_conversation, ConversationResultStoreFactory,
)
from app.conversation_store.postgres import PostgresResultStore
from app.usage.service import UsageError
from c06_store_fixtures import DEPARTMENT, answer


def metadata_service():
    service = Mock()
    service.store.user_id = "employee"
    service.get.return_value = dict(conversation_id=answer()["conversation_id"],
        mode="department", department_ids=[DEPARTMENT])
    service.scope_allowed.return_value = True
    return service


@pytest.mark.parametrize("identity", ["bad", None, 1])
def test_invalid_binding_id_never_looks_up_metadata(identity):
    service = metadata_service()
    with pytest.raises(UsageError, match="conversation_not_found"):
        bind_conversation(service, identity)
    service.get.assert_not_called()


def test_binding_derives_immutable_scope_and_rejects_asserted_department():
    service = metadata_service()
    bound = bind_conversation(service, answer()["conversation_id"], DEPARTMENT)
    assert bound.department_ids == (DEPARTMENT,)
    with pytest.raises(UsageError, match="scope_forbidden"):
        bind_conversation(service, bound.conversation_id, str(uuid4()))
    service.scope_allowed.return_value = False
    with pytest.raises(UsageError, match="scope_forbidden"):
        bind_conversation(service, bound.conversation_id)


def test_factory_never_mutates_cached_adapter_or_connects_for_metadata():
    engine = Mock(dialect=SimpleNamespace(name="postgresql"), url=SimpleNamespace(database="test"))
    callback = Mock(return_value=True)
    base = PostgresResultStore(engine, expected_database="test",
                              authenticated_user_id="employee", source_authorizer=callback)
    factory = ConversationResultStoreFactory(lambda _: base, Mock())
    first = factory.metadata_for("employee")
    second = factory.metadata_for("employee")
    assert first is not second and first is not base and base.authorize is callback
    engine.begin.assert_not_called()
    with pytest.raises(UsageError, match="configuration_invalid"):
        factory.metadata_for("admin")


@pytest.mark.parametrize("decision", [False, None, 1, "yes", {}])
def test_provider_requires_literal_true(decision):
    service = metadata_service()
    provider = Mock()
    provider.authorizer_for.return_value = Mock(return_value=decision)
    factory = ConversationResultStoreFactory(Mock(), Mock(), provider)
    with patch("app.conversation_store.authorization.bind_conversation",
               return_value=SimpleNamespace(user_id="employee", conversation_id=answer()["conversation_id"],
                                            department_ids=(DEPARTMENT,))):
        with pytest.raises(UsageError, match="access_revoked"):
            factory._authorizer(service)("employee", answer())


def test_provider_receives_tuple_from_binding_and_rechecks_after_callback():
    service = metadata_service()
    provider = Mock()
    def revoke(*_):
        service.scope_allowed.return_value = False
        return True
    provider.authorizer_for.return_value = revoke
    factory = ConversationResultStoreFactory(Mock(), Mock(), provider)
    with pytest.raises(UsageError, match="access_revoked"):
        factory._authorizer(service)("employee", answer())
    provider.authorizer_for.assert_called_once_with("employee", answer()["conversation_id"], (DEPARTMENT,))


def test_multi_department_metadata_is_supported_but_single_department_shim_refuses_it():
    service = metadata_service()
    service.get.return_value.update(mode="orchestrator", department_ids=[DEPARTMENT, str(uuid4())])
    assert len(bind_conversation(service, answer()["conversation_id"]).department_ids) == 2
    with pytest.raises(UsageError, match="scope_forbidden"):
        bind_conversation(service, answer()["conversation_id"], DEPARTMENT)


def test_direct_shim_callback_copies_payload_and_sanitizes_provider_errors():
    service = metadata_service()
    provider = Mock()
    payload = answer()
    def mutate(_, data):
        data["answer"] = "changed"; data["sources"].clear()
        return True
    provider.authorizer_for.return_value = mutate
    callback = ConversationResultStoreFactory(Mock(), Mock(), provider)._authorizer(service)
    assert callback("employee", payload) is True
    assert payload["sources"] and payload["answer"] != "changed"
    provider.authorizer_for.side_effect = RuntimeError("synthetic-private-detail")
    with pytest.raises(UsageError) as error: callback("employee", payload)
    assert error.value.status == 503 and "synthetic-private-detail" not in str(error.value)
