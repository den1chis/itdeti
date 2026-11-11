"""Access model of the CRM API (night 2): roles, ownership, and an explicit access class on every route.

Roles: admin, teacher, methodist. A user has a SET of roles (users.roles; empty -> [users.role]);
permissions add up. Roles are read from the database on every request (get_current_user loads the user),
so deactivation and role changes take effect immediately; an inactive user gets 401.

Every route must carry exactly one kind of access-class marker in its dependencies:
  require_admin               admin only
  require_roles(*roles)       any one of the roles is enough (admin is NOT implied: list it)
  require_staff               admin / teacher / methodist
  get_current_student         student-site token (core.student_auth)
or be listed in PUBLIC_ROUTES. tests/test_access_model.py fails on any unmarked route.

Ownership ("only own students") is filtered IN SQL: owned_student_ids(user) is a SELECT to use with .in_();
foreign rows are answered with 404, never 403."""
from typing import Iterable, Optional, Set
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_current_user
from models.user import User

ADMIN, TEACHER, METHODIST = "admin", "teacher", "methodist"
STAFF_ROLES = (ADMIN, TEACHER, METHODIST)

ACCESS_ATTR = "__access_class__"

# Routes that need no authentication at all (method, path). Everything else must be marked.
PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
    ("POST", "/student/auth/login"),
    # FastAPI documentation
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
}


# ---------------------------------------------------------------- roles
def roles_of(user) -> Set[str]:
    """Roles of a user: users.roles, or [users.role] while the set is empty (rows created before migration 013)."""
    roles = [str(r) for r in (getattr(user, "roles", None) or []) if r]
    if not roles and getattr(user, "role", None):
        roles = [str(user.role)]
    return set(roles)


def has_any_role(user, *roles: str) -> bool:
    return bool(roles_of(user) & set(roles))


def is_admin(user) -> bool:
    return ADMIN in roles_of(user)


def role_sql(role: str):
    """SQL condition "user has this role" (same fallback as roles_of)."""
    from sqlalchemy import func

    return or_(User.roles.any(role), and_(func.cardinality(User.roles) == 0, User.role == role))


def primary_role(roles: Iterable[str]) -> str:
    """Value for the legacy single users.role column (read by the CRM front-end via /auth/me)."""
    roles = set(roles)
    for role in (ADMIN, TEACHER, METHODIST):
        if role in roles:
            return role
    raise ValueError("no staff role")


# ---------------------------------------------------------------- access-class markers (dependencies)
def _mark(fn, label: str):
    setattr(fn, ACCESS_ATTR, label)
    return fn


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not is_admin(current_user):
        raise _forbidden()
    return current_user


_mark(require_admin, "admin")

_ROLE_DEPENDENCIES = {}


def require_roles(*roles: str):
    """Dependency: the user has at least one of `roles`. The same function object is returned for the
    same set of roles, so FastAPI can cache it within a request."""
    key = tuple(sorted(set(roles)))
    unknown = set(key) - set(STAFF_ROLES)
    if not key or unknown:
        raise ValueError(f"unknown roles: {unknown or 'none'}")
    if key in _ROLE_DEPENDENCIES:
        return _ROLE_DEPENDENCIES[key]

    async def checker(current_user: User = Depends(get_current_user)) -> User:
        if not has_any_role(current_user, *key):
            raise _forbidden()
        return current_user

    checker.__name__ = "require_roles_" + "_".join(key)
    _mark(checker, "roles: " + ", ".join(r for r in STAFF_ROLES if r in key))
    _ROLE_DEPENDENCIES[key] = checker
    return checker


require_staff = require_roles(*STAFF_ROLES)
require_teacher = require_roles(ADMIN, TEACHER)          # admin + teacher (own students for teacher)
require_catalog_editor = require_roles(ADMIN, METHODIST)  # LMS catalog writes


def access_markers(route) -> list:
    """Access-class labels found in the dependency tree of a route (router-level + route-level)."""
    labels = []

    def walk(dependant):
        for dep in dependant.dependencies:
            label = getattr(dep.call, ACCESS_ATTR, None)
            if label and label not in labels:
                labels.append(label)
            walk(dep)

    walk(route.dependant)
    return labels


def unmarked_routes(app) -> list:
    """(method, path) of every route that has no access-class marker and is not in PUBLIC_ROUTES.
    Must be empty; checked by tests/test_access_model.py."""
    from fastapi.routing import APIRoute

    missing = []
    for route in app.routes:
        methods = sorted(m for m in (getattr(route, "methods", None) or []) if m != "HEAD")
        for method in methods:
            if (method, route.path) in PUBLIC_ROUTES:
                continue
            if not isinstance(route, APIRoute) or not access_markers(route):
                missing.append((method, route.path))
    return missing


def access_label(route) -> str:
    """Human-readable access class of a route (for docs/ROUTES_ACCESS.md)."""
    labels = access_markers(route)
    if not labels:
        methods = getattr(route, "methods", set()) or set()
        if any((m, route.path) in PUBLIC_ROUTES for m in methods):
            return "public"
        return ""
    # Router-level "admin" + route-level roles -> the route is admin only.
    if "admin" in labels:
        return "admin"
    return " & ".join(labels)


# ---------------------------------------------------------------- ownership (filtered in SQL)
def owned_student_ids(user):
    """SELECT of student ids the user may see as "own": admin -> all, teacher -> students.teacher_id = user,
    anybody else -> none. Use as `.where(X.student_id.in_(owned_student_ids(user)))`."""
    from models.student import Student

    if is_admin(user):
        return select(Student.id)
    if TEACHER in roles_of(user):
        return select(Student.id).where(Student.teacher_id == user.id)
    return select(Student.id).where(false())


def owned_student_filter(user):
    """WHERE clause on Student for "own students" (None = no restriction, admin)."""
    from models.student import Student

    if is_admin(user):
        return None
    if TEACHER in roles_of(user):
        return Student.teacher_id == user.id
    return false()


async def student_or_404(db: AsyncSession, user, student_id: UUID, message: str = "Student not found"):
    """The student if it exists AND belongs to the user (admin: any). Otherwise 404 — also for a foreign one."""
    from models.student import Student

    query = select(Student).where(Student.id == student_id)
    condition = owned_student_filter(user)
    if condition is not None:
        query = query.where(condition)
    student = await db.scalar(query)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, message)
    return student


def substitute_lessons(user):
    """SELECT of lesson ids where the user is the teacher of that one lesson (lessons.teacher_id) while the
    student's permanent teacher is somebody else (or nobody)."""
    from models.lesson import Lesson
    from models.student import Student

    return (
        select(Lesson.id)
        .join(Student, Student.id == Lesson.student_id)
        .where(Lesson.teacher_id == user.id, or_(Student.teacher_id.is_(None), Student.teacher_id != user.id))
    )


def effective_teacher_sql():
    """SQL expression: the teacher of a lesson = lessons.teacher_id, or the student's permanent teacher."""
    from sqlalchemy import func

    from models.lesson import Lesson
    from models.student import Student

    return func.coalesce(Lesson.teacher_id, Student.teacher_id)


async def active_teacher_or_400(db: AsyncSession, teacher_id: Optional[UUID]):
    """For assignments: the user must exist, be active and have the teacher role."""
    if teacher_id is None:
        return None
    user = await db.scalar(select(User).where(User.id == teacher_id, User.is_active.is_(True), role_sql(TEACHER)))
    if not user:
        raise HTTPException(400, "Преподаватель не найден, выключен или у него нет роли «преподаватель»")
    return user
