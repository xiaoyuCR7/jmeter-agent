"""Performance baseline management endpoints."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends

from app.services.db_store import db_store
from app.services.auth import require_auth, UserInfo

from app.api.project_boundary import project_request_guard
router = APIRouter(tags=["baselines"], dependencies=[Depends(project_request_guard)])


@router.post("/baselines")
async def create_baseline(
    project_id: str,
    name: str,
    test_plan_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    description: str = "",
    thresholds: Optional[dict] = None,
    metrics_snapshot: Optional[dict] = None,
    user: UserInfo = Depends(require_auth),
):
    """Create a new performance baseline for a project.

    The baseline captures expected performance metrics (P99, error rate, etc.)
    and can be used for automated regression detection.
    """
    baseline = await db_store.create_baseline({
        "project_id": project_id,
        "test_plan_id": test_plan_id,
        "name": name,
        "description": description,
        "execution_id": execution_id,
        "thresholds": thresholds or {
            "p99_max": 2000, "error_rate_max": 0.01, "avg_rt_max": 500,
        },
        "metrics_snapshot": metrics_snapshot or {},
    })
    return baseline


@router.get("/baselines")
async def list_baselines(
    project_id: Optional[str] = Query(None, description="Filter by project"),
):
    """List all performance baselines, optionally filtered by project."""
    return await db_store.list_baselines(project_id=project_id)


@router.get("/baselines/active")
async def get_active_baseline(
    project_id: str = Query(..., description="Project ID"),
    test_plan_id: Optional[str] = Query(None, description="Test plan ID"),
):
    """Get the currently active baseline for a project/test plan."""
    baseline = await db_store.get_active_baseline(project_id, test_plan_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="No active baseline found")
    return baseline


@router.delete("/baselines/{baseline_id}")
async def delete_baseline(
    baseline_id: int,
    user: UserInfo = Depends(require_auth),
):
    """Delete a performance baseline."""
    deleted = await db_store.delete_baseline(baseline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Baseline not found")
    return {"message": "Baseline deleted"}
