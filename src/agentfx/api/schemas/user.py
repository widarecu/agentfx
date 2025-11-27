from pydantic import BaseModel, EmailStr, Field, validator
from datetime import datetime
from ..models.user import UserRole


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None
    is_active: bool = True
    role: UserRole = UserRole.VIEWER


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=100)

    @validator("password")
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        # Add more password strength validation as needed
        return v


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    is_active: bool | None = None
    role: UserRole | None = None
    password: str | None = Field(None, min_length=8, max_length=100)


class UserInDBBase(UserBase):
    id: int
    created_at: datetime
    updated_at: datetime | None = None
    last_login: datetime | None = None

    class Config:
        from_attributes = True


class User(UserInDBBase):
    pass


class UserInDB(UserInDBBase):
    hashed_password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: User


class TokenData(BaseModel):
    username: str | None = None
    scopes: list[str] = []


class TokenPayload(BaseModel):
    sub: int | None = None
    exp: int | None = None
    iat: int | None = None
    scopes: list[str] = []


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False
