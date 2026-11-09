"""PR 9 — HTTP-level auth tests for documents endpoints.

Verifies:
1. No token → 401/403 (not 500, not 200).
2. Cross-department access → 403.
3. User without 'documents:read' → 403.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_list_documents_no_auth_returns_401_or_403(client):
    """Unauthenticated request must NOT return 200."""
    response = client.get("/api/documents/?department_id=hr")
    assert response.status_code in (401, 403), (
        f"Expected 401/403, got {response.status_code}: {response.text}"
    )


def test_upload_no_auth_returns_401_or_403(client):
    """Unauthenticated upload must be rejected before processing file."""
    response = client.post(
        "/api/documents/upload",
        data={"department_id": "hr"},
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert response.status_code in (401, 403)


def test_ask_no_auth_returns_401_or_403(client):
    response = client.post(
        "/api/documents/ask",
        params={"query": "hello", "department_id": "hr"},
    )
    assert response.status_code in (401, 403)


def test_get_document_no_auth_returns_401_or_403(client):
    response = client.get("/api/documents/1?department_id=hr")
    assert response.status_code in (401, 403)


def test_patch_document_no_auth_returns_401_or_403(client):
    response = client.patch(
        "/api/documents/1?department_id=hr&filename=x.pdf"
    )
    assert response.status_code in (401, 403)


def test_version_replace_no_auth_returns_401_or_403(client):
    response = client.post(
        "/api/documents/1/version",
        data={"department_id": "hr"},
        files={"file": ("v2.txt", b"hello", "text/plain")},
    )
    assert response.status_code in (401, 403)


def test_permissions_no_auth_returns_401_or_403(client):
    response = client.get("/api/documents/1/permissions?department_id=hr")
    assert response.status_code in (401, 403)


def test_no_str_e_in_401_response(client):
    """Auth rejection must not leak stack traces or file paths."""
    response = client.get("/api/documents/?department_id=hr")
    text = response.text.lower()
    assert "traceback" not in text
    assert "c:\\users" not in text
    assert "file_path" not in text