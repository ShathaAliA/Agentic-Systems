from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import DevelopmentSessionValidator, unauthorized
from app.models import Permission, Role, RoleAssignment, RolePermission, User
from app.services.authorization import has_permission

bearer = HTTPBearer(auto_error=False)


def require_development_auth(request: Request):
    settings = request.app.state.auth_settings
    if settings.mode != "development_password":
        raise HTTPException(503, "Development authentication is disabled")
    return settings


def get_central_session(request: Request):
    engine = request.app.state.central_engine
    if engine is None:
        from app.database import engine  # Day 1's runtime privilege guard remains enabled.
    with Session(engine) as session:
        yield session


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings=Depends(require_development_auth),
    session: Session = Depends(get_central_session),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    return DevelopmentSessionValidator().validate(session, credentials.credentials)


def get_current_user(context=Depends(get_auth_context)) -> User:
    """Only validated credentials resolve identity; request user IDs are ignored."""
    return context.user


def require_department_permission(permission_code: str):
    def check(department_id: str, user: User = Depends(get_current_user),
              session: Session = Depends(get_central_session)):
        if not has_permission(session, user.id, permission_code, department_id):
            raise HTTPException(403, "Permission denied")
        return user
    return check


def require_global_permission(permission_code: str):
    def check(user: User = Depends(get_current_user), session: Session = Depends(get_central_session)):
        allowed = session.scalar(
            select(Permission.id).join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .where(RoleAssignment.user_id == user.id, RoleAssignment.department_id.is_(None),
                   Role.scope == "global", Permission.code == permission_code).limit(1)
        )
        if allowed is None:
            raise HTTPException(403, "Permission denied")
        return user
    return check
