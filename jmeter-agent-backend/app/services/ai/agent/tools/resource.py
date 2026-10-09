"""Agent tools for resource monitoring, scheduling, SLA evaluation, and regression detection."""

import os
import asyncio
import math
from app.core.config import settings, safe_path
from app.services import project_scope
from app.services.jmeter.resource_monitor import (
    ResourceMonitor,
    get_resource_monitor,
)
from app.services.jmeter.test_scheduler import (
    TestScheduler,
    SchedulerDecision,
)
from app.services.ai.agent.tool_registry import Tool


_monitor = get_resource_monitor()
_scheduler = TestScheduler()


async def check_system_resources() -> dict:
    """Check current CPU and memory usage of the test machine.

    Use this BEFORE executing any performance test to ensure the
    host machine is not already under high load, which would skew
    test results.

    Returns current CPU%, memory%, disk%, and a health assessment.
    """
    snap = await asyncio.to_thread(_monitor.snapshot)
    diagnosis = _monitor.diagnose(snap)
    return {
        **snap.to_dict(),
        "diagnosis": diagnosis,
        "thresholds": {
            "cpu_percent": settings.RESOURCE_CPU_THRESHOLD,
            "memory_percent": settings.RESOURCE_MEMORY_THRESHOLD,
        },
        "recommendation": (
            "System healthy — safe to run tests."
            if snap.is_healthy
            else f"System under load — consider waiting before starting tests. {diagnosis}"
        ),
    }


async def get_scheduler_status() -> dict:
    """Get full scheduler status including readiness decision.

    Use this to decide whether to proceed with test execution,
    wait, or defer. The scheduler evaluates CPU, memory, and
    cooldown between tests.

    Returns a readiness decision (proceed/wait/warning/overloaded)
    and supporting data.
    """
    return await asyncio.to_thread(_scheduler.get_status_summary)


async def wait_for_resources(timeout_seconds: float = 120.0) -> dict:
    """Wait until system resources are below safe thresholds.

    Use this when check_system_resources or get_scheduler_status
    indicates the system is not ready. Blocks until resources free
    up or timeout is reached.

    Args:
        timeout_seconds: Maximum seconds to wait (default 120).
    """
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 600:
        raise ValueError('timeout_seconds must be within (0, 600]')
    became_safe = await _monitor.wait_until_safe(timeout=timeout_seconds)
    snap = await asyncio.to_thread(_monitor.snapshot)
    return {
        "resources_safe": became_safe,
        "current_cpu_percent": round(snap.cpu_percent, 1),
        "current_memory_percent": round(snap.memory_percent, 1),
        "message": (
            "Resources now within safe thresholds."
            if became_safe
            else f"Timed out after {timeout_seconds}s — resources still high."
        ),
    }


async def start_resource_monitoring(interval_seconds: float = 5.0) -> dict:
    """Start continuous background resource monitoring.

    Use this before running a batch of tests to track resource
    usage throughout the test session. Call stop_resource_monitoring
    when done.

    Args:
        interval_seconds: How often to sample resources (default 5s).
    """
    if not math.isfinite(interval_seconds) or not 1 <= interval_seconds <= 3600:
        raise ValueError('interval_seconds must be within [1, 3600]')
    await _monitor.start_monitoring(interval=interval_seconds)
    return {"status": "monitoring_started", "interval_seconds": interval_seconds}


async def stop_resource_monitoring() -> dict:
    """Stop background resource monitoring and return summary."""
    await _monitor.stop_monitoring()
    snap = _monitor.latest
    history = _monitor.get_history(60)
    return {
        "status": "monitoring_stopped",
        "final_snapshot": snap.to_dict() if snap else None,
        "samples_collected": len(history),
        "peak_cpu": max((s.cpu_percent for s in history), default=0),
        "peak_memory": max((s.memory_percent for s in history), default=0),
        "avg_cpu": round(sum(s.cpu_percent for s in history) / len(history), 1) if history else 0,
        "avg_memory": round(sum(s.memory_percent for s in history) / len(history), 1) if history else 0,
    }


