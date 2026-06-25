from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, Query, BackgroundTasks

from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Post, User,PasswordResetToken
from ..database import get_db
from ..schemas import UserCreate,UserResponse, UserPrivateResponse, UserUpdate, PostResponse, Token, PaginatedPostResponse, ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest

from datetime import timedelta, UTC, datetime
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from PIL import UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from ..email_utils import send_password_reset_email

from ..auth import create_access_token, hash_password, verify_password, CurrentUser, generate_reset_token, hash_reset_token
from ..utils import delete_profile_image, process_profile_image

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
    current_user: CurrentUser,
   ): 
    return current_user

@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request_data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(User).where(
            func.lower(User.email) == request_data.email.lower(),
        ),
    )
    user = result.scalars().first()

    if user:
        await db.execute(
            sql_delete(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
            ),
        )

        token = generate_reset_token()
        token_hash = hash_reset_token(token)
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.reset_token_expire_minutes,
        )

        reset_token =PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(reset_token)
        await db.commit()

        background_tasks.add_task(
            send_password_reset_email,
            to_email=user.email,
            username=user.username,
            token=token,
        )

    return {
        "message": "If an account exists with this email, you will receive password reset instructions.",
    }


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request_data: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    token_hash = hash_reset_token(request_data.token)

    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
        ),
    )
    reset_token = result.scalars().first()

    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if reset_token.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        await db.delete(reset_token)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    result = await db.execute(
        select(User).where(User.id == reset_token.user_id),
    )
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    user.password_hash = hash_password(request_data.new_password)

    await db.execute(
        sql_delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
        ),
    )

    await db.commit()
    return {
        "message": "Password reset successfully. You can now log in with your new password.",
    }


@router.patch("/me/password", status_code=status.HTTP_200_OK)
async def change_password(
    password_data: ChangePasswordRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.password_hash = hash_password(password_data.new_password)

    await db.execute(
        sql_delete(PasswordResetToken).where(
            PasswordResetToken.user_id == current_user.id,
        ),
    )

    await db.commit()
    return {"message": "Password changed successfully"}

    
@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(User).where(User.id==user_id)
    )
    user = result.scalars().first()
    
    if user:
        return user

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

@router.get('/{user_id}/posts', response_model=PaginatedPostResponse)
async def get_user_posts(
    user_id: int, 
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 4
    ):
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail = "user not found.")
    
    count_result = await db.execute(
        select(func.count())
        .select_from(Post)
        .where(Post.user_id==user_id)
    )
    
    total= count_result.scalar() or 0
    
    result = await db.execute(
        select(Post)
        .options(selectinload(Post.author))
        .where(Post.user_id==user_id)
        .order_by(Post.date_posted.desc())
        .offset(skip)
        .limit(limit)
        )
    posts = result.scalars().all()
    
    has_more = skip + len(posts) < total
    
    return PaginatedPostResponse(
        posts = [PostResponse.model_validate(post) for post in posts],
        total = total,
        skip = skip,
        limit = limit, 
        has_more = has_more
    )    

@router.patch("/{user_id}", response_model=UserPrivateResponse)
async def update_user(
    user_id: int, 
    user_data: UserUpdate,
    current_user:CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]):
    
    
    if user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user."
        )
        
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
        
    await db.commit()
    await db.refresh(user)
    return user


@router.delete('/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    if user_id!=current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this user."
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found.")

    old_filename = user.image_file
    
    await db.delete(user)
    await db.commit()
    
    if old_filename:
        delete_profile_image(old_filename)


@router.patch('/{user_id}/picture', response_model=UserPrivateResponse)
async def upload_profile_picture(
    user_id: int,
    file: UploadFile,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    
    if current_user.id !=user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail= "Not authorized to update this user's picture"
        )
        
    content = await file.read()
    
    if len(content)> settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {settings.max_upload_size_bytes// (1024* 1024)}MB"
        )
    
    try:
        new_filename = await run_in_threadpool(process_profile_image, content)
    
    except UnidentifiedImageError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail = "Invalid image file. please upload a valid image (JPEG, PNG, GIF, WebP)"
        ) from error
        
    old_filename = current_user.image_file
    
    current_user.image_file = new_filename
    
    await db.commit()
    await db.refresh(current_user)
    
    if old_filename:
        delete_profile_image(old_filename)
    
    return current_user

@router.delete('/{user_id}/picture', response_model=UserPrivateResponse)
async def delete_profile_picture(
    user_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)]
):
    if current_user.id !=user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail= "Not authorized to delete this user's picture"
        )
    
    old_filename = current_user.image_file
    
    if old_filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail = "No profile picture to delete",
        )
    
    current_user.image_file = None
    
    await db.commit()
    await db.refresh(current_user)
    
    delete_profile_image(old_filename)
    
    return current_user