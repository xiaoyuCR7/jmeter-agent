"""Validators — extract numeric claims from LLM output and verify against ground-truth facts.

The rule is simple: LLM is allowed to make numeric statements, but every statement
must be re-derivable from the FactSheet. If it cannot be re-derived, it is rejected.
"""

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

from app.models.schemas import FactSheet, MetricClaim, ValidationResult

logger = logging.getLogger(__name__)


# Tolerance for floating point comparison
DEFAULT_TOLERANCE = 0.05  # 5% relative or absolute
ABSOLUTE_TOLERANCE_MS = 50  # 50ms absolute for response times


class MetricClaimExtractor:
    """Extract numeric metric claims from analysis text."""

    # metric_name_pattern -> (canonical_name, unit)
    # Patterns are permissive: allow Chinese words between the metric name and number.
    METRIC_PATTERNS = [
        # Percentiles
        (r"p99[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", "p99", "ms"),
        (r"p95[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", "p95", "ms"),
        (r"p90[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", "p90", "ms"),
        (r"p50[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", "p50", "ms"),
        # Aggregates
        (r"平均响应时间[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", "avg_response_time", "ms"),
        (r"最大响应时间[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", "max_response_time", "ms"),
        (r"平均延迟[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)?", "avg_response_time", "ms"),
        (r"吞吐量[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*(req/s|rps|tps|请求/秒)?", "throughput", "req/s"),
        (r"TPS[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)", "throughput", "req/s"),
        (r"RPS[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)", "throughput", "req/s"),
        (r"错误率[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*%?", "error_rate", "%"),
        (r"错误率[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+(?:\.\d+)?)\s*percent", "error_rate", "%"),
        (r"总请求数[^\d]{0,20}(?:约|大约|大概|约为|为|是)?\s*(\d+)", "total_requests", ""),
    ]

    def extract(self, text: str) -> list[MetricClaim]:
        """Extract all metric claims from analysis text."""
        claims = []
        for pattern, canonical_name, default_unit in self.METRIC_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                value_str = match.group(1)
                unit_group = match.group(2) if match.lastindex and match.lastindex >= 2 else None
                try:
                    value = float(value_str)
                except ValueError:
                    continue

                unit = default_unit
                if unit_group:
                    unit = self._normalize_unit(unit_group)

                # Convert seconds to milliseconds if needed
                if unit in ("s", "秒") and canonical_name in ("avg_response_time", "max_response_time", "p99", "p95", "p90", "p50"):
                    value *= 1000
                    unit = "ms"

                claims.append(MetricClaim(
                    metric_name=canonical_name,
                    claimed_value=value,
                    unit=unit,
                    context=text[max(0, match.start() - 20):match.end() + 20],
                ))
        return claims

    @staticmethod
    def _normalize_unit(unit_str: str) -> str:
        u = unit_str.lower().strip()
        if u in ("ms", "毫秒"):
            return "ms"
        if u in ("s", "秒"):
            return "s"
        if u in ("req/s", "rps", "tps", "请求/秒"):
            return "req/s"
        if u in ("%", "percent"):
            return "%"
        return u


