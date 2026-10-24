"""JWT-based authentication — login, token validation, refresh, RBAC.

Roles: admin (full access), operator (run tests, view results), viewer (read-only).

Tokens:
- access_token: short-lived (JWT_EXPIRE_MINUTES, default 1h), used for API auth.
- refresh_token: long-lived (JWT_REFRESH_EXPIRE_DAYS, default 7d), stored in DB,
  used only at /auth/refresh to get new access_token.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import InvalidTokenError as JWTError
from passlib.context import CryptContext
from pydantic import BaseModel

from app.core.config import settings
from app.core.time import utc_now_naive
from app.services.db_store import db_store

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_scheme = HTTPBearer(auto_error=False)

# ── Role definitions ────────────────────────────────────────

class Role:
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"

    _hierarchy = {ADMIN: 3, OPERATOR: 2, VIEWER: 1}

    @classmethod
    def can(cls, user_role: str, required_role: str) -> bool:
        return cls._hierarchy.get(user_role, 0) >= cls._hierarchy.get(required_role, 0)

    @classmethod
    def all_roles(cls) -> list[str]:
        return [cls.ADMIN, cls.OPERATOR, cls.VIEWER]

# ── Models ───────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    username: str
    role: str

class RefreshRequest(BaseModel):
    refresh_token: str

class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    username: str
    role: str
    user_id: Optional[int] = None


# ── Password helpers ─────────────────────────────────────────

def hash_password(password: str) -> str:
    validate_password_bytes(password)
    return pwd_context.hash(password)


def validate_password_bytes(password: str) -> str:
    if len(password.encode('utf-8')) > 72:
        raise ValueError('Password must not exceed 72 UTF-8 bytes')
    return password


def verify_password(plain: str, hashed: str) -> bool:
    if len(plain.encode('utf-8')) > 72:
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


# ── Token helpers ────────────────────────────────────────────

def create_access_token(username: str, role: str, session_id: str = "",
                        expires_delta: Optional[timedelta] = None) -> str:
    expire = utc_now_naive() + (expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES))
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
        "iat": utc_now_naive(),
        "type": "access",
        "sid": session_id,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token() -> str:
    """Generate a cryptographically random refresh token (stored in DB)."""
    return secrets.token_urlsafe(64)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


# ── Auth operations ──────────────────────────────────────────

async def login(username: str, password: str) -> Optional[TokenResponse]:
    user = await db_store.get_user_by_username(username)
    if not user or not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None

    refresh_token = create_refresh_token()
    access_token = create_access_token(user.username, user.role, _hash_token(refresh_token))

    # Store refresh token hash in DB
    await db_store.store_refresh_token(
        username=user.username,
        token_hash=_hash_token(refresh_token),
        expires_at=utc_now_naive() + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS),
    )

    logger.info(f"User '{username}' logged in (role={user.role})")
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        username=user.username,
        role=user.role,
    )


async def refresh_access_token(refresh_token: str) -> Optional[TokenResponse]:
    """Issue a new access token using a valid refresh token."""
    token_hash = _hash_token(refresh_token)

    stored = await db_store.get_refresh_token(token_hash)
    if not stored:
        return None
    if stored["expires_at"] and datetime.fromisoformat(stored["expires_at"]) < utc_now_naive():
        await db_store.delete_refresh_token(token_hash)
        return None
    if stored.get("revoked"):
        return None

    user = await db_store.get_user_by_username(stored["username"])
    if not user or not user.is_active:
        return None

    new_refresh = create_refresh_token()
    rotated = await db_store.rotate_refresh_token(
        old_hash=token_hash,
        new_hash=_hash_token(new_refresh),
        expires_at=utc_now_naive() + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS),
    )
    if not rotated:
        return None
    new_access = create_access_token(user.username, user.role, _hash_token(new_refresh))

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        username=user.username,
        role=user.role,
    )


async def logout(refresh_token: str):
    """Revoke a refresh token."""
    await db_store.delete_refresh_token(_hash_token(refresh_token))


# ── Local single-user mode ───────────────────────────────────

_LOCAL_SESSION_TTL = timedelta(days=3650)
_local_token: Optional[str] = None
_local_lock = asyncio.Lock()


async def local_access_token(renew: bool = False) -> str:
    """Session for the default admin, used when a request carries no credentials.

    Private local deployment has no login step; downstream WS/SSE/scope checks
    still revalidate this token like any other session.
    """
    global _local_token
    async with _local_lock:
        if _local_token and not renew:
            return _local_token
        await ensure_default_admin()
        user = await db_store.get_user_by_username(settings.DEFAULT_ADMIN_USERNAME)
        session_id = _hash_token(create_refresh_token())
        await db_store.store_refresh_token(
            username=user.username,
            token_hash=session_id,
            expires_at=utc_now_naive() + _LOCAL_SESSION_TTL,
        )
        _local_token = create_access_token(user.username, user.role, session_id, _LOCAL_SESSION_TTL)
        return _local_token


# ── FastAPI dependencies ─────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    request: Request = None,
) -> UserInfo:
    """Extract and validate JWT from Authorization header.

    Returns UserInfo if valid. Returns anonymous viewer if no token.
    Use require_auth for endpoints that need authentication.
    """
    if request is not None and getattr(request.state, "auth_user", None):
        return request.state.auth_user
    if credentials is None and request is not None:
        token = request.cookies.get("jmeter_access")
        if token:
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    if credentials is None:
        return UserInfo(username="anonymous", role=Role.VIEWER)

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh tokens cannot be used for API access")

    session = await db_store.get_refresh_token(payload.get("sid", ""))
    if (not session or session["username"] != payload.get("sub") or not session["expires_at"]
            or datetime.fromisoformat(session["expires_at"]) <= utc_now_naive()):
        raise HTTPException(status_code=401, detail="Session expired or revoked")
    user = await db_store.get_user_by_username(payload.get("sub"))
    if not user or not user.is_active or user.role not in Role.all_roles():
        raise HTTPException(status_code=401, detail="User unavailable")
    return UserInfo(username=user.username, role=user.role, user_id=user.id)


async def require_auth(
    user: UserInfo = Depends(get_current_user),
) -> UserInfo:
    """Require authenticated user (any role). Raises 401 if anonymous."""
    if user.username == "anonymous":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


async def require_admin(
    user: UserInfo = Depends(require_auth),
) -> UserInfo:
    """Require admin role. Raises 403 otherwise."""
    if not Role.can(user.role, Role.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


async def require_operator(
    user: UserInfo = Depends(require_auth),
) -> UserInfo:
    """Require at least operator role. Raises 403 otherwise."""
    if not Role.can(user.role, Role.OPERATOR):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")
    return user


# ── Helpers ──────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    """Hash a token for storage (SHA-256 hex digest)."""
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


# ── Startup ──────────────────────────────────────────────────

async def ensure_default_admin():
    """Create default admin user if no users exist."""
    existing = await db_store.get_user_by_username(settings.DEFAULT_ADMIN_USERNAME)
    if existing:
        return
    await db_store.create_user(
        username=settings.DEFAULT_ADMIN_USERNAME,
        password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
        role=Role.ADMIN,
    )
    logger.info(f"Created default admin user: {settings.DEFAULT_ADMIN_USERNAME}")