async def get_resource_history(samples: int = 20) -> dict:
    """Get recent resource usage history for trend analysis.

    Args:
        samples: Number of recent samples to return (default 20).
    """
    history = _monitor.get_history(samples)
    if not history:
        return {"message": "No monitoring data yet. Start monitoring first.", "samples": []}
    return {
        "samples": [s.to_dict() for s in history],
        "trend": {
            "cpu_trend": "rising" if len(history) >= 3 and history[-1].cpu_percent > history[0].cpu_percent + 5
            else "falling" if len(history) >= 3 and history[-1].cpu_percent < history[0].cpu_percent - 5
            else "stable",
            "memory_trend": "rising" if len(history) >= 3 and history[-1].memory_percent > history[0].memory_percent + 3
            else "falling" if len(history) >= 3 and history[-1].memory_percent < history[0].memory_percent - 3
            else "stable",
        },
    }


# ── SLA and Regression ──────────────────────────────────────────

async def evaluate_sla(jtl_path: str, sla_preset: str = "standard") -> dict:
    """Evaluate a test report against SLA thresholds.

    Args:
        jtl_path: Path to the JTL result file.
        sla_preset: Preset SLA config: 'standard', 'strict', or 'custom'.
    """
    from app.services.jmeter.report_parser import ReportParser
    from app.services.jmeter.sla_engine import SLAEngine, SLAConfig

    parser = ReportParser()
    engine = SLAEngine()

    try:
        summary = await asyncio.to_thread(parser.parse_jtl, safe_path(jtl_path, [settings.REPORTS_DIR]))
    except Exception as e:
        return {"error": f"Failed to parse JTL: {str(e)}"}

    if sla_preset == "strict":
        config = SLAConfig.strict_api_sla()
    else:
        config = SLAConfig.standard_api_sla()

    result = engine.evaluate(summary, config)
    return engine.to_dict(result)


async def compare_runs(
    baseline_jtl: str, target_jtl: str,
    baseline_name: str = "baseline", target_name: str = "current",
    regression_threshold_pct: float = 20.0,
) -> dict:
    """Compare two test runs and detect performance regressions.

    Args:
        baseline_jtl: Path to the baseline JTL result file.
        target_jtl: Path to the current JTL result file.
        baseline_name: Label for the baseline run.
        target_name: Label for the current run.
        regression_threshold_pct: Percentage change to flag as regression (default 20%).
    """
    from app.services.jmeter.report_parser import ReportParser
    from app.services.jmeter.regression_engine import RegressionEngine

    parser = ReportParser()
    engine = RegressionEngine(regression_threshold_pct=regression_threshold_pct)

    try:
        baseline_jtl = safe_path(baseline_jtl, [settings.REPORTS_DIR])
        target_jtl = safe_path(target_jtl, [settings.REPORTS_DIR])
        baseline = await asyncio.to_thread(parser.parse_jtl, baseline_jtl)
        target = await asyncio.to_thread(parser.parse_jtl, target_jtl)
    except Exception as e:
        return {"error": f"Failed to parse JTL files: {str(e)}"}

    report = engine.compare(baseline, target, baseline_name, target_name)
    return report.to_dict()



async def import_openapi_spec(url: str, execute: bool = False) -> dict:
    """Import an OpenAPI/Swagger specification and auto-generate project + test plans.

    Args:
        url: URL to the OpenAPI JSON/YAML spec.
        execute: Whether to immediately run tests after import.
    """
    from app.services.openapi_importer import OpenAPIImporter
    importer = OpenAPIImporter()
    result = await importer.import_from_url(url, execute_tests=execute)
    return result.model_dump()


# ── SelfHeal ────────────────────────────────────────────────

async def selfheal_test(jmx_path: str, failed_execution_id: str,
                        auto_re_run: bool = True) -> dict:
    """Auto-detect and fix broken extraction rules when APIs change.

    Use when tests fail with 401/403 errors — the agent will:
    1. Analyze the failed test to find broken extractions
    2. Try to find where tokens/keys moved in the response
    3. Update the test plan with corrected extraction rules
    4. Optionally re-run the test

    Args:
        jmx_path: Path to the failing test plan.
        failed_execution_id: ID of the failed execution.
        auto_re_run: Whether to re-run after fixing.
    """
    from app.services.execution_lifecycle import validate_id
    validate_id(failed_execution_id)
    jmx_path = safe_path(jmx_path, [settings.TEST_PLANS_DIR])
    project_scope.require_path(jmx_path, write=True)
    project_scope.require_execution(failed_execution_id)
    for filename in ('result.jtl', 'jmeter.log'):
        safe_path(os.path.join(settings.REPORTS_DIR, failed_execution_id, filename), [settings.REPORTS_DIR])
    from app.services.ai.agents.selfheal_agent import selfheal
    result = await selfheal.diagnose_and_fix(
        jmx_path=jmx_path,
        failed_execution_id=failed_execution_id,
        auto_re_run=auto_re_run,
    )
    return result.model_dump()


