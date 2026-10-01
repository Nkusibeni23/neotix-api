from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import Admin, DbSession
from app.models import User
from app.schemas import UserCreate, UserOut, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(_: Admin, db: DbSession) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, _: Admin, db: DbSession) -> UserOut:
    user = User(
        email=body.email.lower(),
        name=body.name,
        role=body.role,
        organisation=body.organisation,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    return UserOut.model_validate(user)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: Admin, db: DbSession) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    # Stops the last admin locking everyone out of user management.
    if user.id == admin.id and (body.is_active is False or (body.role and body.role != user.role)):
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate or demote yourself")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    return UserOut.model_validate(user)
