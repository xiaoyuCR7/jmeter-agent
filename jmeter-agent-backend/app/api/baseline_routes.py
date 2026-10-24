"""Performance baseline management endpoints."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends

from app.services.baseline_compare import snapshot_of
from app.services.db_store import db_store
from app.services.storage import store

router = APIRouter(tags=["baselines"])


@router.post("/baselines")
async def create_baseline(
    project_id: str,
    name: str,
    test_plan_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    description: str = "",
    thresholds: Optional[dict] = None,
    metrics_snapshot: Optional[dict] = None,
):
    """Create a new performance baseline for a project.

    The baseline captures expected performance metrics (P99, error rate, etc.)
    and can be used for automated regression detection.
    """
    if execution_id and not metrics_snapshot:
        execution = store.executions.get(execution_id)
        if execution is None or execution.project_id != project_id:
            raise HTTPException(status_code=404, detail="Execution not found in this project")
        if execution.summary is None or not execution.summary.total_requests:
            raise HTTPException(status_code=409, detail="Execution has no usable parsed report")
        # Keep the numbers, so the baseline survives deleting its execution.
        metrics_snapshot = snapshot_of(execution.summary)
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


@router.post("/executions/{execution_id}/promote-baseline")
async def promote_execution_to_baseline(execution_id: str):
    """One click on a report: make this run the active baseline of its test plan."""
    execution = store.executions.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    plan = store.test_plans.get(execution.test_plan_id)
    started = execution.start_time.strftime('%Y-%m-%d %H:%M') if execution.start_time else execution_id
    return await create_baseline(
        project_id=execution.project_id, name=f"{plan.name if plan else execution.test_plan_id} @ {started}",
        test_plan_id=execution.test_plan_id or None, execution_id=execution_id,
        description="Promoted from a test report")


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


@router.post("/baselines/{baseline_id}/activate")
async def activate_baseline(
    baseline_id: int,
):
    """Activate a baseline; other baselines in the same scope are deactivated."""
    baseline = await db_store.set_active_baseline(baseline_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="Baseline not found")
    return baseline


@router.delete("/baselines/{baseline_id}")
async def delete_baseline(
    baseline_id: int,
):
    """Delete a performance baseline."""
    deleted = await db_store.delete_baseline(baseline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Baseline not found")
    return {"message": "Baseline deleted"}
