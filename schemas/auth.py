from pydantic import BaseModel, EmailStr
from typing import List, Optional

from pydantic import Field
import uuid


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    roles: List[str] = []
    full_name: str
    phone: Optional[str]

    class Config:
        from_attributes = True


# ---------------------------------------------------------------- handoff CRM -> learning platform (night 5)
class HandoffCreate(BaseModel):
    target: str = Field(min_length=1, max_length=60)  # profile | catalog | student:<uuid>


class HandoffIssued(BaseModel):
    code: str
    expires_in: int
    target: str


class HandoffRedeem(BaseModel):
    code: str = Field(min_length=1, max_length=200)


class HandoffTokens(TokenResponse):
    target: str
