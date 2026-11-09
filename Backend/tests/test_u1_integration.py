"""PR — U1 integration tests for /ask idempotency.

Does NOT use seed admin (may not have role). Inserts usage_policy directly.
"""
import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.usage import service
from app.usage.integration import run_once, ConfirmedTechnicalFailure


pytestmark = pytest.mark.integration

ADMIN_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5435/wadi_db"


@pytest.fixture(scope="module")
def central_db():
    engine = create_engine(ADMIN_URL, hide_parameters=True)
    # Ensure usage_policy exists (id=1, enabled, generous limits)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO usage_policy
                (id, enabled, timezone, monthly_questions, daily_questions,
                 rate_count, rate_seconds, monthly_budget_micro_usd, version)
            VALUES (1, TRUE, 'Asia/Riyadh', 1000, 1000, 100, 60, 1000000000, 1)
            ON CONFLICT (id) DO UPDATE SET
                enabled = TRUE,
                monthly_questions = 1000,
                daily_questions = 1000,
                rate_count = 100,
                monthly_budget_micro_usd = 1000000000
        """))
    yield engine
    engine.dispose()


@pytest.fixture
def user_id(central_db):
    """Use admin@example.com (created by seed) — read-only."""
    with Session(central_db) as db:
        uid = db.execute(text(
            "SELECT id FROM users WHERE email = 'admin@example.com'"
        )).scalar()
        if uid is None:
            pytest.skip("admin@example.com not found — run seed.py")
        return uid


def test_same_request_key_does_not_double_charge(central_db, user_id):
    request_key = f"test-{uuid.uuid4()}"
    calls = {"count": 0}

    def processor(ctx):
        calls["count"] += 1
        return "answered"

    r1 = run_once(central_db, user_id, request_key, "a" * 64, 1000, processor)
    r2 = run_once(central_db, user_id, request_key, "a" * 64, 1000, processor)

    assert calls["count"] == 1, "processor must run only once"
    assert r1.replayed is False
    assert r2.replayed is True
    assert r1.request_id == r2.request_id


def test_same_key_different_fingerprint_rejected(central_db, user_id):
    request_key = f"test-{uuid.uuid4()}"

    run_once(central_db, user_id, request_key, "a" * 64, 1000, lambda ctx: "answered")

    with pytest.raises(service.UsageError) as exc_info:
        run_once(central_db, user_id, request_key, "b" * 64, 1000, lambda ctx: "answered")

    assert exc_info.value.code == "request_key_payload_conflict"


def test_technical_failure_refunds(central_db, user_id):
    request_key = f"test-{uuid.uuid4()}"

    def processor(ctx):
        raise ConfirmedTechnicalFailure()

    r = run_once(central_db, user_id, request_key, "a" * 64, 1000, processor)
    assert r.state == "refunded"
    assert r.outcome == "technical_error"