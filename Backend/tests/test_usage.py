from datetime import datetime, timedelta, timezone
from hashlib import sha256
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.config import AuthSettings
from app.main import create_app
from app.usage import service as u
from app.usage.models import ProviderAttempt, UsageEntry, UsagePolicy, UsagePolicyAudit, UsageRequest
from app.usage.processor import ConfirmedTechnicalFailure, ProviderReceipt, run_once, transaction
from test_authentication import login_headers, seed_auth_users
from test_users import seed_user_admin

NOW = datetime(2026, 1, 15, 9, tzinfo=timezone.utc)
POLICY = dict(enabled=True, timezone="Asia/Riyadh", monthly_questions=20, daily_questions=10,
              rate_count=10, rate_seconds=60, monthly_budget_micro_usd=1000)


@pytest.fixture
def usage():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)
    password = seed_auth_users(engine)
    seed_user_admin(engine)
    app = create_app(central_engine=engine, auth_settings=AuthSettings(mode="development_password", app_env="test"))
    with TestClient(app) as client:
        headers = {who: login_headers(client, password, who) for who in ("admin", "employee", "outsider")}
        yield engine, client, headers
    engine.dispose()


def configure(engine, **changes):
    return transaction(engine, lambda s: u.configure(s, "admin", {**POLICY, **changes}, now=NOW))


def reserve(engine, key="one", cost=100, user="employee", now=NOW):
    return transaction(engine, lambda s: u.reserve(s, user, key, sha256(key.encode()).hexdigest(), cost, now=now))


def begin(engine, identity, now=NOW):
    return transaction(engine, lambda s: u.start(s, identity, now=now))


def summary(engine, user="employee", now=NOW):
    return transaction(engine, lambda s: u.personal_summary(s, user, now=now))


def company(engine, now=NOW):
    return transaction(engine, lambda s: u.company_summary(s, "admin", now=now))


def test_unconfigured_fail_closed_and_safe_own_admin_endpoints(usage):
    engine, client, headers = usage
    assert client.get("/me/usage").status_code == 401
    assert client.get("/me/usage", headers={"X-User-ID": "admin"}).status_code == 401
    assert client.get("/me/usage", headers=headers["employee"]).json()["configured"] is False
    with pytest.raises(u.UsageError, match="not_configured"):
        reserve(engine)
    change = {**POLICY, "expected_version": 0}
    assert client.put("/admin/usage/policy", headers=headers["employee"], json=change).status_code == 403
    assert client.put("/admin/usage/policy", headers=headers["admin"], json=change).status_code == 200
    assert client.put("/admin/usage/policy", headers=headers["admin"], json=change).status_code == 409
    assert client.get("/admin/usage/policy", headers=headers["admin"]).json()["version"] == 1
    reserve(engine)
    employee = client.get("/me/usage/ledger?user_id=outsider", headers=headers["employee"])
    assert employee.status_code == 200 and employee.headers["cache-control"] == "no-store"
    assert {entry["user_id"] for entry in employee.json()} == {"employee"}
    assert client.get("/me/usage/ledger", headers=headers["outsider"]).json() == []
    assert client.get("/admin/usage/summary", headers=headers["employee"]).status_code == 403
    assert client.get("/admin/usage/users/employee", headers=headers["outsider"]).status_code == 403
    request = client.get("/me/usage/requests", headers=headers["employee"]).json()[0]
    assert "claim_id" not in request and "fingerprint" not in request
    assert "budget_micro_usd" not in client.get("/me/usage", headers=headers["employee"]).json()
    assert client.post("/me/usage/reserve", headers=headers["employee"], json={"cost": 0}).status_code == 404
    assert client.get("/me/usage/ledger?limit=101", headers=headers["employee"]).status_code == 422


def test_policy_change_audited_immediate_without_reset_and_timezone_frozen(usage):
    engine, client, headers = usage
    configure(engine)
    configure(engine)  # No-op does not create an audit version.
    reserve(engine)
    configure(engine, monthly_questions=1)
    assert summary(engine)["monthly_remaining"] == 0
    with pytest.raises(u.UsageError, match="monthly_question_limit"):
        reserve(engine, key="second")
    with pytest.raises(u.UsageError, match="timezone_locked"):
        configure(engine, timezone="UTC")
    with Session(engine) as s:
        assert s.get(UsagePolicy, 1).version == 2
        assert s.scalar(select(func.count()).select_from(UsagePolicyAudit)) == 2
    invalid = [{**POLICY, "rate_count": True}, {**POLICY, "rate_seconds": 0},
               {**POLICY, "monthly_budget_micro_usd": -1}, {**POLICY, "timezone": "invalid/zone"},
               {**POLICY, "actor_user_id": "employee"}]
    for payload in invalid:
        assert client.put("/admin/usage/policy", headers=headers["admin"], json=payload).status_code == 422


