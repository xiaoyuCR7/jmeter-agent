"""Authentication endpoints — login, refresh, logout, password change, user management."""

from typing import Optional, Annotated
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from pydantic import BaseModel, Field, AfterValidator
from app.core.config import settings

from app.services.auth import (
    login as auth_login,
    refresh_access_token,
    logout as auth_logout,
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    require_auth,
    require_admin,
    UserInfo,
    hash_password,
    validate_password_bytes,
    Role,
)
from app.services.db_store import db_store

router = APIRouter(tags=["auth"])


def _set_cookies(response: Response, request: Request, tokens: TokenResponse):
    common = dict(httponly=True, secure=request.url.scheme == 'https', samesite='strict')
    response.set_cookie('jmeter_access', tokens.access_token, path='/',
                        max_age=settings.JWT_EXPIRE_MINUTES * 60, **common)
    response.set_cookie('jmeter_refresh', tokens.refresh_token, path='/api/v1/auth',
                        max_age=settings.JWT_REFRESH_EXPIRE_DAYS * 86400, **common)
    response.headers['Cache-Control'] = 'no-store'


NewPassword = Annotated[str, Field(min_length=8, max_length=72), AfterValidator(validate_password_bytes)]


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: NewPassword


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: NewPassword
    role: str = Role.VIEWER


class ConfirmationDecision(BaseModel):
    approved: bool


@router.get('/auth/confirmations')
async def pending_confirmations(user: UserInfo = Depends(require_auth)):
    from app.services.confirmations import confirmations
    return await confirmations.list_pending(user)


@router.post('/auth/confirmations/{confirmation_id}')
async def decide_confirmation(confirmation_id: str, decision: ConfirmationDecision,
                              user: UserInfo = Depends(require_auth)):
    from app.services.confirmations import confirmations
    if not Role.can(user.role, Role.OPERATOR):
        raise HTTPException(403, 'Operator role required')
    try:
        await confirmations.decide(confirmation_id, user, decision.approved)
    except PermissionError as error:
        raise HTTPException(403, str(error))
    except KeyError:
        raise HTTPException(409, 'Confirmation expired or already consumed')
    return {'accepted': True}


@router.post("/auth/login", response_model=TokenResponse)
async def login_endpoint(req: LoginRequest, request: Request, response: Response):
    """Login with username/password. Returns access_token (short-lived) and refresh_token."""
    result = await auth_login(req.username, req.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    _set_cookies(response, request, result)
    return result


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_endpoint(request: Request, response: Response, req: Optional[RefreshRequest] = None):
    """Exchange a refresh_token for a new access_token + rotated refresh_token."""
    token = req.refresh_token if req else request.cookies.get('jmeter_refresh', '')
    result = await refresh_access_token(token)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    _set_cookies(response, request, result)
    return result


@router.post("/auth/logout")
async def logout_endpoint(request: Request, response: Response, req: Optional[RefreshRequest] = None):
    """Revoke a refresh token."""
    await auth_logout(req.refresh_token if req else request.cookies.get('jmeter_refresh', ''))
    response.delete_cookie('jmeter_access', path='/')
    response.delete_cookie('jmeter_refresh', path='/api/v1/auth')
    return {"message": "Logged out"}


@router.get("/auth/me", response_model=UserInfo)
async def get_me(user: UserInfo = Depends(require_auth)):
    """Get current authenticated user info."""
    return user


@router.post("/auth/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: UserInfo = Depends(require_auth),
):
    """Change password for the current user."""
    old_password, new_password = req.old_password, req.new_password
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    db_user = await db_store.get_user_by_username(user.username)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    from app.services.auth import verify_password
    if not verify_password(old_password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    from app.models.db import get_session_factory
    from sqlalchemy import select
    async with get_session_factory()() as s:
        from app.models.db import UserModel
        result = await s.execute(
            select(UserModel).where(UserModel.username == user.username)
        )
        u = result.scalar_one_or_none()
        if u:
            u.password_hash = hash_password(new_password)
            await s.commit()
    await db_store.revoke_user_sessions(user.username)
    return {"message": "Password changed successfully"}


# ── Admin: user management ───────────────────────────────────

@router.get("/auth/users")
async def list_users(user: UserInfo = Depends(require_admin)):
    """List all users (admin only)."""
    users = await db_store.list_users()
    return {"users": users}


@router.post("/auth/users")
async def create_user(
    req: CreateUserRequest,
    user: UserInfo = Depends(require_admin),
):
    """Create a new user (admin only)."""
    username, password, role = req.username, req.password, req.role
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if role not in Role.all_roles():
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}. Must be one of {Role.all_roles()}")

    existing = await db_store.get_user_by_username(username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    from app.services.auth import hash_password
    await db_store.create_user(
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    return {"message": f"User '{username}' created with role '{role}'"}


@router.put("/auth/users/{username}/role")
async def update_user_role(
    username: str,
    role: str,
    user: UserInfo = Depends(require_admin),
):
    """Change a user's role (admin only)."""
    if role not in Role.all_roles():
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")
    db_user = await db_store.get_user_by_username(username)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    from app.models.db import get_session_factory
    from sqlalchemy import select, update
    async with get_session_factory()() as s:
        from app.models.db import UserModel
        result = await s.execute(
            select(UserModel).where(UserModel.username == username)
        )
        u = result.scalar_one_or_none()
        if u:
            u.role = role
            await s.commit()
    return {"message": f"User '{username}' role updated to '{role}'"}


@router.delete("/auth/users/{username}")
async def delete_user(
    username: str,
    user: UserInfo = Depends(require_admin),
):
    """Deactivate a user (admin only). Cannot delete self."""
    if username == user.username:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    await db_store.deactivate_user(username)
    return {"message": f"User '{username}' deactivated"}
