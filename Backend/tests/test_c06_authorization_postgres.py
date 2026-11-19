"""Real result DB, synthetic SQLite Central and provider; fresh clusters only."""
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Department, DepartmentMembership, RoleAssignment, User
from app.conversation_store.authorization import ConversationResultStoreFactory
from app.conversation_store.conversations import ConversationService
from app.usage.service import UsageError
from c06_store_fixtures import DEPARTMENT, answer
from test_c06_result_store_postgres import engines, store, record, pytestmark
from test_c06_conversations_postgres import context, create, result
from test_usage import usage


def factory(context, provider):
    _, central, _, _, runtime = context
    return ConversationResultStoreFactory(lambda user: store(runtime, user=user), central, provider)


def saved(context, identity, *, no_answer=False, other_source=False):
    service = context[0]
    item = record(outcome="no_answer" if no_answer else "answered")
    item.payload["conversation_id"] = identity
    if other_source:
        item.payload["sources"][0]["department_id"] = str(uuid4())
    service.store.save_once(item)
    return item


@pytest.mark.parametrize("decision", [True, False, None, 1, "yes"])
def test_bound_delivery_requires_explicit_source_approval(context, decision):
    identity = create(context[0]); item = saved(context, identity)
    provider = Mock(); provider.authorizer_for.return_value = Mock(return_value=decision)
    bound = factory(context, provider)("employee", identity, DEPARTMENT)
    if decision is True:
        assert bound.load_authorized("employee", item.request_id) == item
    else:
        with pytest.raises(UsageError) as error:
            bound.load_authorized("employee", item.request_id)
        assert error.value.status == 403
    provider.authorizer_for.assert_called_once_with("employee", identity, (DEPARTMENT,))


@pytest.mark.parametrize("failure", ["missing", "factory", "callback", "timeout", "invalid_callback", "provider_403", "provider_503"])
def test_provider_failures_are_sanitized_even_with_sensitive_exception_text(context, failure):
    identity = create(context[0]); item = saved(context, identity)
    provider = Mock(); provider.authorizer_for.return_value = Mock(return_value=True)
    secret = "synthetic-private-error-do-not-deliver"
    if failure == "missing": provider = None
    if failure == "factory": provider.authorizer_for.side_effect = RuntimeError(secret)
    if failure in ("callback", "timeout"):
        provider.authorizer_for.return_value.side_effect = (TimeoutError(secret) if failure == "timeout" else RuntimeError(secret))
    if failure == "invalid_callback": provider.authorizer_for.return_value = None
    if failure.startswith("provider_"):
        provider.authorizer_for.return_value.side_effect = UsageError(int(failure[-3:]), secret)
    bound = factory(context, provider)("employee", identity, DEPARTMENT)
    with pytest.raises(UsageError) as error:
        bound.load_authorized("employee", item.request_id)
    assert error.value.status == (403 if failure == "provider_403" else 503)
    assert secret not in str(error.value)


def test_binding_denies_other_owner_admin_forged_scope_and_cross_conversation(context):
    identity = create(context[0]); other = create(context[0]); item = saved(context, other)
    provider = Mock(); provider.authorizer_for.return_value = Mock(return_value=True)
    factories = factory(context, provider)
    for user in ("admin", "outsider"):
        with pytest.raises(UsageError) as error: factories(user, identity, DEPARTMENT)
        assert error.value.status == 404
    with pytest.raises(UsageError, match="scope_forbidden"):
        factories("employee", identity, "hr")
    with pytest.raises(UsageError) as error:
        factories("employee", identity, DEPARTMENT).load_authorized("employee", item.request_id)
    assert error.value.status == 403
    provider.authorizer_for.assert_not_called()


@pytest.mark.parametrize("change", ["inactive_user", "membership", "role", "failed_department", "archived_department"])
def test_current_access_is_rechecked_on_every_delivery(context, change):
    identity = create(context[0]); item = saved(context, identity)
    provider = Mock(); provider.authorizer_for.return_value = Mock(return_value=True)
    bound = factory(context, provider)("employee", identity, DEPARTMENT)
    assert bound.load_authorized("employee", item.request_id) == item
    with Session(context[1]) as s:
        if change == "inactive_user": s.get(User, "employee").status = "inactive"
        if change == "membership": s.query(DepartmentMembership).filter_by(user_id="employee", department_id=DEPARTMENT).update({"status": "inactive"})
        if change == "role": s.query(RoleAssignment).filter_by(user_id="employee", department_id=DEPARTMENT).delete()
        if change == "failed_department": s.get(Department, DEPARTMENT).status = "failed"
        if change == "archived_department": s.get(Department, DEPARTMENT).archived_at = datetime.now(timezone.utc)
        s.commit()
    with pytest.raises(UsageError) as error: bound.load_authorized("employee", item.request_id)
    assert error.value.status == 403
    assert provider.authorizer_for.call_count == 1


