from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.main import create_app
from app.usage import service as u
from app.usage.models import ProviderAttempt, UsageEntry, UsagePolicy, UsageRequest
from app.usage.processor import transaction
from test_authentication import login_headers
from test_authentication_postgres import isolated_postgres, pytestmark
from test_users import seed_user_admin
from test_usage import POLICY, NOW


@pytest.fixture(scope="module")
def accounting_pg(isolated_postgres):
    bootstrap, runtime, password = isolated_postgres
    seed_user_admin(bootstrap)
    with bootstrap.begin() as c:
        c.execute(text("GRANT SELECT,INSERT ON usage_policy,usage_requests,usage_provider_attempts,usage_entries,usage_policy_audit TO auth_test_api"))
        c.execute(text("GRANT UPDATE(enabled,timezone,monthly_questions,daily_questions,rate_count,rate_seconds,monthly_budget_micro_usd,version) ON usage_policy TO auth_test_api"))
        c.execute(text("GRANT UPDATE(state,outcome,held_micro_usd,claim_id,started_at,closed_at) ON usage_requests TO auth_test_api"))
        c.execute(text("GRANT UPDATE(state,actual_micro_usd,receipt_key,charged_month,input_tokens,output_tokens,settled_at) ON usage_provider_attempts TO auth_test_api"))
        assert c.scalar(text("SELECT count(*) FROM usage_policy")) == 0
    yield bootstrap, runtime, password


@pytest.fixture
def accounting(accounting_pg):
    bootstrap, runtime, password = accounting_pg
    # Reset only new test accounting rows, never the original development cluster.
    with bootstrap.begin() as c:
        c.execute(text("TRUNCATE usage_policy_audit,usage_entries,usage_provider_attempts,usage_requests,usage_policy"))
    transaction(runtime, lambda s: u.configure(s, "admin", POLICY, now=NOW))
    yield bootstrap, runtime, password


def admit(runtime, key, quote=100, user="employee"):
    return transaction(runtime, lambda s: u.reserve(s, user, key, "a" * 64, quote, now=NOW))


def test_concurrent_duplicate_delivery_counts_and_holds_once(accounting):
    bootstrap, runtime, _ = accounting
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: admit(runtime, "one-key"), range(8)))
    assert sum(result.created for result in results) == 1
    assert len({result.request.id for result in results}) == 1
    with Session(bootstrap) as s:
        assert u.count_questions(s, "employee", month="2026-01") == 1
        assert u.total_held(s) == 100
        assert s.scalar(select(func.count()).select_from(UsageEntry)) == 1


def test_concurrent_daily_credit_and_company_budget_limits(accounting):
    _, runtime, _ = accounting
    transaction(runtime, lambda s: u.configure(s, "admin", {**POLICY, "daily_questions": 2}, now=NOW))
    def credit(index):
        try:
            admit(runtime, f"credit-{index}", quote=1)
            return "accepted"
        except u.UsageError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(credit, range(8)))
    assert values.count("accepted") == 2 and values.count("daily_question_limit") == 6
    # Separate users compete for the same company cap, not separate tenant budgets.
    transaction(runtime, lambda s: u.configure(s, "admin", {**POLICY, "monthly_budget_micro_usd": 202}, now=NOW))
    def money(index):
        try:
            admit(runtime, f"budget-{index}", quote=100, user="employee" if index % 2 else "outsider")
            return "accepted"
        except u.UsageError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(money, range(6)))
    assert values.count("accepted") == 2 and values.count("company_budget_limit") == 4
    assert transaction(runtime, lambda s: u.total_held(s)) == 202


def test_start_cancel_race_never_releases_a_started_request(accounting):
    _, runtime, _ = accounting
    request = admit(runtime, "race").request
    def start():
        try:
            transaction(runtime, lambda s: u.start(s, request.id, now=NOW))
            return "started"
        except u.UsageError:
            return "not-started"
    def cancel():
        try:
            transaction(runtime, lambda s: u.cancel_before_start(s, "employee", "race", now=NOW))
            return "cancelled"
        except u.UsageError:
            return "not-cancelled"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(start), pool.submit(cancel)
        result = first.result(), second.result()
    assert result in {("started", "not-cancelled"), ("not-started", "cancelled")}
    with Session(runtime) as s:
        row = s.get(UsageRequest, request.id)
        assert (row.state, u.total_held(s)) in {("running", 100), ("refunded", 0)}


def test_attempt_caps_concurrent_settlement_and_refund_are_idempotent(accounting):
    _, runtime, _ = accounting
    request = admit(runtime, "attempts").request
    claim = transaction(runtime, lambda s: u.start(s, request.id, now=NOW))
    def authorize(index):
        try:
            transaction(runtime, lambda s: u.authorize_attempt(s, request.id, claim, f"attempt-{index}", "fake", "model", 60, now=NOW))
            return index
        except u.UsageError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(authorize, range(4)))
    accepted = [value for value in results if value is not None]
    assert len(accepted) == 1
    key = f"attempt-{accepted[0]}"
    def settle(_):
        transaction(runtime, lambda s: u.settle_attempt(s, request.id, claim, key, "provider-unique", 35, now=NOW))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(settle, range(4)))
    def refund(_):
        transaction(runtime, lambda s: u.finish(s, request.id, claim, "technical_error", now=NOW))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(refund, range(4)))
    with Session(runtime) as s:
        assert u.spent(s, "2026-01") == 35 and u.total_held(s) == 0
        assert u.count_questions(s, "employee", month="2026-01") == 0
        assert s.scalar(select(func.count()).select_from(UsageEntry).where(UsageEntry.event == "question_refunded")) == 1


