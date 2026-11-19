"""PR 1 — Verify the legacy `get_department_db` is removed.

This ensures no router silently falls back to a central session for
business data, which would break department isolation.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


def test_no_router_imports_legacy_session():
    """No app module should import `get_department_db`."""
    offenders = []
    for py in APP.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"from\s+app\.database\s+import\s+([^\n]+)", text):
            imports = m.group(1)
            if "get_department_db" in imports:
                offenders.append(f"{py.relative_to(BACKEND)}: imports {imports.strip()}")
    assert not offenders, (
        "The following modules still import the removed get_department_db:\n"
        + "\n".join(offenders)
    )


def test_no_functional_get_department_db_definition():
    """`app/database.py` must not define a functional `get_department_db`."""
    db_py = APP / "database.py"
    assert db_py.exists()
    text = db_py.read_text(encoding="utf-8")

    # The only allowed occurrence is inside a comment (DEPRECATED).
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "def get_department_db(" not in line, (
            "app/database.py still defines get_department_db() — remove it."
        )


def test_documents_router_uses_core_session_interface():
    """documents/router.py should use get_central_db + authorized_department_session."""
    router_py = APP / "documents" / "router.py"
    assert router_py.exists()
    text = router_py.read_text(encoding="utf-8")

    assert "get_central_db" in text, (
        "documents/router.py must use get_central_db for authentication/permission checks."
    )
    assert "authorized_department_session" in text, (
        "documents/router.py must use authorized_department_session for business data."
    )
    assert "get_department_db" not in text, (
        "documents/router.py still references get_department_db."
    )