"""Core HTTP boundary checks with synthetic SQLite auth, no store configured."""
from uuid import uuid4
import pytest

from app.conversation_store.routes import ConversationCreate
from pydantic import ValidationError
from test_authentication import auth, login_headers


def test_unconfigured_store_fails_closed_and_cache_is_disabled(auth):
    client, _, password = auth
    headers = login_headers(client, password)
    for method, path, body in [("GET", "/api/conversations", None),
        ("POST", "/api/conversations", {"request_key":"new", "mode":"department", "department_ids":["hr"]}),
        ("GET", f"/api/conversations/{uuid4()}", None),
        ("GET", f"/api/conversations/{uuid4()}/messages", None)]:
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("change", [{"owner_id":"admin"}, {"department_ids":[]},
    {"department_ids":["hr","hr"]}, {"mode":"department","department_ids":["hr","finance"]},
    {"request_key":" "}, {"question":"not a question submission endpoint"}])
def test_conversation_create_rejects_identity_and_invalid_inputs(change):
    data=dict(request_key="safe", mode="department", department_ids=["hr"])
    data.update(change)
    with pytest.raises(ValidationError): ConversationCreate.model_validate(data)


def test_no_client_message_edit_delete_or_submission_route(auth):
    client, _, password = auth
    headers = login_headers(client, password)
    for method in ("POST", "PATCH", "DELETE"):
        response=client.request(method, f"/api/conversations/{uuid4()}/messages", headers=headers, json={})
        assert response.status_code == 405
        assert response.headers["cache-control"] == "no-store"


def test_misbound_factory_is_rejected(auth):
    from types import SimpleNamespace
    client, _, password=auth
    client.app.state.conversation_store_factory=lambda _: SimpleNamespace(user_id="outsider")
    response=client.get("/api/conversations",headers=login_headers(client,password))
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
