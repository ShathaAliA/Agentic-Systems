"""Trusted server accounting. Caller owns the transaction; no external work under lock."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import unicodedata
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text
from app.models import User
from app.usage.models import ProviderAttempt, UsageEntry, UsagePolicy, UsagePolicyAudit, UsageRequest
from app.users.service import require_admin

ACTIVE = ("reserved", "running", "uncertain")
POLICY_FIELDS = ("enabled", "timezone", "monthly_questions", "daily_questions", "rate_count", "rate_seconds", "monthly_budget_micro_usd")


class UsageError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def clock(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise UsageError(422, "timezone_aware_clock_required")
    return value.astimezone(timezone.utc)


def lock(session):
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('usage-accounting',0))"))


def integer(value, maximum=10**12, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise UsageError(422, "invalid_accounting_integer")
    return value


def reference(value, maximum=80):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", value) or len(value) > maximum:
        raise UsageError(422, "invalid_accounting_reference")
    return value


def label(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(unicodedata.category(c).startswith("C") for c in value):
        raise UsageError(422, "invalid_provider_label")
    return value


def periods(now, zone):
    local = now.astimezone(ZoneInfo(zone))
    return local.strftime("%Y-%m"), local.date().isoformat()


def user(session, user_id, active=False):
    row = session.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    if row is None:
        raise UsageError(404, "user_not_found")
    if active and row.status != "active":
        raise UsageError(403, "user_not_active")
    return row


def policy(session):
    return session.scalar(select(UsagePolicy).where(UsagePolicy.id == 1).execution_options(populate_existing=True))


def policy_dict(row):
    return {**{key: getattr(row, key) for key in POLICY_FIELDS}, "version": row.version}


def configure(session, actor, values, *, expected_version=None, now=None):
    lock(session)
    require_admin(session, actor)
    if set(values) != set(POLICY_FIELDS) or type(values["enabled"]) is not bool:
        raise UsageError(422, "invalid_usage_policy")
    try:
        if not isinstance(values["timezone"], str) or len(values["timezone"]) > 64:
            raise ValueError()
        ZoneInfo(values["timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise UsageError(422, "invalid_company_timezone") from None
    for key in ("monthly_questions", "daily_questions", "rate_count"):
        integer(values[key], 10**9)
    integer(values["rate_seconds"], 3600, 1)
    integer(values["monthly_budget_micro_usd"], 10**15)
    row = policy(session)
    if expected_version is not None:
        integer(expected_version, 2**31 - 1)
        if expected_version != (row.version if row else 0):
            raise UsageError(409, "usage_policy_version_conflict")
    before = policy_dict(row) if row else None
    if row and row.timezone != values["timezone"] and session.scalar(select(UsageRequest.id).limit(1)):
        raise UsageError(409, "timezone_locked_after_first_request")
    if row and all(getattr(row, key) == values[key] for key in POLICY_FIELDS):
        return row
    if row is None:
        row = UsagePolicy(id=1, version=1, **values)
        session.add(row)
    else:
        for key in POLICY_FIELDS:
            setattr(row, key, values[key])
        row.version += 1
    session.add(UsagePolicyAudit(actor_user_id=actor, before=before, after=policy_dict(row), created_at=clock(now)))
    session.flush()
    return row


def emit(session, request, key, event, now, *, questions=0, cost=0, hold=0, cost_month=None, verification=None):
    session.add(UsageEntry(request_id=request.id, user_id=request.user_id, event_key=key, event=event,
        question_month=request.question_month, question_day=request.question_day, question_delta=questions,
        cost_micro_usd=cost, hold_delta_micro_usd=hold, cost_month=cost_month,
        verification_ref=verification, created_at=now))
    session.flush()


def spent(session, month, user_id=None):
    query = select(func.coalesce(func.sum(UsageEntry.cost_micro_usd), 0)).where(UsageEntry.cost_month == month)
    if user_id is not None:
        query = query.where(UsageEntry.user_id == user_id)
    return int(session.scalar(query))


def total_held(session):
    held = int(session.scalar(select(func.coalesce(func.sum(UsageEntry.hold_delta_micro_usd), 0))))
    if held < 0:
        raise UsageError(409, "ledger_hold_invariant_violated")
    return held


def count_questions(session, user_id, *, month=None, day=None):
    query = select(func.coalesce(func.sum(UsageEntry.question_delta), 0)).where(UsageEntry.user_id == user_id)
    if month:
        query = query.where(UsageEntry.question_month == month)
    if day:
        query = query.where(UsageEntry.question_day == day)
    used = -int(session.scalar(query))
    if used < 0:
        raise UsageError(409, "ledger_credit_invariant_violated")
    return used


def rate_count(session, user_id, settings, now):
    # Refunded attempts still count. Rejected admissions never reach a provider.
    return int(session.scalar(select(func.count()).select_from(UsageRequest).where(
        UsageRequest.user_id == user_id, UsageRequest.created_at > now - timedelta(seconds=settings.rate_seconds))))


@dataclass(frozen=True)
class Admission:
    request: UsageRequest
    created: bool


def reserve(session, user_id, request_key, fingerprint, estimated_micro_usd, *, now=None):
    lock(session)
    now = clock(now)
    user(session, user_id, active=True)
    reference(request_key)
    integer(estimated_micro_usd)
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise UsageError(422, "invalid_request_fingerprint")
    existing = session.scalar(select(UsageRequest).where(UsageRequest.user_id == user_id, UsageRequest.request_key == request_key))
    if existing:
        if existing.fingerprint != fingerprint or existing.estimated_micro_usd != estimated_micro_usd:
            raise UsageError(409, "request_key_payload_conflict")
        return Admission(existing, False)
    settings = policy(session)
    if settings is None:
        raise UsageError(503, "usage_policy_not_configured")
    if not settings.enabled:
        raise UsageError(429, "question_processing_disabled")
    month, day = periods(now, settings.timezone)
    for exceeded, reason in (
        (count_questions(session, user_id, month=month) >= settings.monthly_questions, "monthly_question_limit"),
        (count_questions(session, user_id, day=day) >= settings.daily_questions, "daily_question_limit"),
        (rate_count(session, user_id, settings, now) >= settings.rate_count, "question_rate_limit"),
    ):
        if exceeded:
            raise UsageError(429, reason)
    available = settings.monthly_budget_micro_usd - spent(session, month) - total_held(session)
    if available <= 0 or available < estimated_micro_usd:
        raise UsageError(429, "company_budget_limit")
    request = UsageRequest(user_id=user_id, request_key=request_key, fingerprint=fingerprint,
        policy_version=settings.version, timezone=settings.timezone, question_month=month, question_day=day,
        estimated_micro_usd=estimated_micro_usd, held_micro_usd=estimated_micro_usd, created_at=now)
    session.add(request)
    session.flush()
    emit(session, request, "reserved", "question_reserved", now, questions=-1, hold=estimated_micro_usd)
    return Admission(request, True)


def request_row(session, identity):
    row = session.scalar(select(UsageRequest).where(UsageRequest.id == identity).execution_options(populate_existing=True))
    if row is None:
        raise UsageError(404, "usage_request_not_found")
    return row


def start(session, request_id, *, now=None):
    lock(session)
    row = request_row(session, request_id)
    user(session, row.user_id, active=True)
    if row.state != "reserved":
        raise UsageError(409, "request_already_started_or_closed")
    row.state, row.claim_id, row.started_at = "running", str(uuid.uuid4()), clock(now)
    emit(session, row, "started", "execution_started", row.started_at)
    return row.claim_id


def claimed(session, request_id, claim_id):
    row = request_row(session, request_id)
    if not claim_id or row.claim_id != claim_id:
        raise UsageError(403, "invalid_execution_claim")
    return row


def cost_totals(session, request_id):
    paid = int(session.scalar(select(func.coalesce(func.sum(UsageEntry.cost_micro_usd), 0)).where(UsageEntry.request_id == request_id)))
    pending = int(session.scalar(select(func.coalesce(func.sum(ProviderAttempt.max_micro_usd), 0)).where(
        ProviderAttempt.request_id == request_id, ProviderAttempt.state == "authorized")))
    return paid, pending


@dataclass(frozen=True)
class AttemptAdmission:
    attempt: ProviderAttempt
    created: bool


def authorize_attempt(session, request_id, claim_id, attempt_key, provider, model, max_micro_usd, *, now=None):
    lock(session)
    row = claimed(session, request_id, claim_id)
    user(session, row.user_id, active=True)
    reference(attempt_key)
    label(provider, 80)
    label(model, 100)
    integer(max_micro_usd)
    old = session.scalar(select(ProviderAttempt).where(ProviderAttempt.request_id == request_id, ProviderAttempt.attempt_key == attempt_key))
    if old:
        if (old.provider, old.model, old.max_micro_usd) != (provider, model, max_micro_usd):
            raise UsageError(409, "attempt_key_payload_conflict")
        return AttemptAdmission(old, False)  # Never invoke a provider on a replay.
    if row.state != "running":
        raise UsageError(409, "request_not_running")
    paid, pending = cost_totals(session, request_id)
    if paid + pending + max_micro_usd > row.estimated_micro_usd:
        raise UsageError(409, "attempt_exceeds_reserved_envelope")
    attempt = ProviderAttempt(request_id=request_id, attempt_key=attempt_key, provider=provider,
        model=model, max_micro_usd=max_micro_usd, created_at=clock(now))
    session.add(attempt)
    session.flush()
    emit(session, row, "authorize:" + attempt_key, "attempt_authorized", clock(now))
    return AttemptAdmission(attempt, True)


def mark_uncertain(session, request_id, claim_id, *, now=None):
    lock(session)
    row = claimed(session, request_id, claim_id)
    if row.state == "uncertain":
        return row
    if row.state != "running":
        raise UsageError(409, "request_not_running")
    row.state = "uncertain"
    emit(session, row, "uncertain", "reconciliation_required", clock(now))
    return row


def settle_attempt(session, request_id, claim_id, attempt_key, receipt_key, actual_micro_usd,
                   *, input_tokens=0, output_tokens=0, now=None):
    lock(session)
    row = claimed(session, request_id, claim_id)
    reference(attempt_key)
    reference(receipt_key, 128)
    integer(actual_micro_usd)
    integer(input_tokens, 10**9)
    integer(output_tokens, 10**9)
    attempt = session.scalar(select(ProviderAttempt).where(ProviderAttempt.request_id == request_id,
        ProviderAttempt.attempt_key == attempt_key).execution_options(populate_existing=True))
    if attempt is None:
        raise UsageError(404, "attempt_not_authorized")
    if attempt.state == "settled":
        if (attempt.receipt_key, attempt.actual_micro_usd, attempt.input_tokens, attempt.output_tokens) != (receipt_key, actual_micro_usd, input_tokens, output_tokens):
            raise UsageError(409, "receipt_payload_conflict")
        return attempt
    if row.state not in ("running", "uncertain"):
        raise UsageError(409, "request_not_running")
    duplicate = session.scalar(select(ProviderAttempt.id).where(ProviderAttempt.provider == attempt.provider,
        ProviderAttempt.receipt_key == receipt_key))
    if duplicate:
        raise UsageError(409, "provider_receipt_already_recorded")
    now = clock(now)
    attempt.state, attempt.actual_micro_usd, attempt.receipt_key = "settled", actual_micro_usd, receipt_key
    attempt.charged_month = periods(now, row.timezone)[0]
    attempt.input_tokens, attempt.output_tokens, attempt.settled_at = input_tokens, output_tokens, now
    session.flush()
    paid, pending = cost_totals(session, request_id)
    # Overruns must not consume the money still covering other in-flight attempts.
    paid += actual_micro_usd  # The current immutable receipt entry is appended below.
    old_hold = row.held_micro_usd
    row.held_micro_usd = max(row.estimated_micro_usd - paid, pending, 0)
    emit(session, row, "settle:" + attempt_key, "attempt_settled", now, cost=actual_micro_usd,
         hold=row.held_micro_usd - old_hold, cost_month=attempt.charged_month)
    if actual_micro_usd > attempt.max_micro_usd and row.state == "running":
        mark_uncertain(session, request_id, claim_id, now=now)
    return attempt


def finish(session, request_id, claim_id, outcome, *, verification_ref=None, now=None):
    lock(session)
    row = claimed(session, request_id, claim_id)
    if outcome not in ("answered", "no_answer", "technical_error"):
        raise UsageError(422, "invalid_question_outcome")
    if row.state in ("completed", "refunded"):
        if row.outcome != outcome:
            raise UsageError(409, "terminal_outcome_conflict")
        return row
    if row.state not in ("running", "uncertain"):
        raise UsageError(409, "request_not_running")
    if row.state == "uncertain" and not verification_ref:
        raise UsageError(409, "authoritative_reconciliation_required")
    if verification_ref:
        reference(verification_ref, 128)
    if session.scalar(select(ProviderAttempt.id).where(ProviderAttempt.request_id == request_id,
                                                      ProviderAttempt.state == "authorized").limit(1)):
        raise UsageError(409, "provider_attempt_still_unsettled")
    now = clock(now)
    old_hold = row.held_micro_usd
    refund = outcome == "technical_error"
    row.state, row.outcome, row.closed_at, row.held_micro_usd = "refunded" if refund else "completed", outcome, now, 0
    emit(session, row, "closed", "question_refunded" if refund else "request_completed", now,
         questions=1 if refund else 0, hold=-old_hold, verification=verification_ref)
    return row


def cancel_before_start(session, user_id, request_key, *, now=None):
    lock(session)
    row = session.scalar(select(UsageRequest).where(UsageRequest.user_id == user_id,
        UsageRequest.request_key == request_key).execution_options(populate_existing=True))
    if row is None:
        raise UsageError(404, "usage_request_not_found")
    if row.state == "refunded" and row.outcome == "cancelled":
        return row
    if row.state != "reserved":
        raise UsageError(409, "cannot_cancel_started_request")
    now = clock(now)
    old_hold = row.held_micro_usd
    row.state, row.outcome, row.closed_at, row.held_micro_usd = "refunded", "cancelled", now, 0
    emit(session, row, "closed", "question_refunded", now, questions=1, hold=-old_hold)
    return row


def personal_summary(session, user_id, *, now=None):
    lock(session)
    user(session, user_id)
    settings = policy(session)
    if settings is None:
        return {"user_id": user_id, "configured": False, "enabled": False}
    now = clock(now)
    month, day = periods(now, settings.timezone)
    monthly = count_questions(session, user_id, month=month)
    daily = count_questions(session, user_id, day=day)
    rate = rate_count(session, user_id, settings, now)
    return {"user_id": user_id, "configured": True, "enabled": settings.enabled,
        "timezone": settings.timezone, "month": month, "day": day, "monthly_used_or_held": monthly,
        "monthly_remaining": max(settings.monthly_questions - monthly, 0), "daily_used_or_held": daily,
        "daily_remaining": max(settings.daily_questions - daily, 0), "rate_remaining": max(settings.rate_count - rate, 0),
        "spent_micro_usd": spent(session, month, user_id),
        "company_budget_blocked": settings.monthly_budget_micro_usd - spent(session, month) - total_held(session) <= 0}


def company_summary(session, actor, *, now=None):
    lock(session)
    require_admin(session, actor)
    settings = policy(session)
    if settings is None:
        return {"configured": False, "enabled": False}
    month = periods(clock(now), settings.timezone)[0]
    actual, held = spent(session, month), total_held(session)
    return {"configured": True, "enabled": settings.enabled, "month": month,
        "budget_micro_usd": settings.monthly_budget_micro_usd, "spent_micro_usd": actual,
        "held_micro_usd": held, "remaining_micro_usd": max(settings.monthly_budget_micro_usd - actual - held, 0),
        "over_budget_micro_usd": max(actual + held - settings.monthly_budget_micro_usd, 0),
        "unresolved_requests": int(session.scalar(select(func.count()).select_from(UsageRequest).where(UsageRequest.state.in_(ACTIVE))))}
