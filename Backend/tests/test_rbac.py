from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Base, Department, DepartmentMembership, Permission, Role, RolePermission, RoleAssignment, User
from app.services.authorization import has_permission


def test_department_permission_requires_active_user_department_and_membership():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s, s.begin():
        s.add_all([User(id="u",name="User",email="u@invalid"), Department(id="d",name="Legal"), Department(id="other",name="Other"), Role(id="r",name="Employee",scope="department"), Permission(id="p",code="documents:read")])
        s.flush()
        membership=DepartmentMembership(user_id="u",department_id="d")
        s.add_all([membership,RolePermission(role_id="r",permission_id="p"),RoleAssignment(user_id="u",role_id="r",department_id="d")])
        s.flush()
        assert has_permission(s,"u","documents:read","d")
        assert not has_permission(s,"u","documents:read","other")
        assert not has_permission(s,"u","documents:upload","d")
        membership.status="inactive"; s.flush()
        assert not has_permission(s,"u","documents:read","d")
        membership.status="active"; s.get(Department,"d").status="provisioning"; s.flush()
        assert not has_permission(s,"u","documents:read","d")
        s.get(Department,"d").status="active"; s.get(User,"u").status="inactive"; s.flush()
        assert not has_permission(s,"u","documents:read","d")
    engine.dispose()
