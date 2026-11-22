"""Admin-configurable open-handoff cap."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.handoffs.models import HandoffPolicy, HandoffPolicyAudit
from app.handoffs.service import DEFAULT_OPEN_CAP
from app.usage.service import UsageError, integer
from app.users.service import require_admin, writer_lock
from app.departments.service import DepartmentError


def current(session):
    return session.scalar(select(HandoffPolicy).where(HandoffPolicy.id == 1).execution_options(populate_existing=True))


def open_cap_provider(central_engine):
    """Read afresh on every creation so an Admin change applies at once."""
    def cap():
        with Session(central_engine) as s:
            row = current(s)
            return row.open_cap if row else DEFAULT_OPEN_CAP
    return cap


def view(row):
    return dict(open_cap=row.open_cap if row else DEFAULT_OPEN_CAP, version=row.version if row else 0,
                default=row is None)


def configure(session, actor, open_cap, expected_version):
    writer_lock(session, actor)
    require_admin(session, actor)
    try:
        integer(open_cap, 50, 1)
        integer(expected_version, 2**31 - 1)
    except UsageError:
        raise DepartmentError(422, "invalid_handoff_policy") from None
    row = current(session)
    if expected_version != (row.version if row else 0):
        raise DepartmentError(409, "handoff_policy_version_conflict")
    before = row.open_cap if row else None
    if row is not None and row.open_cap == open_cap:
        return view(row)
    if row is None:
        row = HandoffPolicy(id=1, open_cap=open_cap, version=1)
        session.add(row)
    else:
        row.open_cap, row.version = open_cap, row.version + 1
    session.add(HandoffPolicyAudit(actor_user_id=actor, before=before, after=open_cap, version=row.version))
    session.flush()
    return view(row)
