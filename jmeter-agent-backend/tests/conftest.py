"""Library fixtures represent trusted maintenance, never an authenticated request.

Historical unit tests call storage/tools directly without HTTP. Give those calls
an explicit capability instead of weakening production's missing-scope denial.
AuthenticationMiddleware always clears it, and ACL tests use bind_scope(...) to
exercise user permissions or bind_scope(None) to exercise missing authorization.
"""
import pytest


@pytest.fixture(autouse=True)
def trusted_library_calls():
    from app.services.project_scope import internal_scope
    with internal_scope():
        yield