# ── Anomaly Detection ──────────────────────────────────────

async def detect_anomalies(execution_id: str) -> dict:
    """Run ML anomaly detection on a test execution.

    Uses statistical methods (Z-score, IQR) to find performance spikes,
    throughput drops, and response time degradation patterns.

    Args:
        execution_id: The execution ID to analyze.
    """
    import os
    from app.core.config import settings
    from app.services.execution_lifecycle import validate_id
    validate_id(execution_id)
    project_scope.require_execution(execution_id)
    jtl_path = safe_path(os.path.join(settings.REPORTS_DIR, execution_id, "result.jtl"), [settings.REPORTS_DIR])
    if not os.path.exists(jtl_path):
        return {"error": f"JTL not found for execution {execution_id}"}
    from app.services.jmeter.report_parser import ReportParser
    parser = ReportParser()
    summary = await asyncio.to_thread(parser.parse_jtl, jtl_path)
    from app.services.ml_analysis import ml_detector
    report = ml_detector.analyze_timeline(summary.timeline_segments)
    return report.model_dump()


# ── Cost Estimation ───────────────────────────────────────

async def estimate_test_cost(concurrent_users: int, duration_seconds: int,
                             avg_response_time_ms: float = 200,
                             is_distributed: bool = False,
                             remote_engines: int = 0) -> dict:
    """Estimate resource usage and cost before running a test.

    Args:
        concurrent_users: Number of virtual users.
        duration_seconds: Test duration.
        avg_response_time_ms: Expected average response time.
        is_distributed: Whether this is a distributed test.
        remote_engines: Number of remote engines.
    """
    from app.services.insights_engine import cost_estimator
    est = cost_estimator.estimate(
        concurrent_users=concurrent_users,
        duration_seconds=duration_seconds,
        avg_response_time_ms=avg_response_time_ms,
        is_distributed=is_distributed,
        remote_engines=remote_engines,
    )
    return est.model_dump()


# ── Drift Detection ───────────────────────────────────────

async def detect_performance_drift(project_id: str = "default", days: int = 30) -> dict:
    """Detect performance drift over time — finds gradual degradation.

    Args:
        project_id: Project to analyze.
        days: Number of days to look back.
    """
    from datetime import datetime, timedelta, timezone
    from app.services.storage import store
    from app.services.insights_engine import drift_detector as dd

    if not 1 <= days <= 3650:
        raise ValueError('days must be within [1, 3650]')
    project_scope.require_project(project_id)
    history = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for execution in list(store.executions.values()):
        if execution.project_id != project_id or execution.status != 'completed' or not execution.start_time:
            continue
        # Legacy executions use datetime.now(): naive timestamps are server-local.
        date = execution.start_time.astimezone(timezone.utc)
        summary = execution.summary
        if date < cutoff or not summary or not summary.total_requests:
            continue
        history.append({'date': date.isoformat(), **{key: getattr(summary, key) for key in
            ('p99_response_time', 'avg_response_time', 'error_rate', 'throughput')}})
    report = dd.analyze(history)
    return report.model_dump()


# ── Cross-Version Comparison ──────────────────────────────

