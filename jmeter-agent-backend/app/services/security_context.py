"""Trusted request identity, never populated from model arguments or prompts."""
from contextvars import ContextVar
from typing import Optional
from app.services.auth import UserInfo

current_principal: ContextVar[Optional[UserInfo]] = ContextVar('current_principal', default=None)
current_access_token: ContextVar[str] = ContextVar('current_access_token', default='')