class FactValidator:
    """Validate LLM analysis claims against a deterministic FactSheet."""

    def __init__(self, tolerance: float = DEFAULT_TOLERANCE, absolute_ms: float = ABSOLUTE_TOLERANCE_MS):
        self.tolerance = tolerance
        self.absolute_ms = absolute_ms
        self.extractor = MetricClaimExtractor()

    @staticmethod
    def _get_fact_value(fact: FactSheet, metric_name: str) -> Optional[float]:
        """Resolve a canonical metric name to its ground-truth value."""
        if metric_name == "total_requests":
            return float(fact.total_requests)
        if metric_name == "avg_response_time":
            return fact.avg_response_time
        if metric_name == "max_response_time":
            return fact.max_response_time
        if metric_name == "error_rate":
            return fact.error_rate
        if metric_name == "throughput":
            return fact.throughput
        if metric_name in fact.percentiles:
            return fact.percentiles[metric_name]
        # Try per-label aggregate? For now, return None and mark as missing.
        return None

    @staticmethod
    def _matches(claimed: float, actual: float, metric_name: str, absolute_ms: float, tolerance: float) -> bool:
        """Compare claimed vs actual with domain-aware tolerance."""
        diff = abs(claimed - actual)
        if metric_name in ("avg_response_time", "max_response_time", "p99", "p95", "p90", "p50"):
            # Use absolute tolerance for response times (ms)
            if diff <= absolute_ms:
                return True
        # Relative / percentage tolerance for others
        if actual == 0:
            return diff <= tolerance
        return diff / abs(actual) <= tolerance

    def validate(self, analysis_text: str, fact: FactSheet) -> ValidationResult:
        """Validate all numeric claims in analysis_text against fact."""
        claims = self.extractor.extract(analysis_text)
        mismatches = []
        missing_fields = []

        for claim in claims:
            actual = self._get_fact_value(fact, claim.metric_name)
            if actual is None:
                missing_fields.append(claim.metric_name)
                continue

            if not self._matches(
                claimed=claim.claimed_value,
                actual=actual,
                metric_name=claim.metric_name,
                absolute_ms=self.absolute_ms,
                tolerance=self.tolerance,
            ):
                mismatches.append({
                    "metric": claim.metric_name,
                    "claimed": claim.claimed_value,
                    "actual": actual,
                    "unit": claim.unit or "",
                    "context": claim.context,
                })

        is_valid = not mismatches and not missing_fields

        # Build a corrected summary if invalid
        corrected_summary = None
        if not is_valid:
            lines = ["以下数字声明与原始 JTL 计算结果不符，已修正："]
            for m in mismatches:
                lines.append(f"- {m['metric']}: LLM 声明 {m['claimed']}{m['unit']}，实际 {m['actual']}{m['unit']}")
            corrected_summary = "\n".join(lines)

        return ValidationResult(
            is_valid=is_valid,
            claims=claims,
            mismatches=mismatches,
            missing_fields=list(set(missing_fields)),
            corrected_summary=corrected_summary,
        )


class HypothesisExtractor:
    """Extract structured hypotheses from LLM analysis text.

    In the future this can be replaced by a structured LLM call (response_format=json).
    For now, use lightweight regex/markers to avoid introducing extra LLM latency.
    """

    def extract(self, analysis_text: str, execution_id: str, module: str, version: Optional[str] = None) -> list:
        """Extract hypothesis objects from text.

        Returns a list of dicts that can be passed to HypothesisLedger.add().
        """
        from app.models.schemas import Hypothesis, HypothesisCategory, HypothesisStatus
        hypotheses = []

        # Look for explicit hypothesis markers
        # Pattern: 【假设】... 或 "假设：..." 或 "初步判断：..."
        marker_patterns = [
            r"【假设】[:：]?\s*(.+?)(?=\n\n|\n【|\Z)",
            r"假设[:：]\s*(.+?)(?=\n\n|\n\d+\.|[##]|\Z)",
            r"初步判断[:：]\s*(.+?)(?=\n\n|\n\d+\.|[##]|\Z)",
            r"瓶颈[:：]\s*(.+?)(?=\n\n|\n\d+\.|[##]|\Z)",
        ]

        seen = set()
        for pattern in marker_patterns:
            for match in re.finditer(pattern, analysis_text, re.DOTALL | re.IGNORECASE):
                statement = match.group(1).strip()
                if len(statement) < 10 or statement in seen:
                    continue
                seen.add(statement)

                category = HypothesisCategory.BOTTLENECK
                if any(k in statement for k in ("建议", "优化", "推荐", "应该", "可以")):
                    category = HypothesisCategory.RECOMMENDATION
                elif any(k in statement for k in ("根因", "原因", "由于", "因为")):
                    category = HypothesisCategory.ROOT_CAUSE

                hypotheses.append(Hypothesis(
                    hypothesis_id="",
                    source_execution_id=execution_id,
                    module=module,
                    version=version or "unknown",
                    category=category,
                    statement=statement,
                    confidence=0.5,
                    status=HypothesisStatus.UNTESTED,
                ))

        return hypotheses


# Module singletons
claim_extractor = MetricClaimExtractor()
fact_validator = FactValidator()
hypothesis_extractor = HypothesisExtractor()
