from pydantic import EmailStr
from typing import Optional
from sqlmodel import SQLModel


class Token(SQLModel):
    access_token: str
    token_type: str


class TokenData(SQLModel):
    username: Optional[str] = None


class UserBase(SQLModel):
    username: str
    email: EmailStr
    

class UserCreate(UserBase):
    password: str


class User(UserBase):
    id: str
    is_active: bool

    class Config:
        orm_mode = True