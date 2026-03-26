from fastapi import APIRouter, Depends

from app.auth.jwt import get_current_active_user
from app.models.models import User
from app.schemas.auth import User as UserSchema

router = APIRouter(tags=["authentication"])


@router.get("/users/me", response_model=UserSchema)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user
