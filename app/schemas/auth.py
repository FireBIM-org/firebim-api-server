from pydantic import EmailStr
from sqlmodel import SQLModel


class UserBase(SQLModel):
    username: str
    email: EmailStr


class User(UserBase):
    id: str
    is_active: bool
    # ADMIN_GROUP in the verified token's groups, or the token email in ADMIN_EMAILS (see app/auth/admin.py)
    is_admin: bool

    class Config:
        orm_mode = True
