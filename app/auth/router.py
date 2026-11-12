from fastapi import APIRouter, Depends

from app.auth.admin import get_is_admin
from app.auth.jwt import get_current_active_user
from app.models.models import User
from app.schemas.auth import User as UserSchema

router = APIRouter(tags=["authentication"])


@router.get("/users/me", response_model=UserSchema)
async def read_users_me(
    current_user: User = Depends(get_current_active_user),
    is_admin: bool = Depends(get_is_admin),
):
    return UserSchema(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        is_admin=is_admin,
    )
