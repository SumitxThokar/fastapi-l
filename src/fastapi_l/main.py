from fastapi import FastAPI, Request, HTTPException, status, Depends
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from typing import Annotated

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from .models import User, Post
from .database import engine, get_db
from .routers import posts, users

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # shutdown
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name ="static")
app.mount("/media", StaticFiles(directory="media"), name = "media")

templates = Jinja2Templates(directory="templates")

app.include_router(users.router, prefix = "/api/users", tags = ["users"])
app.include_router(posts.router, prefix = "/api/posts", tags = ["posts"])

@app.get("/", include_in_schema=False, name = "home")
@app.get("/posts", include_in_schema=False, name = "posts")
async def home(request: Request, db: Annotated[AsyncSession, Depends(get_db)], limit: int = 4):
    count_result = await db.execute(select(func.count()).select_from(Post))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Post)
        .options(selectinload(Post.author))
        .order_by(Post.date_posted.desc())
        .limit(limit)
    )
    posts = result.scalars().all()
    
    has_more = len(posts) < total

    return templates.TemplateResponse(
        request, 
        "home.html", 
        {
            "posts": posts, 
            "title": "Home",
            "limit": limit,
            "has_more": has_more
        }
    )

@app.get("/posts/{post_id}", include_in_schema=False)
async def get_post_page(request: Request, post_id:int, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Post).options(selectinload(Post.author)).where(Post.id == post_id))
    post = result.scalars().first()
    if post:
        title = post.title[:50] 
        return templates.TemplateResponse(
            request, 
            "post.html", 
            {"post": post,"title" : title })
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")
    
@app.get('/users/{user_id}/posts', include_in_schema=False, name = "user_posts")
async def user_posts_page(
    request: Request,
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 4
):
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    
    count_result = await db.execute(select(func.count()).select_from(Post).where(Post.user_id == user_id))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Post)
        .options(selectinload(Post.author))
        .where(Post.user_id == user_id)
        .order_by(Post.date_posted.desc())
        .limit(limit)
    )
    posts = result.scalars().all()
    
    has_more = len(posts) < total

    return templates.TemplateResponse(
        request,
        "user_posts.html",
        {
            "posts": posts,
            "user": user, 
            "title": f"{user.username}'s Posts",
            "limit": limit,
            "has_more": has_more
        }        
    )

@app.get('/login', include_in_schema=False)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"title":"Login"},
    )
    
@app.get("/register", include_in_schema=False)
async def register_page(request: Request):
    return templates.TemplateResponse(
        request,
        "register.html",
        {"title":"Register"}
    )
@app.get("/forgot-password", include_in_schema=False)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        {"title": "Forgot Password"},
    )
    
@app.get('/reset-password', include_in_schema=False)
async def reset_password_page(request: Request):
    response =  templates.TemplateResponse(
        request,
        "reset_password.html",
        {"title":"Reset Password"}
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

@app.get('/account', include_in_schema=False)
async def account_page(request: Request):
    return templates.TemplateResponse(
        request,
        "account.html",
        {"title":"Account"},
    )
  
@app.exception_handler(StarletteHTTPException)
async def general_http_exception_handler(request:Request, exception: StarletteHTTPException):
    
    if request.url.path.startswith("/api"):
        return await http_exception_handler(request, exception)
    
    message = (
        exception.detail
        if exception.detail
        else "An error occured. Please check your request and try again."
    )
    
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exception.status_code,
            "ttile": exception.status_code,
            "message":message,
        },
        status_code=exception.status_code,
    ) 
    
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exception: RequestValidationError
):
    if request.url.path.startswith("/api"):
        return await request_validation_exception_handler(request, exception)

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "title": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "message": "Invalid request. Please check your input and try again.",
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )