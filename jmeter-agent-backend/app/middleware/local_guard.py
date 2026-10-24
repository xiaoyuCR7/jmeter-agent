"""Local-mode request guards: trusted Host header and same-site Origin.

There is no login. These checks keep other websites open in the same browser
(cross-site requests, DNS rebinding) from driving the local API.
"""
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from app.core.config import settings


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


class OriginGuardMiddleware:
    """Reject state-changing requests and WebSocket handshakes from foreign origins."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            connection = HTTPConnection(scope)
            origin = connection.headers.get('origin')
            method = scope.get('method', 'GET')
            same_origin = str(connection.base_url).rstrip('/').replace('ws://', 'http://').replace('wss://', 'https://')
            check = method not in ('GET', 'HEAD', 'OPTIONS') or scope['type'] == 'websocket'                 or scope.get('path', '').startswith('/api/v1/mcp/')
            if check and origin and origin != same_origin and origin not in settings.CORS_ORIGINS:
                if scope['type'] == 'websocket':
                    return await send({'type': 'websocket.close', 'code': 1008})
                return await JSONResponse({'detail': 'Origin not allowed'}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)