def test_no_answer_still_needs_provider_and_out_of_scope_source_never_reaches_it(context):
    identity = create(context[0]); no_answer = saved(context, identity, no_answer=True)
    provider = Mock(); provider.authorizer_for.return_value = Mock(return_value=True)
    bound = factory(context, provider)("employee", identity, DEPARTMENT)
    assert bound.load_authorized("employee", no_answer.request_id) == no_answer
    provider.authorizer_for.assert_called_once()
    bad = saved(context, identity, other_source=True)
    with pytest.raises(UsageError) as error: bound.load_authorized("employee", bad.request_id)
    assert error.value.status == 403 and provider.authorizer_for.call_count == 1


def test_provider_copy_cannot_mutate_delivered_payload(context):
    identity = create(context[0]); item = saved(context, identity)
    provider = Mock()
    def mutate(_, payload):
        payload["answer"] = "mutated"; payload["sources"].clear()
        return True
    provider.authorizer_for.return_value = mutate
    bound = factory(context, provider)("employee", identity, DEPARTMENT)
    assert bound.load_authorized("employee", item.request_id) == item


def test_revoke_during_provider_io_denies_delivery(context):
    identity = create(context[0]); item = saved(context, identity)
    provider = Mock()
    def revoke(*_):
        with Session(context[1]) as s:
            s.query(DepartmentMembership).filter_by(user_id="employee", department_id=DEPARTMENT).update({"status": "inactive"})
            s.commit()
        return True
    provider.authorizer_for.return_value = revoke
    bound = factory(context, provider)("employee", identity, DEPARTMENT)
    with pytest.raises(UsageError) as error: bound.load_authorized("employee", item.request_id)
    assert error.value.status == 403


def test_admin_own_scope_preserves_global_rbac_but_never_bypasses_source_provider(context):
    _, central, _, _, runtime = context
    service = ConversationService(store(runtime, user="admin"), central)
    identity = create(service)
    item = record(user="admin"); item.payload["conversation_id"] = identity
    service.store.save_once(item)
    provider = Mock(); provider.authorizer_for.return_value = Mock(return_value=False)
    bound = factory(context, provider)("admin", identity, DEPARTMENT)
    with pytest.raises(UsageError) as error: bound.load_authorized("admin", item.request_id)
    assert error.value.status == 403
    provider.authorizer_for.assert_called_once_with("admin", identity, (DEPARTMENT,))


def test_missing_conversation_and_unanswered_scope_do_not_reach_provider(context):
    identity = create(context[0]); item = saved(context, identity, no_answer=True)
    provider = Mock(); provider.authorizer_for.return_value = Mock(return_value=True)
    factories = factory(context, provider)
    with pytest.raises(UsageError) as error: factories("employee", str(uuid4()), DEPARTMENT)
    assert error.value.status == 404
    bad = record(outcome="no_answer")
    bad.payload.update(conversation_id=identity, unanswered_departments=[str(uuid4())])
    context[0].store.save_once(bad)
    with pytest.raises(UsageError) as error:
        factories("employee", identity, DEPARTMENT).load_authorized("employee", bad.request_id)
    assert error.value.status == 403
    provider.authorizer_for.assert_not_called()


def test_http_history_masks_only_failed_result_and_ignores_forged_context(context):
    service, central, client, headers, _ = context
    identity = create(service)
    prepared = [result(service, central, identity) for _ in range(2)]
    for request_id, command in prepared: service.append_completed(identity, request_id, command)
    provider = Mock()
    def approve(_, payload):
        if payload["question_request_id"] == prepared[0][0]:
            raise TimeoutError("synthetic-hidden-provider-detail")
        return True
    provider.authorizer_for.return_value = approve
    client.app.state.source_authorizer_provider = provider
    response = client.get(f"/api/conversations/{identity}/messages?department_id=forged&user_id=admin",
                          headers={**headers["employee"], "X-User-ID": "admin"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    first, second = response.json()["items"]
    assert first["content_status"] == "verification_unavailable"
    assert first["question"] is first["response"] is None
    assert second["content_status"] == "available"
    assert "synthetic-hidden-provider-detail" not in response.text
    client.app.state.source_authorizer_provider = None
    missing = client.get(f"/api/conversations/{identity}/messages", headers=headers["employee"])
    assert all(i["content_status"] == "verification_unavailable" and i["question"] is i["response"] is None
               for i in missing.json()["items"])
