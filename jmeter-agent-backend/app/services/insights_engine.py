"""Insights Engine — cost estimation, drift detection, cross-version comparison.

Three advanced analytical capabilities:
1. CostEstimation — predict resource usage before execution
2. DriftDetection — 30-day trend analysis with early warning
3. CrossVersionComparison — deep diff between any two versions with root cause hints
"""

import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Cost Estimation
# ═══════════════════════════════════════════════════════════

@dataclass
class CostEstimate:
    """Pre-execution cost/resource estimation."""
    estimated_requests: int
    estimated_data_transfer_gb: float
    estimated_duration_minutes: float
    estimated_cloud_cost_usd: float  # rough estimate
    jvm_heap_recommendation: str
    network_bandwidth_mbps: float
    warnings: list[str] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {
            "estimated_requests": self.estimated_requests,
            "estimated_data_transfer_gb": round(self.estimated_data_transfer_gb, 3),
            "estimated_duration_minutes": round(self.estimated_duration_minutes, 1),
            "estimated_cloud_cost_usd": round(self.estimated_cloud_cost_usd, 2),
            "jvm_heap_recommendation": self.jvm_heap_recommendation,
            "network_bandwidth_mbps": round(self.network_bandwidth_mbps, 1),
            "warnings": self.warnings,
        }


class CostEstimator:
    """Estimate resource usage and cost before running a test.

    Usage:
        estimator = CostEstimator()
        est = estimator.estimate(concurrent_users=500, duration_seconds=600, avg_response_time_ms=200)
    """

    # Constants for estimation
    AVG_REQUEST_SIZE_BYTES = 2048   # ~2KB average request
    AVG_RESPONSE_SIZE_BYTES = 8192  # ~8KB average response
    CLOUD_COST_PER_GB = 0.09        # ~$0.09/GB (AWS/GCP egress pricing)
    CLOUD_COMPUTE_PER_HOUR = 0.05   # ~$0.05/hour for compute

    def estimate(
        self,
        concurrent_users: int,
        duration_seconds: int,
        avg_response_time_ms: float = 200,
        request_size_bytes: int = 0,
        response_size_bytes: int = 0,
        is_distributed: bool = False,
        remote_engines: int = 0,
    ) -> CostEstimate:
        """Estimate resource consumption for a planned test.

        Args:
            concurrent_users: Number of concurrent virtual users.
            duration_seconds: Test duration in seconds.
            avg_response_time_ms: Expected average response time.
            request_size_bytes: Average request payload size.
            response_size_bytes: Average response payload size.
            is_distributed: Whether this is a distributed test.
            remote_engines: Number of remote engines.
        """
        for name, value, minimum in (
            ("concurrent_users", concurrent_users, 1), ("duration_seconds", duration_seconds, 1),
            ("avg_response_time_ms", avg_response_time_ms, 0),
            ("request_size_bytes", request_size_bytes, 0), ("response_size_bytes", response_size_bytes, 0),
            ("remote_engines", remote_engines, 0),
        ):
            if not math.isfinite(value) or value < minimum or (name == "avg_response_time_ms" and value == 0):
                raise ValueError(f"Invalid {name}")
        req_size = request_size_bytes or self.AVG_REQUEST_SIZE_BYTES
        resp_size = response_size_bytes or self.AVG_RESPONSE_SIZE_BYTES

        # Estimate throughput: concurrent_users / avg_response_time
        avg_rt_seconds = avg_response_time_ms / 1000
        if avg_rt_seconds <= 0:
            avg_rt_seconds = 0.2

        estimated_rps = concurrent_users / avg_rt_seconds
        estimated_requests = int(estimated_rps * duration_seconds)

        # Data transfer
        total_bytes = estimated_requests * (req_size + resp_size)
        total_gb = total_bytes / (1024 ** 3)

        # Duration
        duration_minutes = duration_seconds / 60

        # Cloud cost
        compute_hours = duration_seconds / 3600
        cloud_cost = total_gb * self.CLOUD_COST_PER_GB + compute_hours * self.CLOUD_COMPUTE_PER_HOUR
        if is_distributed:
            cloud_cost *= (1 + remote_engines * 0.5)  # Engines add ~50% overhead each

        # JVM heap recommendation
        heap_size_mb = max(256, int(concurrent_users * 0.5 + 128))
        heap_recommendation = f"-Xms{heap_size_mb}m -Xmx{max(heap_size_mb, 512)}m"

        # Network bandwidth
        bandwidth_mbps = (estimated_rps * (req_size + resp_size) * 8) / 1_000_000

        # Warnings
        warnings = []
        if estimated_rps > 1000:
            warnings.append(f"High throughput ({estimated_rps:.0f} RPS) — consider distributed testing")
        if heap_size_mb > 2048:
            warnings.append(f"High heap requirement ({heap_size_mb}MB) — increase -Xmx")
        if bandwidth_mbps > 100:
            warnings.append(f"Network bandwidth may be saturated ({bandwidth_mbps:.0f} Mbps)")
        if total_gb > 10:
            warnings.append(f"Large data transfer ({total_gb:.1f} GB) — check disk space")

        return CostEstimate(
            estimated_requests=estimated_requests,
            estimated_data_transfer_gb=total_gb,
            estimated_duration_minutes=duration_minutes,
            estimated_cloud_cost_usd=cloud_cost,
            jvm_heap_recommendation=heap_recommendation,
            network_bandwidth_mbps=bandwidth_mbps,
            warnings=warnings,
        )