async def compare_versions(baseline_execution_id: str, target_execution_id: str,
                           baseline_name: str = "baseline",
                           target_name: str = "current") -> dict:
    """Deep comparison between two test runs with root cause analysis.

    Args:
        baseline_execution_id: ID of the baseline execution.
        target_execution_id: ID of the current execution.
        baseline_name: Label for baseline.
        target_name: Label for current.
    """
    import os
    from app.core.config import settings
    from app.services.jmeter.report_parser import ReportParser
    from app.services.insights_engine import version_comparator as vc

    parser = ReportParser()
    from app.services.execution_lifecycle import validate_id
    validate_id(baseline_execution_id)
    validate_id(target_execution_id)
    project_scope.require_execution(baseline_execution_id)
    project_scope.require_execution(target_execution_id)
    b_jtl = safe_path(os.path.join(settings.REPORTS_DIR, baseline_execution_id, "result.jtl"), [settings.REPORTS_DIR])
    t_jtl = safe_path(os.path.join(settings.REPORTS_DIR, target_execution_id, "result.jtl"), [settings.REPORTS_DIR])
    if not os.path.exists(b_jtl):
        return {"error": f"Baseline JTL not found: {baseline_execution_id}"}
    if not os.path.exists(t_jtl):
        return {"error": f"Target JTL not found: {target_execution_id}"}
    baseline = (await asyncio.to_thread(parser.parse_jtl, b_jtl)).model_dump()
    target = (await asyncio.to_thread(parser.parse_jtl, t_jtl)).model_dump()
    report = vc.compare(baseline, target, baseline_name, target_name)
    return report.model_dump()


async def parse_dashboard_data(report_dir: str) -> dict:
    """Parse JMeter HTML dashboard statistics.json for chart data."""
    from app.services.jmeter.report_parser import ReportParser
    parser = ReportParser()
    report_dir = safe_path(report_dir, [settings.REPORTS_DIR])
    # The parser probes these alternatives; authorize every candidate before I/O.
    for relative in (('html', 'statistics.json'), ('statistics.json',), ('html', 'content', 'js', 'dashboard.js')):
        safe_path(os.path.join(report_dir, *relative), [settings.REPORTS_DIR])
    return await asyncio.to_thread(parser.parse_dashboard_json, report_dir)


