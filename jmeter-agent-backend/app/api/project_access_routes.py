"""Explicit access previews and commits; no global user directory exposure."""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.services.project_access import access

router = APIRouter(tags=['project-access'])


class AccessChangeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    owner_id: Annotated[int, Field(strict=True, gt=0)]
    members: dict[int, Literal['viewer', 'operator']]


class AccessApplyRequest(AccessChangeRequest):
    expected_revision: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]+$')


@router.get('/projects/{project_id}/access')
async def get_project_access(project_id: str):
    snapshot = await access.snapshot()
    permission = snapshot.projects.get(project_id)
    if permission is None or not (permission.read or permission.manage):
        raise HTTPException(404, 'Project not found')
    result = {'project_id': project_id, 'mode': snapshot.project_modes[project_id],
              'permissions': {'read': permission.read, 'write': permission.write, 'manage': permission.manage}}
    if permission.manage:
        try:
            async with await access._session() as session:
                # Recheck management against current DB policy before exposing IDs.
                _, policy = await access._management(session, project_id)
                result.update(mode=policy['mode'], owner_id=policy['owner_id'], members=policy['members'])
        except SQLAlchemyError:
            raise HTTPException(503, 'Project authorization unavailable') from None
    return result


@router.post('/projects/{project_id}/access/preview')
async def preview_project_access(project_id: str, request: AccessChangeRequest):
    return await access.preview(project_id, request.owner_id, request.members)


@router.post('/projects/{project_id}/access/apply')
async def apply_project_access(project_id: str, request: AccessApplyRequest):
    from app.api.project_boundary import CommittedAccessResponse
    result = await access.apply_preview(project_id, request.owner_id, request.members, request.expected_revision)
    return CommittedAccessResponse(result)