# ═══════════════════════════════════════════════════════════
# Drift Detection
# ═══════════════════════════════════════════════════════════

@dataclass
class DriftPoint:
    date: str
    p99: float
    avg_response_time: float
    error_rate: float
    throughput: float


@dataclass
class DriftReport:
    total_days: int
    data_points: int
    metrics_trending: list[str]  # metrics that are trending worse
    slope_per_day: dict[str, float]  # metric → per-day change
    projected_30day: dict[str, float]  # metric → projected value in 30 days
    warnings: list[str]
    health: str  # "stable" | "drifting" | "degrading"
    recommendation: str

    def model_dump(self) -> dict:
        return {
            "total_days": self.total_days,
            "data_points": self.data_points,
            "metrics_trending": self.metrics_trending,
            "slope_per_day": {k: round(v, 4) for k, v in self.slope_per_day.items()},
            "projected_30day": {k: round(v, 2) for k, v in self.projected_30day.items()},
            "warnings": self.warnings,
            "health": self.health,
            "recommendation": self.recommendation,
        }


class DriftDetector:
    """Detect gradual performance degradation over time."""

    def analyze(
        self, history: list[dict], metric_keys: list[str] = None,
    ) -> DriftReport:
        """Analyze a time series of test metrics for drift.

        Args:
            history: List of {date, p99_response_time, avg_response_time, error_rate, throughput}.
            metric_keys: Metrics to track (default: all).
        """
        if not history:
            return DriftReport(
                total_days=0, data_points=0,
                metrics_trending=[], slope_per_day={}, projected_30day={},
                warnings=["No historical data available"],
                health="unknown",
                recommendation="Run more tests to establish a baseline trend",
            )

        if metric_keys is None:
            metric_keys = ["p99_response_time", "avg_response_time", "error_rate", "throughput"]

        # Normalize timezone and use elapsed days; test runs need not be daily.
        dated = []
        for point in history:
            try:
                date = datetime.fromisoformat(point.get("date", "").replace("Z", "+00:00"))
                date = date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
                dated.append((date, point))
            except (ValueError, TypeError, AttributeError):
                continue
        dated.sort(key=lambda item: item[0])
        total_days = (dated[-1][0] - dated[0][0]).days if dated else 0

        slopes = {}
        projected = {}
        trending = []
        warnings = []
        degrading = False
        numerical_failure = False

        for metric in metric_keys:
            values = []
            for date, point in dated:
                try:
                    value = point[metric]
                    if isinstance(value, bool):
                        continue
                    value = float(value)
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(value) and value >= 0:
                    values.append(((date - dated[0][0]).total_seconds() / 86400, value))
            if len(values) < 3:
                continue

            # Linear regression
            n = len(values)
            x_mean = sum(i for i, _ in values) / n
            y_mean = sum(v for _, v in values) / n

            numerator = sum((x - x_mean) * (y - y_mean) for x, y in values)
            denominator = sum((x - x_mean) ** 2 for x, _ in values)

            if denominator == 0:
                continue

            slope = numerator / denominator
            projection = y_mean + slope * (values[-1][0] + 30 - x_mean)
            span = values[-1][0] - values[0][0]
            change_pct = (slope * span) / y_mean * 100 if y_mean > 0 else 0
            if not all(math.isfinite(value) for value in (y_mean, slope, projection, change_pct)):
                numerical_failure = True
                warnings.append(f"{metric}: trend unavailable because the calculation exceeded numeric limits")
                continue
            slopes[metric] = slope
            projected[metric] = max(0, projection)

            # Check if trend is concerning
            is_worse_metric = metric in ("p99_response_time", "avg_response_time", "error_rate")
            is_better_metric = metric == "throughput"

            if is_worse_metric and slope > 0 and abs(change_pct) > 5:
                trending.append(metric)
                degrading = degrading or abs(change_pct) > 20
                warnings.append(
                    f"{metric} increasing +{change_pct:.1f}% over {span:g} days "
                    f"(projected: {projected[metric]:.1f})"
                )
            elif is_better_metric and slope < 0 and abs(change_pct) > 10:
                trending.append(metric)
                degrading = degrading or abs(change_pct) > 20
                warnings.append(
                    f"{metric} decreasing {change_pct:.1f}% over {span:g} days"
                )

        # Overall health
        if not slopes:
            health = "unknown"
            recommendation = "Insufficient dated metric observations to establish a trend."
            warnings.append(recommendation)
        elif degrading:
            health = "degrading"
            recommendation = (
                "Performance is degrading. Investigate recent changes: "
                "code deployments, infrastructure changes, data growth. "
                "Run a full diagnostic test with profiling enabled."
            )
        elif trending:
            health = "drifting"
            recommendation = (
                "Early signs of performance drift. Schedule a baseline comparison "
                "test and review recent commits for potential causes."
            )
        elif numerical_failure:
            health = "unknown"
            recommendation = "Some trends could not be calculated. Check the metric observations."
        else:
            health = "stable"
            recommendation = "Performance is stable. Continue regular monitoring."

        return DriftReport(
            total_days=total_days,
            data_points=len(dated),
            metrics_trending=trending,
            slope_per_day=slopes,
            projected_30day=projected,
            warnings=warnings,
            health=health,
            recommendation=recommendation,
        )


