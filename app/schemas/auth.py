from pydantic import EmailStr
from sqlmodel import SQLModel


class UserBase(SQLModel):
    username: str
    email: EmailStr


class User(UserBase):
    id: str
    is_active: bool

    class Config:
        orm_mode = True