def test_request_and_attempt_idempotency_and_no_answer_counts_once(usage):
    engine, _, _ = usage
    configure(engine)
    first = reserve(engine)
    assert first.created and not reserve(engine).created
    with pytest.raises(u.UsageError, match="payload_conflict"):
        reserve(engine, cost=101)
    claim = begin(engine, first.request.id)
    with pytest.raises(u.UsageError, match="already_started"):
        begin(engine, first.request.id)
    authorize = lambda s: u.authorize_attempt(s, first.request.id, claim, "route", "fake", "model", 40, now=NOW)
    assert transaction(engine, authorize).created
    assert not transaction(engine, authorize).created
    settle = lambda s: u.settle_attempt(s, first.request.id, claim, "route", "receipt-one", 20, now=NOW)
    transaction(engine, settle)
    transaction(engine, settle)
    transaction(engine, lambda s: u.finish(s, first.request.id, claim, "no_answer", now=NOW))
    transaction(engine, lambda s: u.finish(s, first.request.id, claim, "no_answer", now=NOW))
    assert summary(engine)["monthly_used_or_held"] == 1
    assert company(engine)["spent_micro_usd"] == 20 and company(engine)["held_micro_usd"] == 0
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(ProviderAttempt)) == 1
        assert s.scalar(select(func.sum(UsageEntry.question_delta))) == -1


def test_technical_refund_retains_paid_cost_and_rate_consumption(usage):
    engine, _, _ = usage
    configure(engine, rate_count=1)
    request = reserve(engine).request
    claim = begin(engine, request.id)
    transaction(engine, lambda s: u.authorize_attempt(s, request.id, claim, "generation", "fake", "model", 100, now=NOW))
    transaction(engine, lambda s: u.settle_attempt(s, request.id, claim, "generation", "paid-before-error", 45, now=NOW))
    for _ in range(2):
        transaction(engine, lambda s: u.finish(s, request.id, claim, "technical_error", now=NOW))
    assert summary(engine)["monthly_used_or_held"] == 0
    assert company(engine)["spent_micro_usd"] == 45
    with pytest.raises(u.UsageError, match="question_rate_limit"):
        reserve(engine, key="rate-abuse")
    assert reserve(engine, key="after-window", now=NOW + timedelta(seconds=60)).created


def test_unfinished_attempt_never_releases_money_or_credit(usage):
    engine, _, _ = usage
    configure(engine)
    request = reserve(engine).request
    claim = begin(engine, request.id)
    transaction(engine, lambda s: u.authorize_attempt(s, request.id, claim, "pending", "fake", "model", 100, now=NOW))
    with pytest.raises(u.UsageError, match="unsettled"):
        transaction(engine, lambda s: u.finish(s, request.id, claim, "technical_error", now=NOW))
    transaction(engine, lambda s: u.mark_uncertain(s, request.id, claim, now=NOW))
    assert company(engine)["held_micro_usd"] == 100
    with pytest.raises(u.UsageError, match="authoritative"):
        transaction(engine, lambda s: u.finish(s, request.id, claim, "technical_error", now=NOW))
    with pytest.raises(u.UsageError, match="unsettled"):
        transaction(engine, lambda s: u.finish(s, request.id, claim, "technical_error", verification_ref="checked", now=NOW))
    transaction(engine, lambda s: u.settle_attempt(s, request.id, claim, "pending", "verified-zero", 0, now=NOW))
    transaction(engine, lambda s: u.finish(s, request.id, claim, "technical_error", verification_ref="terminal-provider-result", now=NOW))
    assert company(engine)["held_micro_usd"] == 0 and summary(engine)["monthly_used_or_held"] == 0


def test_daily_monthly_boundary_and_old_holds_carry_but_question_credit_does_not(usage):
    engine, _, _ = usage
    configure(engine, monthly_questions=1, daily_questions=1)
    # Riyadh: one minute before February starts, then one minute after.
    old = datetime(2026, 1, 31, 20, 59, tzinfo=timezone.utc)
    new = old + timedelta(minutes=2)
    request = reserve(engine, now=old, cost=900).request
    assert summary(engine, now=new)["monthly_remaining"] == 1
    assert company(engine, now=new)["held_micro_usd"] == 900
    with pytest.raises(u.UsageError, match="company_budget_limit"):
        reserve(engine, key="new-month-too-large", cost=101, now=new)
    claim = begin(engine, request.id, now=new)
    transaction(engine, lambda s: u.authorize_attempt(s, request.id, claim, "late", "fake", "model", 900, now=new))
    transaction(engine, lambda s: u.settle_attempt(s, request.id, claim, "late", "late-receipt", 50, now=new))
    transaction(engine, lambda s: u.finish(s, request.id, claim, "technical_error", now=new))
    assert company(engine, now=new)["spent_micro_usd"] == 50
    assert summary(engine, now=new)["monthly_remaining"] == 1
    assert summary(engine, now=old)["monthly_used_or_held"] == 0


