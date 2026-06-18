from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Post, User
from ..database import get_db
from ..schemas import UserCreate,UserResponse, UserPrivateResponse, UserUpdate, PostResponse, Token

from datetime import timedelta
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func

from ..auth import create_access_token, hash_password, oauth2_scheme, verify_access_token, verify_password

from ..config import settings
router = APIRouter()

@router.post("", response_model=UserPrivateResponse, status_code=status.HTTP_201_CREATED,)
async def create_user(user: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(User).where(func.lower(User.username) == user.username.lower()))
    existing_user = result.scalars().first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username already exists"
        )
    
    result = await db.execute(select(User).where(func.lower(User.email) == user.email.lower()))
    existing_email = result.scalars().first()
    
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="email already exists"
        )
    
    new_user = User(
        username = user.username,
        email= user.email.lower(),
        password_hash = hash_password(user.password)
    )
    db.add(new_user) # stages
    await db.commit() # executes and save to db
    await db.refresh(new_user) # auto tracked by sqlalchemy but still good habit for server side error handling.
    
    return new_user

@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)]
):
    result = await db.execute(
        select(User).where(
            func.lower(User.email) == form_data.username.lower(),
        )
    )
    
    user = result.scalars().first()
    
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail = "Incorrect email or password",
            headers = {"WWW-Authenticate":"Bearer"},
                    )
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub":str(user.id)},
        expires_delta=access_token_expires,
    )
    return Token(access_token=access_token, token_type="bearer")

@router.get("/me", response_model=UserPrivateResponse)
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate":"Bearer"},
        )
        
    try: 
        user_id_int = int(user_id)
        
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Token",
            headers={"WWW-Authentiate":"Bearer"},
        )
        
    result = await db.execute(
        select(User).where(User.id == user_id_int),
    )
    
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code= status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate":"Bearer"},
        )
    
    return user

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(User).where(User.id==user_id)
    )
    user = result.scalars().first()
    
    if user:
        return user

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

@router.get('/{user_id}/posts', response_model=list[PostResponse])
async def get_user_posts(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail = "user not found.")
    
    result = await db.execute(select(Post).options(selectinload(Post.author)).where(Post.user_id==user_id).order_by(Post.date_posted.desc()))
    posts = result.scalars().all()
    
    return posts    

@router.patch("/{user_id}", response_model=UserPrivateResponse)
async def update_user(user_id: int, user_data: UserUpdate,db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(User).where(User.id==user_id)
    )
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if user_data.username is not None and user_data.username.lower() != user.username.lower():
        result = await db.execute(select(User).where(func.lower(User.username) == user_data.username.lower()))
        existing_username = result.scalars().first()
        if existing_username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists.")
        
    if user_data.email is not None and user_data.email.lower() != user.email.lower():
        result = await db.execute(select(User).where(func.lower(User.email) == user_data.email.lower()))
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail = "Email already exists.")
        
    if user_data.username is not None:
        user.username = user_data.username 
    
    if user_data.email is not None:
        user.email = user_data.email.lower()
        
    if user_data.image_file is not None:
        user.image_file = user_data.image_file    
        
    await db.commit()
    await db.refresh(user)
    return user


@router.delete('/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found.")

    await db.delete(user)
    await db.commit()
