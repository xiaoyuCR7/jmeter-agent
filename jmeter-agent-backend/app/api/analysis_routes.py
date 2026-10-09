"""Typed HTTP adapters for existing, local analysis services."""
from datetime import datetime, timedelta, timezone
import math

from fastapi import APIRouter, HTTPException, Query, Depends

from app.services.storage import store
from app.services.execution_lifecycle import validate_id
from app.services.insights_engine import cost_estimator, drift_detector, version_comparator
from app.services.ml_analysis import ml_detector

from app.api.project_boundary import project_request_guard
router = APIRouter(tags=['analysis'], dependencies=[Depends(project_request_guard)])


def _execution(execution_id):
    try:
        validate_id(execution_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(404, 'Execution not found')
    if not execution.summary or not execution.summary.total_requests:
        raise HTTPException(409, 'Execution has no usable parsed report')
    return execution


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


@router.post('/analysis/anomalies')
async def detect_anomalies(execution_id: str):
    execution = _execution(execution_id)
    if not execution.summary.timeline_segments:
        raise HTTPException(409, 'Execution has no timeline for anomaly analysis')
    return _json_safe(ml_detector.analyze_timeline(execution.summary.timeline_segments).model_dump())


@router.post('/analysis/estimate-cost')
async def estimate_cost(concurrent_users: int = Query(..., ge=1),
                        duration_seconds: int = Query(..., ge=1),
                        avg_response_time_ms: float = Query(200, gt=0, allow_inf_nan=False),
                        is_distributed: bool = False, remote_engines: int = Query(0, ge=0)):
    return cost_estimator.estimate(concurrent_users=concurrent_users, duration_seconds=duration_seconds,
        avg_response_time_ms=avg_response_time_ms, is_distributed=is_distributed,
        remote_engines=remote_engines).model_dump()


@router.post('/analysis/drift')
async def analyze_drift(project_id: str = 'default', days: int = Query(30, ge=1, le=3650),
                        execution_id: str = None):
    target = _execution(execution_id) if execution_id else None
    if target:
        project_id = target.project_id
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    history = []
    for execution in store.executions.values():
        if execution.project_id != project_id or execution.status != 'completed':
            continue
        if target and execution.test_plan_id != target.test_plan_id:
            continue
        if not execution.start_time or not execution.summary:
            continue
        # Legacy executions use datetime.now(): naive timestamps are server-local.
        date = execution.start_time.astimezone(timezone.utc)
        if date < cutoff:
            continue
        if not execution.summary.total_requests:
            continue
        history.append({'date': date.isoformat(),
            **{key: getattr(execution.summary, key) for key in
               ('p99_response_time', 'avg_response_time', 'error_rate', 'throughput')}})
    result = drift_detector.analyze(history).model_dump()
    if len(history) < 3:
        result['health'] = 'unknown'
        result['recommendation'] = 'At least three comparable executions are required'
    return _json_safe(result)


@router.post('/analysis/compare-versions')
async def compare_versions(baseline_execution_id: str, target_execution_id: str,
                           baseline_name: str = 'baseline', target_name: str = 'current'):
    baseline, target = _execution(baseline_execution_id), _execution(target_execution_id)
    if baseline.project_id != target.project_id:
        raise HTTPException(400, 'Executions must belong to the same project')
    return _json_safe(version_comparator.compare(baseline.summary.model_dump(), target.summary.model_dump(),
                                                 baseline_name, target_name).model_dump())


@router.post('/analysis/correlate')
async def correlate_metrics(execution_id: str, prometheus_url: str = None, promql: str = None):
    _execution(execution_id)
    # No approved outbound-URL policy exists for this adapter yet. An explicit
    # unsupported response is safer than an unrestricted server-side fetch.
    raise HTTPException(501, 'Remote metric correlation is not configured; use collected local metrics')
