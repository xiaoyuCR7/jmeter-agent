"""Shared path/query resource gate, evaluated before business route side effects."""
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse
from app.services.project_scope import require_project, require_plan, require_execution


async def project_request_guard(request: Request):
    write = request.method not in ('GET', 'HEAD', 'OPTIONS')
    for values in (request.path_params, request.query_params):
        for key, check in (('project_id', require_project), ('plan_id', require_plan),
                           ('test_plan_id', require_plan), ('execution_id', require_execution),
                           ('baseline_execution_id', require_execution), ('target_execution_id', require_execution)):
            value = values.get(key)
            if value:
                check(value, write=write)


class AuthorizedReportFiles(StaticFiles):
    async def get_response(self, path, scope):
        import os
        from app.services.project_scope import require_path
        # Starlette has normalized dot segments by this boundary. Check the
        # actual canonical target too, including symlinks inside a report.
        require_path(os.path.join(self.directory, path))
        return await super().get_response(path, scope)


class CommittedAccessResponse(JSONResponse):
    """Send only mutation acknowledgement metadata under fresh post-commit access.

    Never wrap project content or a stream: the committing actor may have just
    removed their own rights. Normal response delivery still rechecks this new
    scope, so concurrent account deactivation or later revocation remains fatal.
    """
    def __init__(self, content: dict):
        super().__init__(content)  # No background task or streaming callback.

    async def __call__(self, scope, receive, send):
        from app.services.project_scope import bind_scope, load_user_scope
        with bind_scope(await load_user_scope()):
            return await super().__call__(scope, receive, send)
