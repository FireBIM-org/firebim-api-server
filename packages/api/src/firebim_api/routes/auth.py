from fastapi import APIRouter, Depends
from firebim_common.models import User
from firebim_common.schemas.auth import User as UserSchema

from firebim_api.auth.jwt import get_current_active_user

router = APIRouter(tags=["authentication"])


@router.get("/users/me", response_model=UserSchema)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user