@pytest.mark.parametrize("changes,reason", [({"monthly_questions": 0}, "monthly_question_limit"),
    ({"daily_questions": 0}, "daily_question_limit"), ({"rate_count": 0}, "question_rate_limit"),
    ({"monthly_budget_micro_usd": 0}, "company_budget_limit"), ({"enabled": False}, "disabled")])
def test_independent_limits_deny_admission_without_side_effects(usage, changes, reason):
    engine, _, _ = usage
    configure(engine, **changes)
    with pytest.raises(u.UsageError, match=reason):
        reserve(engine)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(UsageRequest)) == 0
        assert s.scalar(select(func.count()).select_from(UsageEntry)) == 0


def test_cost_overrun_retains_other_inflight_caps_and_truthful_spend(usage):
    engine, _, _ = usage
    configure(engine, monthly_budget_micro_usd=100)
    request = reserve(engine, cost=100).request
    claim = begin(engine, request.id)
    for key in ("first", "second"):
        transaction(engine, lambda s: u.authorize_attempt(s, request.id, claim, key, "fake", "model", 50, now=NOW))
    transaction(engine, lambda s: u.settle_attempt(s, request.id, claim, "first", "overrun", 80, now=NOW))
    result = company(engine)
    assert result["spent_micro_usd"] == 80 and result["held_micro_usd"] == 50
    assert result["over_budget_micro_usd"] == 30
    with pytest.raises(u.UsageError, match="company_budget_limit"):
        reserve(engine, key="blocked", cost=1, user="outsider")
    transaction(engine, lambda s: u.settle_attempt(s, request.id, claim, "second", "other-terminal", 10, now=NOW))
    transaction(engine, lambda s: u.finish(s, request.id, claim, "answered", verification_ref="all-attempts-confirmed", now=NOW))
    assert company(engine)["spent_micro_usd"] == 90 and company(engine)["held_micro_usd"] == 0


def test_cancel_only_before_start_and_refund_once(usage):
    engine, _, _ = usage
    configure(engine)
    reserve(engine)
    for _ in range(2):
        transaction(engine, lambda s: u.cancel_before_start(s, "employee", "one", now=NOW))
    assert summary(engine)["monthly_used_or_held"] == 0
    running = reserve(engine, key="running").request
    begin(engine, running.id)
    with pytest.raises(u.UsageError, match="cannot_cancel"):
        transaction(engine, lambda s: u.cancel_before_start(s, "employee", "running", now=NOW))
    assert company(engine)["held_micro_usd"] == 100


def test_runner_multiple_department_attempts_counts_one_and_never_replays(usage):
    engine, _, _ = usage
    configure(engine)
    calls = []
    def processor(context):
        for department in ("hr", "finance", "summary"):
            context.perform(department, "fake", "model", 30,
                lambda key: (calls.append(key) or ProviderReceipt(key, 10)))
        return "answered"
    first = run_once(engine, "employee", "runner", "a" * 64, 100, processor)
    second = run_once(engine, "employee", "runner", "a" * 64, 100, processor)
    assert first.state == "completed" and second.replayed and len(calls) == 3
    with Session(engine) as s:
        assert s.scalar(select(func.sum(UsageEntry.question_delta))) == -1
        assert s.scalar(select(func.sum(UsageEntry.cost_micro_usd))) == 30


def test_runner_confirmed_failure_refunds_but_timeout_remains_uncertain(usage):
    engine, _, _ = usage
    configure(engine)
    def confirmed(context):
        context.perform("paid", "fake", "model", 100, lambda key: ProviderReceipt(key, 20))
        raise ConfirmedTechnicalFailure()
    failed = run_once(engine, "employee", "confirmed", "b" * 64, 100, confirmed)
    assert failed.state == "refunded"
    def unknown(context):
        context.authorize("timeout", "fake", "model", 100)
        raise TimeoutError("CANARY_PRIVATE_PROVIDER_ERROR")
    uncertain = run_once(engine, "employee", "timeout", "c" * 64, 100, unknown)
    assert uncertain.state == "uncertain"
    with Session(engine) as s:
        assert u.total_held(s) == 100
        assert s.scalar(select(func.sum(UsageEntry.cost_micro_usd))) == 20


