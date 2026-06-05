from pydantic import BaseModel, ConfigDict, Field, EmailStr
from datetime import datetime

class UserBase(BaseModel):
    username: str= Field(min_length=1, max_length=30)
    email: EmailStr = Field(max_length=120)
    
class UserCreate(UserBase):
    pass

class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True, )
    
    id: int
    image_file: str |None
    image_path: str


class PostBase(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)

class PostCreate(PostBase):
    user_id: int 

class PostResponse(PostBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    date_posted: datetime
    user_id: int
    author: UserResponse