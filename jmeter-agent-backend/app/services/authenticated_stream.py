"""Recheck stream sessions before sending each event, including after logout."""
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse

from app.services.auth import get_current_user
from app.services.security_context import current_access_token


class AuthenticatedStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # A failed ASGI send can leave the iterator suspended at yield.
            await self.body_iterator.aclose()

    def __init__(self, content, *args, **kwargs):
        token = current_access_token.get()

        async def authenticated():
            from app.services.project_scope import recheck_scope
            try:
                async for chunk in content:
                    try:
                        if token:
                            await get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token))
                        await recheck_scope()
                    except HTTPException:
                        break
                    yield chunk
            finally:
                if hasattr(content, 'aclose'):
                    await content.aclose()

        super().__init__(authenticated(), *args, **kwargs)