# ═══════════════════════════════════════════════════════════
# Cross-Version Comparison
# ═══════════════════════════════════════════════════════════

@dataclass
class VersionDiff:
    metric: str
    v1_value: float
    v2_value: float
    delta_pct: Optional[float]
    direction: str  # better / worse / stable
    severity: str    # low / medium / high
    possible_root_causes: list[str] = field(default_factory=list)


@dataclass
class VersionComparisonReport:
    version_a: str
    version_b: str
    diffs: list[VersionDiff] = field(default_factory=list)
    overall_verdict: str = "stable"
    root_cause_hints: list[str] = field(default_factory=list)
    summary: str = ""
    unavailable_metrics: list[str] = field(default_factory=list)

    def model_dump(self) -> dict:
        return {
            "version_a": self.version_a,
            "version_b": self.version_b,
            "diffs": [
                {
                    "metric": d.metric, "v1": d.v1_value, "v2": d.v2_value,
                    "delta_pct": round(d.delta_pct, 1) if d.delta_pct is not None else None,
                    "delta_absolute": round(d.v2_value - d.v1_value, 2), "direction": d.direction,
                    "severity": d.severity,
                    "possible_root_causes": d.possible_root_causes,
                }
                for d in self.diffs
            ],
            "overall_verdict": self.overall_verdict,
            "root_cause_hints": self.root_cause_hints,
            "summary": self.summary,
            "unavailable_metrics": self.unavailable_metrics,
        }


