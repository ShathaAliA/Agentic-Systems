"""Document audience filtering — applied BEFORE retrieval, re-checked AFTER."""
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.documents.models import Document, DocumentAudience


def user_can_access_document(
    db: Session,
    document_id: int,
    user_department_ids: list[str],
    user_role_ids: list[str],
) -> bool:
    """
    Audience rules (department AND role):
      - No rows → visible within caller's authorized department scope.
      - (dept_id, role_id): both must match.
      - (dept_id, NULL): user in dept, any role.
      - (NULL, role_id): any dept, user has role.
      - (NULL, NULL): visible to all (within dept scope already checked).
    """
    if not user_department_ids:
        return False

    audiences = db.execute(
        select(DocumentAudience).where(DocumentAudience.document_id == document_id)
    ).scalars().all()

    if not audiences:
        return True  # legacy: no audiences configured

    for a in audiences:
        if a.department_id is None and a.role_id is None:
            return True
        dept_match = (a.department_id is None) or (a.department_id in user_department_ids)
        role_match = (a.role_id is None) or (a.role_id in user_role_ids)
        if dept_match and role_match:
            return True
    return False


def filter_visible_documents(
    db: Session,
    documents: list[Document],
    user_department_ids: list[str],
    user_role_ids: list[str],
) -> list[Document]:
    return [
        d for d in documents
        if user_can_access_document(db, d.id, user_department_ids, user_role_ids)
    ]


def get_user_context(db_central, user_id: str) -> tuple[list[str], list[str]]:
    """Return (department_ids, role_ids) for the user from the CENTRAL database."""
    from app.models import DepartmentMembership, RoleAssignment

    depts = db_central.execute(
        select(DepartmentMembership.department_id).where(
            DepartmentMembership.user_id == user_id
        )
    ).scalars().all()

    roles = db_central.execute(
        select(RoleAssignment.role_id).where(
            RoleAssignment.user_id == user_id
        )
    ).scalars().all()

    return list(depts), list(roles)