def test_ledger_failure_rolls_back_charge_and_preserves_held_money(accounting):
    bootstrap, runtime, _ = accounting
    request = admit(runtime, "atomic").request
    claim = transaction(runtime, lambda s: u.start(s, request.id, now=NOW))
    transaction(runtime, lambda s: u.authorize_attempt(s, request.id, claim, "bill", "fake", "model", 100, now=NOW))
    with bootstrap.begin() as c:
        c.execute(text("REVOKE INSERT ON usage_entries FROM auth_test_api"))
    try:
        with pytest.raises(DBAPIError):
            transaction(runtime, lambda s: u.settle_attempt(s, request.id, claim, "bill", "atomic-receipt", 45, now=NOW))
    finally:
        with bootstrap.begin() as c:
            c.execute(text("GRANT INSERT ON usage_entries TO auth_test_api"))
    with Session(runtime) as s:
        assert u.spent(s, "2026-01") == 0 and u.total_held(s) == 100
        assert s.scalar(select(ProviderAttempt)).state == "authorized"
    transaction(runtime, lambda s: u.settle_attempt(s, request.id, claim, "bill", "atomic-receipt", 45, now=NOW))
    assert transaction(runtime, lambda s: u.spent(s, "2026-01")) == 45


def test_uncertain_holds_survive_month_rollover_and_cannot_be_swept(accounting):
    _, runtime, _ = accounting
    request = admit(runtime, "unknown", quote=900).request
    claim = transaction(runtime, lambda s: u.start(s, request.id, now=NOW))
    transaction(runtime, lambda s: u.authorize_attempt(s, request.id, claim, "pending", "fake", "model", 900, now=NOW))
    transaction(runtime, lambda s: u.mark_uncertain(s, request.id, claim, now=NOW))
    later = NOW + timedelta(days=40)
    assert transaction(runtime, lambda s: u.company_summary(s, "admin", now=later))["held_micro_usd"] == 900
    with pytest.raises(u.UsageError, match="unsettled"):
        transaction(runtime, lambda s: u.finish(s, request.id, claim, "technical_error", verification_ref="reviewed", now=later))
    with pytest.raises(u.UsageError, match="company_budget_limit"):
        transaction(runtime, lambda s: u.reserve(s, "outsider", "late", "b" * 64, 101, now=later))


def test_api_privacy_and_immutable_accounting_permissions(accounting):
    _, runtime, password = accounting
    app = create_app(central_engine=runtime, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    admit(runtime, "private")
    with TestClient(app) as client:
        employee = login_headers(client, password)
        outsider = login_headers(client, password, "outsider")
        administrator = login_headers(client, password, "admin")
        assert client.get("/me/usage/requests", headers=outsider).json() == []
        assert len(client.get("/me/usage/requests?user_id=outsider", headers=employee).json()) == 1
        assert client.put("/admin/usage/policy", headers=employee, json={**POLICY, "expected_version": 1}).status_code == 403
        assert client.get("/admin/usage/summary", headers=administrator).status_code == 200
    for statement in ("DELETE FROM usage_requests", "UPDATE usage_entries SET cost_micro_usd=0",
                      "DELETE FROM usage_entries", "DELETE FROM usage_policy_audit",
                      "UPDATE usage_policy_audit SET after='{}'", "UPDATE usage_requests SET user_id='admin'"):
        with runtime.connect() as c, pytest.raises(DBAPIError) as error:
            c.execute(text(statement))
        assert error.value.orig.sqlstate == "42501"


def test_concurrent_refund_loop_cannot_bypass_the_rate_window(accounting):
    _, runtime, _ = accounting
    transaction(runtime, lambda s: u.configure(s, "admin", {**POLICY, "rate_count": 2}, now=NOW))
    def attempt(index):
        try:
            request = admit(runtime, f"rate-{index}", quote=1).request
            claim = transaction(runtime, lambda s: u.start(s, request.id, now=NOW))
            transaction(runtime, lambda s: u.finish(s, request.id, claim, "technical_error", now=NOW))
            return "accepted"
        except u.UsageError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    assert results.count("accepted") == 2 and results.count("question_rate_limit") == 4
    with Session(runtime) as s:
        assert u.count_questions(s, "employee", month="2026-01") == 0
        assert u.total_held(s) == 0


def test_stale_admin_policy_write_cannot_overwrite_a_new_limit(accounting):
    _, runtime, password = accounting
    app = create_app(central_engine=runtime, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        administrator = login_headers(client, password, "admin")
    def change(value):
        with TestClient(app) as client:
            return client.put("/admin/usage/policy", headers=administrator,
                json={**POLICY, "daily_questions": value, "expected_version": 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [2, 3]))
    assert sorted(results) == [200, 409]
    with Session(runtime) as s:
        assert s.get(UsagePolicy, 1).version == 2
