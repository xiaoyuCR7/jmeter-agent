"""Shared HTTP, report-file and WebSocket authentication boundary."""
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from app.core.config import settings
from app.services.auth import Role, get_current_user
from app.services.security_context import current_principal, current_access_token
from urllib.parse import urlsplit


class TrustedHostMiddleware:
    """Explicit hosts, including IPv6; WS denial uses a WS frame, not HTTP ASGI events."""
    def __init__(self, app, allowed_hosts):
        if not allowed_hosts or any('*' in host for host in allowed_hosts):
            raise ValueError('ALLOWED_HOSTS must list explicit trusted hostnames')
        self.app = app
        self.allowed_hosts = {host.lower() for host in allowed_hosts}

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            raw_hosts = [v.decode('latin-1') for k, v in scope.get('headers', []) if k.lower() == b'host']
            try:
                parsed = urlsplit('//' + raw_hosts[0]) if len(raw_hosts) == 1 else None
                valid = parsed is not None and parsed.hostname in self.allowed_hosts and not (
                    parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment)
                if parsed is not None:
                    parsed.port  # Reject malformed ports as well.
            except ValueError:
                valid = False
            if not valid:
                if scope['type'] == 'websocket':
                    return await send({'type': 'websocket.close', 'code': 1008})
                return await JSONResponse({'detail': 'Invalid host header'}, status_code=400)(scope, receive, send)
        return await self.app(scope, receive, send)


class AuthenticationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        path = scope.get('path', '')
        method = scope.get('method', 'GET')
        protected = (path.startswith('/api/v1/') and not path.startswith('/api/v1/auth/')) or path.startswith(('/reports/', '/ws/'))
        origin = connection.headers.get('origin')
        same_origin = str(connection.base_url).rstrip('/').replace('ws://', 'http://').replace('wss://', 'https://')
        check_origin = method not in ('GET', 'HEAD', 'OPTIONS') or scope['type'] == 'websocket' or path.startswith('/api/v1/mcp/')
        try:
            if check_origin and origin and origin != same_origin and origin not in settings.CORS_ORIGINS:
                raise HTTPException(403, 'Origin not allowed')
            if protected and method != 'OPTIONS':
                authorization = connection.headers.get('authorization', '')
                if authorization:
                    scheme, _, token = authorization.partition(' ')
                    if scheme.lower() != 'bearer' or not token:
                        raise HTTPException(401, 'Bearer authentication required')
                else:
                    token = connection.cookies.get('jmeter_access', '')
                if not token:
                    raise HTTPException(401, 'Authentication required')
                user = await get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token))
                required = Role.VIEWER if method in ('GET', 'HEAD', 'OPTIONS') else Role.OPERATOR
                if path.startswith(('/api/v1/system/paths', '/api/v1/ai/model-configs', '/api/v1/ai/models/user')):
                    required = Role.ADMIN
                if not Role.can(user.role, required):
                    raise HTTPException(403, f'{required} role required')
                scope.setdefault('state', {})['auth_user'] = user
                scope['state']['auth_token'] = token
        except HTTPException as error:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': error.detail}, status_code=error.status_code)(scope, receive, send)
        context_token = current_principal.set(scope.get('state', {}).get('auth_user'))
        access_token = current_access_token.set(scope.get('state', {}).get('auth_token', ''))
        try:
            from app.services.project_scope import bind_scope, load_user_scope, require_execution
            with bind_scope(None):
                try:
                    authorization = await load_user_scope() if protected and method != 'OPTIONS' else None
                    with bind_scope(authorization):
                        if authorization is not None and path.startswith(('/reports/', '/ws/executions/')):
                            prefix = '/reports/' if path.startswith('/reports/') else '/ws/executions/'
                            relative = path[len(prefix):]
                            if '\\' in relative or any(part in ('.', '..') for part in relative.split('/')):
                                raise HTTPException(404, 'Project resource not found')
                            require_execution(path[len(prefix):].split('/')[0])
                except HTTPException as error:
                    if scope['type'] == 'websocket':
                        return await send({'type': 'websocket.close', 'code': 1008})
                    return await JSONResponse({'detail': error.detail}, status_code=error.status_code)(scope, receive, send)
                with bind_scope(authorization):
                    started = denied = False
                    async def protected_send(message):
                        nonlocal started, denied
                        if denied:
                            return
                        if authorization is not None and scope['type'] == 'http':
                            from app.services.project_scope import recheck_scope
                            try:
                                await recheck_scope()
                            except HTTPException as error:
                                denied = True
                                if started:
                                    return await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
                                return await JSONResponse({'detail': error.detail}, status_code=error.status_code)(scope, receive, send)
                        started = started or message['type'] == 'http.response.start'
                        await send(message)
                    return await self.app(scope, receive, protected_send)
        finally:
            current_principal.reset(context_token)
            current_access_token.reset(access_token)