def test_ledger_failure_rolls_back_admission(usage):
    engine, _, _ = usage
    configure(engine)
    with patch.object(u, "emit", side_effect=u.UsageError(503, "synthetic_ledger_failure")):
        with pytest.raises(u.UsageError):
            reserve(engine)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(UsageRequest)) == 0
        assert u.total_held(s) == 0


def test_wrong_claim_and_duplicate_receipt_cannot_mutate_another_request(usage):
    engine, _, _ = usage
    configure(engine)
    first = reserve(engine, key="first").request
    claim = begin(engine, first.id)
    with pytest.raises(u.UsageError, match="invalid_execution_claim"):
        transaction(engine, lambda s: u.finish(s, first.id, "wrong", "answered", now=NOW))
    for key, user_id in (("first", "employee"), ("second", "outsider")):
        if key == "second":
            request = reserve(engine, key=key, user=user_id).request
            current_claim = begin(engine, request.id)
        else:
            request, current_claim = first, claim
        transaction(engine, lambda s: u.authorize_attempt(s, request.id, current_claim, "attempt", "fake", "model", 100, now=NOW))
        if key == "first":
            transaction(engine, lambda s: u.settle_attempt(s, request.id, current_claim, "attempt", "same-provider-receipt", 20, now=NOW))
        else:
            with pytest.raises(u.UsageError, match="already_recorded"):
                transaction(engine, lambda s: u.settle_attempt(s, request.id, current_claim, "attempt", "same-provider-receipt", 20, now=NOW))
    with Session(engine) as s:
        assert u.spent(s, "2026-01") == 20
    with pytest.raises(u.UsageError, match="receipt_payload_conflict"):
        transaction(engine, lambda s: u.settle_attempt(s, first.id, claim, "attempt", "same-provider-receipt", 21, now=NOW))


def test_daily_reset_without_monthly_rollover_and_lowered_budget_keeps_holds(usage):
    engine, _, _ = usage
    configure(engine, monthly_questions=2, daily_questions=1)
    reserve(engine)
    with pytest.raises(u.UsageError, match="daily_question_limit"):
        reserve(engine, key="same-day")
    reserve(engine, key="next-day", now=NOW + timedelta(days=1))
    with pytest.raises(u.UsageError, match="monthly_question_limit"):
        reserve(engine, key="third-day", now=NOW + timedelta(days=2))
    configure(engine, monthly_budget_micro_usd=50)
    assert company(engine)["held_micro_usd"] == 200 and company(engine)["over_budget_micro_usd"] == 150
    assert summary(engine)["monthly_used_or_held"] == 2


@pytest.mark.parametrize("invalid", [-1, True, 1.0])
def test_invalid_money_values_never_create_credit_or_hold(usage, invalid):
    engine, _, _ = usage
    configure(engine)
    with pytest.raises(u.UsageError, match="integer"):
        reserve(engine, cost=invalid)
    assert company(engine)["held_micro_usd"] == 0


def test_inactive_user_cannot_start_more_work_but_bills_are_still_recorded(usage):
    from app.models import User
    engine, client, headers = usage
    configure(engine)
    request = reserve(engine).request
    claim = begin(engine, request.id)
    transaction(engine, lambda s: u.authorize_attempt(s, request.id, claim, "existing", "fake", "model", 100, now=NOW))
    with Session(engine) as s:
        s.get(User, "employee").status = "inactive"
        s.commit()
    assert client.get("/me/usage", headers=headers["employee"]).status_code == 401
    with pytest.raises(u.UsageError, match="user_not_active"):
        reserve(engine, key="another")
    with pytest.raises(u.UsageError, match="user_not_active"):
        transaction(engine, lambda s: u.authorize_attempt(s, request.id, claim, "new", "fake", "model", 0, now=NOW))
    transaction(engine, lambda s: u.settle_attempt(s, request.id, claim, "existing", "late-inactive-bill", 15, now=NOW))
    transaction(engine, lambda s: u.finish(s, request.id, claim, "technical_error", now=NOW))
    assert company(engine)["spent_micro_usd"] == 15 and company(engine)["held_micro_usd"] == 0


def test_disabled_policy_stops_new_admissions_without_erasing_existing_work(usage):
    engine, _, _ = usage
    configure(engine)
    request = reserve(engine).request
    configure(engine, enabled=False)
    with pytest.raises(u.UsageError, match="disabled"):
        reserve(engine, key="new")
    assert company(engine)["held_micro_usd"] == 100
    claim = begin(engine, request.id)
    transaction(engine, lambda s: u.finish(s, request.id, claim, "no_answer", now=NOW))
    assert summary(engine)["monthly_used_or_held"] == 1 and company(engine)["held_micro_usd"] == 0
