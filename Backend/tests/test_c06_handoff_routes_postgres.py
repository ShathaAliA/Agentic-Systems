"""H3: /api/handoffs end to end on the real C06 store with the real app and a synthetic Central.

The store is module-scoped and immutable and the requester is always `employee`, so every test closes what it opens."""
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.handoffs import access
from app.models import Department, DepartmentMembership, Permission, Role, RolePermission, RoleAssignment
from test_c06_handoff_service_postgres import make_conversation
from test_c06_result_store_postgres import engines, store, pytestmark  # noqa: F401
from test_usage import usage  # noqa: F401


@pytest.fixture
def world(engines, usage):
    _, runtime = engines
    central, client, headers = usage
    dept = "d" + uuid4().hex[:8]
    with Session(central) as s:
        s.add_all([Department(id=dept, name="Handoff " + dept), Permission(id="hq", code=access.REQUEST),
                   Permission(id="hh", code=access.HANDLE), Role(id="handoff-handler", name="Handoff Handler", scope="department")])
        s.flush()
        s.add_all([DepartmentMembership(user_id="employee", department_id=dept),
                   DepartmentMembership(user_id="outsider", department_id=dept),
                   RolePermission(role_id="reader", permission_id="hq"), RolePermission(role_id="handoff-handler", permission_id="hh"),
                   RoleAssignment(user_id="employee", role_id="reader", department_id=dept),
                   RoleAssignment(user_id="outsider", role_id="handoff-handler", department_id=dept)])
        s.commit()
    client.app.state.conversation_store_factory = lambda user: store(runtime, user=user)
    yield runtime, client, headers, dept


def body(runtime, dept, **changes):
    conversation = make_conversation(runtime, "employee", dept, ("no_answer",))
    return {"request_key": "k-" + uuid4().hex, "conversation_id": conversation, "turn_sequence": 1,
            "department_id": dept, "summary": "ملخص الاستفسار", **changes}


def test_full_conversation_over_http(world):
    runtime, client, h, dept = world
    created = client.post("/api/handoffs", json=body(runtime, dept), headers=h["employee"])
    assert created.status_code == 201 and created.json()["status"] == "new"
    assert "no-store" in created.headers["cache-control"]
    identity = created.json()["handoff_id"]
    assert [i["handoff_id"] for i in client.get("/api/handoffs", headers=h["employee"]).json()["items"]] == [identity]
    inbox = client.get("/api/handoffs/inbox?status=new", headers=h["outsider"]).json()
    assert [i["handoff_id"] for i in inbox["items"]] == [identity] and "conversation_id" not in inbox["items"][0]
    assert client.get("/api/handoffs/inbox", headers=h["employee"]).json()["items"] == []
    assert client.get(f"/api/handoffs/{identity}", headers=h["admin"]).status_code == 404  # audit read arrives in H5
    assert client.post(f"/api/handoffs/{identity}/claim", headers=h["employee"]).status_code == 403
    assert client.post(f"/api/handoffs/{identity}/messages", json={"body": "هل من جديد؟"}, headers=h["employee"]).status_code == 201
    reply = client.post(f"/api/handoffs/{identity}/messages", json={"body": "نعم، تفضل"}, headers=h["outsider"])
    assert reply.status_code == 201 and reply.json()["sequence"] == 2
    detail = client.get(f"/api/handoffs/{identity}", headers=h["employee"]).json()
    assert (detail["status"], detail["unread"], detail["assignee_id"]) == ("claimed", 1, "outsider")
    thread = client.get(f"/api/handoffs/{identity}/messages", headers=h["employee"]).json()
    assert [m["author_role"] for m in thread["items"]] == ["requester", "staff"]
    assert client.post(f"/api/handoffs/{identity}/read", json={}, headers=h["employee"]).json()["up_to_sequence"] == 2
    assert client.get(f"/api/handoffs/{identity}", headers=h["employee"]).json()["unread"] == 0
    assert client.post(f"/api/handoffs/{identity}/read", json={"up_to_sequence": 9}, headers=h["employee"]).status_code == 422
    assert client.post(f"/api/handoffs/{identity}/transfer", json={"to_user_id": "employee"}, headers=h["outsider"]).status_code == 422
    assert client.post(f"/api/handoffs/{identity}/close", headers=h["outsider"]).json()["status"] == "closed"
    assert client.post(f"/api/handoffs/{identity}/messages", json={"body": "متأخر"}, headers=h["employee"]).json()["detail"] == "handoff_closed"
    assert client.post(f"/api/handoffs/{identity}/reopen", headers=h["outsider"]).json()["status"] == "new"
    assert client.post(f"/api/handoffs/{identity}/claim", headers=h["outsider"]).json()["status"] == "claimed"
    assert client.post(f"/api/handoffs/{identity}/close", headers=h["outsider"]).json()["status"] == "closed"


