from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.auth.jwt import create_access_token
from akashic.database import get_db
from akashic.models.user import User
from akashic.schemas.user import UserCreate, UserLogin, UserResponse, TokenResponse

router = APIRouter(prefix="/api/users", tags=["users"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username taken")
    # First user gets admin role; subsequent users get viewer
    count_result = await db.execute(select(func.count(User.id)))
    user_count = count_result.scalar()
    role = "admin" if user_count == 0 else "viewer"
    user = User(
        username=data.username,
        email=data.email,
        password_hash=pwd_context.hash(data.password),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user
