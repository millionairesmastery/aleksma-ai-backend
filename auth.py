"""JWT authentication, password hashing, and user dependency injection."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from db import get_connection, put_connection

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 30

bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = "User"


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class UserInfo(BaseModel):
    id: int
    email: str
    name: str
    is_active: bool = True
    avatar_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Password utils
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    pwd_bytes = password[:72].encode('utf-8')
    salt = _bcrypt.gensalt()
    return _bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    pwd_bytes = plain[:72].encode('utf-8')
    hashed_bytes = hashed.encode('utf-8')
    return _bcrypt.checkpw(pwd_bytes, hashed_bytes)


# ---------------------------------------------------------------------------
# Token utils
# ---------------------------------------------------------------------------


def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire, "type": "access"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire, "type": "refresh"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


# ---------------------------------------------------------------------------
# Dependency: get current user from JWT (optional — returns None if no token)
# ---------------------------------------------------------------------------


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[UserInfo]:
    """Returns the authenticated user or None if no token provided."""
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            return None
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, name, is_active, avatar_url FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row or not row[3]:  # not found or inactive
                return None
            return UserInfo(id=row[0], email=row[1], name=row[2], is_active=row[3], avatar_url=row[4])
    finally:
        put_connection(conn)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> UserInfo:
    """Returns the authenticated user or raises 401."""
    user = await get_current_user_optional(credentials)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# Auth operations
# ---------------------------------------------------------------------------


def register_user(email: str, password: str, name: str) -> dict:
    """Create a new user with personal workspace. Returns user dict + tokens."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="Email already registered")

            hashed = hash_password(password)
            cur.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id, email, name",
                (email, name, hashed),
            )
            row = cur.fetchone()
            user_id, user_email, user_name = row

            cur.execute(
                "INSERT INTO projects (user_id, name, description) VALUES (%s, %s, %s) RETURNING id",
                (user_id, "My First Project", ""),
            )
            project_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO assemblies (project_id, name) VALUES (%s, %s)",
                (project_id, "Main Assembly"),
            )

        conn.commit()

        return {
            "access_token": create_access_token(user_id, user_email),
            "refresh_token": create_refresh_token(user_id),
            "token_type": "bearer",
            "user": {"id": user_id, "email": user_email, "name": user_name},
        }
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def login_user(email: str, password: str) -> dict:
    """Authenticate and return tokens."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, name, password_hash, is_active FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="Invalid email or password")

            user_id, user_email, user_name, password_hash, is_active = row

            if not is_active:
                raise HTTPException(status_code=403, detail="Account is disabled")

            # For legacy users without password (e.g., the seeded local@localhost)
            if not password_hash:
                raise HTTPException(status_code=401, detail="Invalid email or password")

            if not verify_password(password, password_hash):
                raise HTTPException(status_code=401, detail="Invalid email or password")

            return {
                "access_token": create_access_token(user_id, user_email),
                "refresh_token": create_refresh_token(user_id),
                "token_type": "bearer",
                "user": {"id": user_id, "email": user_email, "name": user_name},
            }
    finally:
        put_connection(conn)


def refresh_access_token(refresh_token: str) -> dict:
    """Issue a new access token from a refresh token."""
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = int(payload["sub"])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, name FROM users WHERE id = %s AND is_active = true", (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="User not found or inactive")

        return {
            "access_token": create_access_token(row[0], row[1]),
            "refresh_token": refresh_token,  # reuse existing refresh token
            "token_type": "bearer",
            "user": {"id": row[0], "email": row[1], "name": row[2]},
        }
    finally:
        put_connection(conn)
