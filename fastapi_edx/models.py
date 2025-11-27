from pydantic import BaseModel, EmailStr, validator
from typing import Optional

class UserData(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    
    @validator('email')
    def validate_email(cls, v):
        if not v or '@' not in v:
            raise ValueError('Invalid email format')
        return v.lower().strip()