resource_tools = [
    Tool(
        name="check_system_resources",
        description=(
            "Check current CPU, memory, and disk usage of the test machine. "
            "MUST call this BEFORE executing any performance test to ensure "
            "the host is not under load. Returns health assessment and recommendation."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=check_system_resources,
    ),
    Tool(
        name="get_scheduler_status",
        description=(
            "Get the full test scheduler status including readiness decision. "
            "Evaluates CPU, memory, and cooldown between tests. Returns a decision: "
            "proceed (safe), wait (need to wait), warning (borderline), overloaded (do not run). "
            "Use this to decide whether to run the next test now or wait."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=get_scheduler_status,
    ),
    Tool(
        name="wait_for_resources",
        description=(
            "Wait until system CPU and memory are below safe thresholds. "
            "Use when the scheduler says 'wait' — this blocks until resources "
            "free up or timeout is reached."
        ),
        parameters={
            "type": "object",
            "properties": {
                "timeout_seconds": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default 120)",
                },
            },
        },
        handler=wait_for_resources,
    ),
    Tool(
        name="start_resource_monitoring",
        description=(
            "Start continuous background monitoring of CPU and memory. "
            "Use before running a batch of tests to track usage throughout."
        ),
        parameters={
            "type": "object",
            "properties": {
                "interval_seconds": {
                    "type": "number",
                    "description": "Sampling interval in seconds (default 5)",
                },
            },
        },
        handler=start_resource_monitoring,
    ),
    Tool(
        name="stop_resource_monitoring",
        description=(
            "Stop background resource monitoring and return a summary "
            "with peak and average CPU/memory usage during the session."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=stop_resource_monitoring,
    ),
    Tool(
        name="get_resource_history",
        description=(
            "Get recent resource usage history for trend analysis. "
            "Use to determine if CPU/memory are trending up or down "
            "before deciding to run a test."
        ),
        parameters={
            "type": "object",
            "properties": {
                "samples": {
                    "type": "integer",
                    "description": "Number of recent samples (default 20)",
                },
            },
        },
        handler=get_resource_history,
    ),
    Tool(
        name="evaluate_sla",
        description="Evaluate a test report (JTL) against predefined SLA thresholds. Returns pass/fail/warn verdict with detailed violation list and per-label breakdown.",
        parameters={
            "type": "object",
            "properties": {
                "jtl_path": {"type": "string", "description": "Path to the .jtl result file"},
                "sla_preset": {"type": "string", "enum": ["standard", "strict"], "description": "SLA preset: standard (P99<2s, err<1%) or strict (P99<1s, err<0.5%)"},
            },
            "required": ["jtl_path"],
        },
        handler=evaluate_sla,
    ),
    Tool(
        name="compare_runs",
        description="Compare two test runs (baseline vs current) and detect performance regressions. Returns per-metric deltas, regression flags, and overall verdict.",
        parameters={
            "type": "object",
            "properties": {
                "baseline_jtl": {"type": "string", "description": "Path to the baseline JTL file"},
                "target_jtl": {"type": "string", "description": "Path to the current JTL file to compare"},
                "baseline_name": {"type": "string", "description": "Label for baseline (e.g. 'v1.0')"},
                "target_name": {"type": "string", "description": "Label for current (e.g. 'v1.1')"},
                "regression_threshold_pct": {"type": "number", "description": "Pct change to flag as regression (default 20)"},
            },
            "required": ["baseline_jtl", "target_jtl"],
        },
        handler=compare_runs,
    ),
# ── OpenAPI Import ─────────────────────────────────────────

    Tool(
        name="import_openapi_spec",
        description="Import supported OpenAPI/Swagger HTTP operations from a URL and create plans. Supply real authentication tokens separately; unsupported correlation is not inferred. Optional execution requires confirmation.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to the OpenAPI JSON/YAML spec"},
                "execute": {"type": "boolean", "description": "Whether to immediately run tests after import"},
            },
            "required": ["url"],
        },
        handler=import_openapi_spec,
        require_confirmation=True,
    ),
    Tool(
        name="selfheal_test",
        description="Auto-detect and fix broken extraction rules when APIs change. Analyzes 401/403 errors and finds where tokens moved in the response.",
        parameters={
            "type": "object",
            "properties": {
                "jmx_path": {"type": "string", "description": "Path to the failing test plan"},
                "failed_execution_id": {"type": "string", "description": "ID of the failed execution"},
                "auto_re_run": {"type": "boolean", "description": "Re-run test after fixing (default true)"},
            },
            "required": ["jmx_path", "failed_execution_id"],
        },
        handler=selfheal_test,
        require_confirmation=True,
    ),
    Tool(
        name="detect_anomalies",
        description="Run statistical anomaly detection on a test execution. Finds performance spikes, throughput drops, and degradation patterns using Z-score and IQR methods.",
        parameters={
            "type": "object",
            "properties": {
                "execution_id": {"type": "string", "description": "The execution ID to analyze"},
            },
            "required": ["execution_id"],
        },
        handler=detect_anomalies,
    ),
    Tool(
        name="estimate_test_cost",
        description="Estimate resource usage (requests, bandwidth, cloud cost, JVM heap) before running a test.",
        parameters={
            "type": "object",
            "properties": {
                "concurrent_users": {"type": "integer", "description": "Number of virtual users"},
                "duration_seconds": {"type": "integer", "description": "Test duration in seconds"},
                "avg_response_time_ms": {"type": "number", "description": "Expected average response time (default 200)"},
                "is_distributed": {"type": "boolean", "description": "Is this a distributed test?"},
                "remote_engines": {"type": "integer", "description": "Number of remote engines"},
            },
            "required": ["concurrent_users", "duration_seconds"],
        },
        handler=estimate_test_cost,
    ),
    Tool(
        name="detect_performance_drift",
        description="Detect gradual performance degradation over time using linear regression. Analyzes 30-day trend and projects future values.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID (default: 'default')"},
                "days": {"type": "integer", "description": "Days to look back (default 30)"},
            },
        },
        handler=detect_performance_drift,
    ),
    Tool(
        name="compare_versions",
        description="Deep comparison between two test runs with root cause analysis hints. Identifies specific metrics that degraded and suggests possible causes.",
        parameters={
            "type": "object",
            "properties": {
                "baseline_execution_id": {"type": "string", "description": "ID of the baseline execution"},
                "target_execution_id": {"type": "string", "description": "ID of the current execution"},
                "baseline_name": {"type": "string", "description": "Label for baseline"},
                "target_name": {"type": "string", "description": "Label for current"},
            },
            "required": ["baseline_execution_id", "target_execution_id"],
        },
        handler=compare_versions,
    ),
    Tool(
        name="parse_dashboard_data",
        description="Parse JMeter HTML dashboard statistics.json to extract chart data. Returns per-transaction metrics with AI insights.",
        parameters={
            "type": "object",
            "properties": {
                "report_dir": {"type": "string", "description": "Report directory path, e.g. reports/<execution_id>"},
            },
            "required": ["report_dir"],
        },
        handler=parse_dashboard_data,
    ),
]