def test_validation_and_authentication(world):
    runtime, client, h, dept = world
    good = body(runtime, dept)
    assert client.post("/api/handoffs", json=good).status_code == 401
    for bad in ({**good, "extra": 1}, {**good, "summary": ""}, {**good, "turn_sequence": 0}, {**good, "conversation_id": "x"},
                {**good, "request_key": "bad key"}, {k: v for k, v in good.items() if k != "department_id"}):
        assert client.post("/api/handoffs", json=bad, headers=h["employee"]).status_code == 422
    assert client.get("/api/handoffs?limit=0", headers=h["employee"]).status_code == 422
    assert client.get("/api/handoffs/not-a-uuid", headers=h["employee"]).status_code == 422
    assert client.get("/api/handoffs/inbox?status=open", headers=h["outsider"]).status_code == 422
    assert client.get(f"/api/handoffs/{uuid4()}", headers=h["employee"]).json()["detail"] == "handoff_not_found"
    assert client.post("/api/handoffs", json=body(runtime, "other-dept"), headers=h["employee"]).status_code == 403


def test_admin_sets_the_open_cap(world):
    runtime, client, h, dept = world
    url = "/api/admin/handoffs/policy"
    assert client.get(url, headers=h["admin"]).json() == dict(open_cap=5, version=0, default=True)
    assert client.get(url, headers=h["employee"]).status_code == 403
    assert client.put(url, json={"open_cap": 1, "expected_version": 0}, headers=h["employee"]).status_code == 403
    assert client.put(url, json={"open_cap": 0, "expected_version": 0}, headers=h["admin"]).status_code == 422
    assert client.put(url, json={"open_cap": 51, "expected_version": 0}, headers=h["admin"]).status_code == 422
    assert client.put(url, json={"open_cap": 1, "expected_version": 3}, headers=h["admin"]).status_code == 409
    assert client.put(url, json={"open_cap": 1, "expected_version": 0}, headers=h["admin"]).json() == dict(open_cap=1, version=1, default=False)
    first = client.post("/api/handoffs", json=body(runtime, dept), headers=h["employee"])
    assert first.status_code == 201
    second = client.post("/api/handoffs", json=body(runtime, dept), headers=h["employee"])
    assert (second.status_code, second.json()["detail"]) == (429, "handoff_open_limit")
    identity = first.json()["handoff_id"]
    assert client.post(f"/api/handoffs/{identity}/claim", headers=h["outsider"]).status_code == 200
    assert client.post(f"/api/handoffs/{identity}/close", headers=h["outsider"]).status_code == 200
    again = client.post("/api/handoffs", json=body(runtime, dept), headers=h["employee"])
    assert again.status_code == 201  # a place was freed
    assert client.post(f"/api/handoffs/{again.json()['handoff_id']}/claim", headers=h["outsider"]).status_code == 200
    assert client.post(f"/api/handoffs/{again.json()['handoff_id']}/close", headers=h["outsider"]).status_code == 200
    assert client.put(url, json={"open_cap": 1, "expected_version": 1}, headers=h["admin"]).json()["version"] == 1  # unchanged
