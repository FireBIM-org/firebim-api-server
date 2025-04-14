from pydantic import BaseModel, EmailStr
from typing import Optional


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class UserBase(BaseModel):
    username: str
    email: EmailStr
    

class UserCreate(UserBase):
    password: str


class User(UserBase):
    id: str
    is_active: bool

    class Config:
        orm_mode = True