class VersionComparator:
    """Deep comparison between two versions with root cause hints."""

    @staticmethod
    def _measurement(value) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) and number >= 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    def compare(
        self,
        version_a: dict, version_b: dict,
        version_a_name: str = "v1",
        version_b_name: str = "v2",
    ) -> VersionComparisonReport:
        """Compare two test result sets and provide root cause analysis.

        Args:
            version_a: Baseline metrics dict.
            version_b: Current metrics dict.
            version_a_name: Label for baseline version.
            version_b_name: Label for current version.
        """
        metrics = [
            ("avg_response_time", "Avg Response Time", "higher_is_worse"),
            ("p95_response_time", "P95 Response Time", "higher_is_worse"),
            ("p99_response_time", "P99 Response Time", "higher_is_worse"),
            ("max_response_time", "Max Response Time", "higher_is_worse"),
            ("error_rate", "Error Rate", "higher_is_worse"),
            ("throughput", "Throughput", "lower_is_worse"),
        ]

        diffs = []
        root_causes = set()
        worse_count = 0
        unavailable = []

        for metric_key, metric_label, direction in metrics:
            v1 = self._measurement(version_a.get(metric_key))
            v2 = self._measurement(version_b.get(metric_key))
            if v1 is None or v2 is None:
                unavailable.append(metric_key)
                continue

            delta_pct = (v2 - v1) / v1 * 100 if v1 else None
            if delta_pct is not None and not math.isfinite(delta_pct):
                delta_pct = None

            if delta_pct is None:
                if v1 == v2:
                    direction_str, severity = "stable", "low"
                else:
                    worse = (v2 > v1) == (direction == "higher_is_worse")
                    direction_str, severity = ("worse" if worse else "better"), "unknown"
                    worse_count += int(worse)
            elif direction == "higher_is_worse":
                if delta_pct > 10:
                    direction_str = "worse"
                    severity = "high" if delta_pct > 50 else ("medium" if delta_pct > 25 else "low")
                    worse_count += 1
                elif delta_pct < -10:
                    direction_str = "better"
                    severity = "low"
                else:
                    direction_str = "stable"
                    severity = "low"
            elif direction == "lower_is_worse":
                if delta_pct < -10:
                    direction_str = "worse"
                    severity = "high" if delta_pct < -50 else ("medium" if delta_pct < -25 else "low")
                    worse_count += 1
                elif delta_pct > 10:
                    direction_str = "better"
                    severity = "low"
                else:
                    direction_str = "stable"
                    severity = "low"
            else:
                direction_str = "stable"
                severity = "low"

            # Root cause hints
            causes = []
            if metric_key == "avg_response_time" and direction_str == "worse":
                if delta_pct is not None and delta_pct > 100:
                    causes.append("Possible N+1 query introduced or missing DB index")
                    causes.append("New external API call without timeout/caching")
                elif delta_pct is not None and delta_pct > 30:
                    causes.append("Possible algorithmic inefficiency or larger payloads")
                elif delta_pct is not None:
                    causes.append("Minor regression — check recent code changes")

            if metric_key == "error_rate" and direction_str == "worse":
                if delta_pct is not None and delta_pct > 100:
                    causes.append("Possible API contract change or auth configuration issue")
                causes.append("Check server logs for new error patterns")

            if metric_key == "throughput" and direction_str == "worse":
                causes.append("Throughput degradation — possible resource contention or thread pool exhaustion")

            if metric_key == "p99_response_time" and direction_str == "worse":
                causes.append("Tail latency increased — check for GC pauses, lock contention, or external dependency slowdown")

            for cause in causes:
                root_causes.add(cause)

            diffs.append(VersionDiff(
                metric=metric_label,
                v1_value=v1,
                v2_value=v2,
                delta_pct=delta_pct,
                direction=direction_str,
                severity=severity,
                possible_root_causes=causes,
            ))

        # Overall verdict
        if not diffs or (worse_count == 0 and unavailable):
            overall = "unknown"
            summary = "Insufficient comparable measurements for an overall verdict"
        elif worse_count == 0:
            overall = "stable"
            summary = f"No regressions detected between {version_a_name} and {version_b_name}"
        elif worse_count <= 2:
            overall = "minor_regression"
            summary = f"{worse_count} metric(s) degraded — review recommended"
        else:
            overall = "regression"
            summary = f"{worse_count} metrics degraded — {version_b_name} is significantly worse than {version_a_name}"

        # Per-label comparison if available
        per_label_a = version_a.get("per_label") or {}
        per_label_b = version_b.get("per_label") or {}
        per_label_a = per_label_a if isinstance(per_label_a, dict) else {}
        per_label_b = per_label_b if isinstance(per_label_b, dict) else {}
        all_labels = set(per_label_a.keys()) | set(per_label_b.keys())

        for label in all_labels:
            a = per_label_a.get(label, {})
            b = per_label_b.get(label, {})
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue
            a_p99 = self._measurement(a.get("p99_response_time"))
            b_p99 = self._measurement(b.get("p99_response_time"))
            if a_p99 and b_p99 is not None and b_p99 > a_p99 * 1.3:
                root_causes.add(f"Endpoint '{label}': P99 increased by {(b_p99-a_p99)/a_p99*100:.0f}%")

        return VersionComparisonReport(
            version_a=version_a_name,
            version_b=version_b_name,
            diffs=diffs,
            overall_verdict=overall,
            root_cause_hints=sorted(root_causes)[:10],
            summary=summary,
            unavailable_metrics=unavailable,
        )


cost_estimator = CostEstimator()
drift_detector = DriftDetector()
version_comparator = VersionComparator()
