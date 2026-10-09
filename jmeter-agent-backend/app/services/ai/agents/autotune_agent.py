"""AutoTuneAgent — autonomous multi-dimensional test parameter optimization.

Given a performance goal, the agent runs tests, analyzes results, adjusts
parameters, and converges on the optimal configuration — without human
intervention.

Supports three optimization strategies:
  - binary_search: single-param max-safe-value under a constraint
  - linear_scan: step through single-param range to find transition points
  - llm_guided: LLM decides ALL tunable parameters each iteration (multi-dim)

Multi-dimensional mode:
  When AutoTuneConfig.tunable_params is populated, the LLM-guided strategy
  optimizes all specified parameters simultaneously (concurrency, ramp-up,
  duration, think-time). Binary/linear search remain single-param and use
  config.optimize_param to select which parameter to vary.
"""

import time
import math
import os
import shutil
import json as _json
import re as _re
import logging
import asyncio
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

from app.models.schemas import DetailedTestSummary
from app.services import project_scope

logger = logging.getLogger(__name__)


class OptimizationGoal(str, Enum):
    MAX_SAFE_CONCURRENCY = "max_safe_concurrency"
    MAX_THROUGHPUT = "max_throughput"
    MIN_LATENCY = "min_latency"
    FIND_BREAKPOINT = "find_breakpoint"
    CUSTOM = "custom"


class OptimizationStrategy(str, Enum):
    BINARY_SEARCH = "binary_search"
    LINEAR_SCAN = "linear_scan"
    LLM_GUIDED = "llm_guided"


# ── Multi-dimensional parameter types ──────────────────────────

@dataclass
class TunableParameter:
    """A single tunable parameter with its valid range.

    Used in multi-dim mode to tell the optimizer which JMX properties
    to vary and within what bounds.
    """
    name: str                              # "concurrency" | "ramp_up" | "duration" | "think_time"
    min_value: float
    max_value: float
    step: float = 1.0                      # granularity for linear_scan
    description: str = ""                  # human-readable label for LLM prompts

    # JMX property mapping
    @property
    def jmx_property(self) -> str:
        _map = {
            "concurrency": "ThreadGroup.num_threads",
            "ramp_up": "ThreadGroup.ramp_time",
            "duration": "ThreadGroup.duration",
            "think_time": "ConstantTimer.delay",
        }
        return _map.get(self.name, "")


# Pre-built tunable param sets for common scenarios
PRESET_CONCURRENCY_ONLY = [
    TunableParameter("concurrency", 5, 1000, 5, "并发用户数"),
]
PRESET_CONCURRENCY_RAMPUP = [
    TunableParameter("concurrency", 5, 1000, 5, "并发用户数"),
    TunableParameter("ramp_up", 10, 300, 10, "加压时间(秒)"),
]
PRESET_FULL = [
    TunableParameter("concurrency", 5, 1000, 5, "并发用户数"),
    TunableParameter("ramp_up", 10, 300, 10, "加压时间(秒)"),
    TunableParameter("duration", 30, 600, 30, "持续时间(秒)"),
    TunableParameter("think_time", 0, 2000, 100, "思考时间(ms)"),
]


# ── Config ─────────────────────────────────────────────────────

@dataclass
class AutoTuneConfig:
    """Configuration for an AutoTune run.

    Single-dim mode (backward compatible):
        Omit tunable_params. Optimizes concurrency only.
        binary_search / linear_scan vary optimize_param (default: "concurrency").

    Multi-dim mode:
        Populate tunable_params with desired parameters.
        llm_guided strategy optimizes all params simultaneously.
        binary_search / linear_scan vary the first entry in tunable_params.
    """
    goal: OptimizationGoal = OptimizationGoal.MAX_SAFE_CONCURRENCY
    strategy: OptimizationStrategy = OptimizationStrategy.BINARY_SEARCH
    constraint_metric: str = "p99_response_time"
    constraint_operator: str = "lt"
    constraint_value: float = 500.0

    # Single-dim compatibility (used when tunable_params is empty)
    min_concurrency: int = 5
    max_concurrency: int = 1000
    ramp_up_seconds: int = 30
    duration_seconds: int = 120
    think_time_ms: int = 0

    # Iteration control
    max_iterations: int = 12
    convergence_threshold_pct: float = 5.0
    cooldown_between_runs: int = 10

    # Safety
    circuit_breaker_max_failures: int = 3
    circuit_breaker_max_error_rate: float = 50.0

    # Multi-dim: parameters to optimize (empty = single-dim concurrency mode)
    tunable_params: list = field(default_factory=list)

    # For single-dim strategies: which param to vary (default: first tunable or "concurrency")
    optimize_param: str = "concurrency"

    @property
    def is_multi_dim(self) -> bool:
        return len(self.tunable_params) > 0

    def get_param_range(self, name: str) -> Optional["TunableParameter"]:
        """Get the range for a named parameter."""
        for p in self.tunable_params:
            if p.name == name:
                return p
        return None

    def get_active_params(self) -> list["TunableParameter"]:
        """Return the list of actively tuned parameters."""
        if self.tunable_params:
            return list(self.tunable_params)
        # Backward compat: implicit concurrency-only
        return [TunableParameter("concurrency", self.min_concurrency, self.max_concurrency, 5, "并发用户数")]

    def get_fixed_params(self) -> dict:
        """Return fixed (non-tuned) parameters as a dict for JMX modification."""
        active_names = {p.name for p in self.get_active_params()}
        fixed = {}
        if "ramp_up" not in active_names:
            fixed["ramp_up"] = self.ramp_up_seconds
        if "duration" not in active_names:
            fixed["duration"] = self.duration_seconds
        if "think_time" not in active_names:
            fixed["think_time"] = self.think_time_ms
        return fixed


# ── Iteration / Result ─────────────────────────────────────────

@dataclass
class AutoTuneIteration:
    """Result of a single optimization iteration."""
    iteration: int
    concurrent_users: int
    ramp_up: int
    duration: int
    metrics: Optional[dict] = None
    constraint_value: float = 0.0
    constraint_met: bool = False
    is_converged: bool = False
    error: Optional[str] = None
    execution_id: str = ""
    param_values: dict = field(default_factory=dict)  # all tuned param values this iteration


@dataclass
class AutoTuneResult:
    """Final result of an AutoTune optimization run."""
    goal: str
    strategy: str
    optimal_concurrency: int = 0
    optimal_metrics: dict = field(default_factory=dict)
    optimal_params: dict = field(default_factory=dict)  # all optimal param values (multi-dim)
    iterations: list[AutoTuneIteration] = field(default_factory=list)
    total_iterations: int = 0
    total_duration_seconds: float = 0.0
    convergence_reason: str = ""
    recommendations: list[str] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None

    def model_dump(self) -> dict:
        return {
            "goal": self.goal, "strategy": self.strategy,
            "optimal_concurrency": self.optimal_concurrency,
            "optimal_params": self.optimal_params,
            "optimal_metrics": self.optimal_metrics,
            "iterations": [
                {
                    "iteration": i.iteration,
                    "concurrent_users": i.concurrent_users,
                    "param_values": i.param_values,
                    "constraint_value": i.constraint_value,
                    "constraint_met": i.constraint_met,
                    "error": i.error,
                }
                for i in self.iterations
            ],
            "total_iterations": self.total_iterations,
            "total_duration_seconds": self.total_duration_seconds,
            "convergence_reason": self.convergence_reason,
            "recommendations": self.recommendations,
            "success": self.success,
            "error": self.error,
        }


# ── Agent ──────────────────────────────────────────────────────

class AutoTuneAgent:
    """Autonomous multi-dimensional test parameter optimizer.

    Single-dim usage (backward compatible):
        agent = AutoTuneAgent()
        result = await agent.optimize(
            jmx_path="test_plans/default/baseline.jmx",
            config=AutoTuneConfig(
                goal=OptimizationGoal.MAX_SAFE_CONCURRENCY,
                constraint_value=500.0,
            ),
        )

    Multi-dim usage:
        config = AutoTuneConfig(
            goal=OptimizationGoal.MAX_SAFE_CONCURRENCY,
            strategy=OptimizationStrategy.LLM_GUIDED,
            constraint_metric="p99_response_time",
            constraint_operator="lt",
            constraint_value=500.0,
            tunable_params=PRESET_FULL,
        )
        result = await agent.optimize(jmx_path, config)
        print(f"Optimal: {result.optimal_params}")
    """

    # ── JMX parameter name → JMX property name ─────────────────
    JMX_PROP_MAP = {
        "concurrency": "ThreadGroup.num_threads",
        "ramp_up": "ThreadGroup.ramp_time",
        "duration": "ThreadGroup.duration",
        "think_time": "ConstantTimer.delay",
    }

    def __init__(self):
        self._on_iteration: Optional[Callable[[AutoTuneIteration], Awaitable[None]]] = None
        self._cancel_flag = False
        self._consecutive_failures: int = 0
        self._backup_path: Optional[str] = None

    def cancel(self):
        self._cancel_flag = True

    # ── Backup / Restore ──────────────────────────────────────

    def _backup_jmx(self, jmx_path: str) -> str:
        import uuid
        backup = jmx_path + f".autotune.{uuid.uuid4().hex}.bak"
        project_scope.require_path(jmx_path)
        project_scope.require_path(backup, write=True)
        if os.path.exists(jmx_path):
            shutil.copy2(jmx_path, backup)
            logger.info(f"AutoTune: backed up JMX to {backup}")
        return backup

    def _restore_jmx(self, jmx_path: str, backup_path: str) -> bool:
        if backup_path:
            try:
                project_scope.require_path(backup_path)
                project_scope.require_path(jmx_path, write=True)
                if not os.path.exists(backup_path):
                    return False
                shutil.copy2(backup_path, jmx_path)
                logger.info(f"AutoTune: restored JMX from {backup_path}")
                return True
            except Exception as e:
                logger.error(f"AutoTune: failed to restore JMX from backup: {e}")
        return False

    # ── Circuit breaker ───────────────────────────────────────

    def _check_circuit_breaker(
        self, iteration: "AutoTuneIteration", config: AutoTuneConfig,
    ) -> Optional[str]:
        if iteration.error:
            self._consecutive_failures += 1
            if self._consecutive_failures >= config.circuit_breaker_max_failures:
                return (
                    f"Circuit breaker tripped: {self._consecutive_failures} "
                    f"consecutive failures (limit: {config.circuit_breaker_max_failures}). "
                    f"Last error: {iteration.error[:200]}"
                )
        else:
            self._consecutive_failures = 0
            if iteration.metrics:
                error_rate = iteration.metrics.get("error_rate", 0) or 0
                if error_rate > config.circuit_breaker_max_error_rate:
                    return (
                        f"Circuit breaker tripped: error rate {error_rate:.1f}% "
                        f"exceeds safety limit {config.circuit_breaker_max_error_rate}%."
                    )
        return None

    # ── Main entry point ──────────────────────────────────────

    async def optimize(
        self,
        jmx_path: str,
        config: AutoTuneConfig = None,
        on_iteration: Callable[[AutoTuneIteration], Awaitable[None]] = None,
    ) -> AutoTuneResult:
        config = config or AutoTuneConfig()
        self._on_iteration = on_iteration
        self._cancel_flag = False
        self._consecutive_failures = 0
        self._backup_path = None

        start_time = time.time()
        iterations: list[AutoTuneIteration] = []
        result = None

        try:
            if config.goal != OptimizationGoal.MAX_SAFE_CONCURRENCY:
                raise ValueError(f"Unsupported optimization goal: {config.goal}")
            if config.max_iterations < 1:
                raise ValueError('max_iterations must be positive')
            if config.constraint_metric not in DetailedTestSummary.model_fields:
                raise ValueError(f'Unknown constraint metric: {config.constraint_metric}')
            self._evaluate_constraint({config.constraint_metric: 0}, config)
            if not any(param.name == 'concurrency' for param in config.get_active_params()):
                raise ValueError('Unsupported goal/parameter combination: max_safe_concurrency requires concurrency')
            if config.strategy != OptimizationStrategy.LLM_GUIDED and self._get_optimize_param(config).name != 'concurrency':
                raise ValueError('Unsupported goal/parameter combination: max_safe_concurrency requires concurrency')
            from app.core.config import settings, safe_path
            await project_scope.refresh_scope()
            jmx_path = safe_path(jmx_path, allowed_dirs=[settings.TEST_PLANS_DIR])
            project_scope.require_path(jmx_path, write=True)
            if not os.path.isfile(jmx_path):
                raise ValueError('JMX plan does not exist')
            if config.strategy == OptimizationStrategy.BINARY_SEARCH:
                result = await self._binary_search(jmx_path, config, iterations)
            elif config.strategy == OptimizationStrategy.LINEAR_SCAN:
                result = await self._linear_scan(jmx_path, config, iterations)
            else:
                result = await self._llm_guided(jmx_path, config, iterations)
        except Exception as e:
            logger.exception("AutoTune optimization crashed:")
            result = AutoTuneResult(
                goal=config.goal.value, strategy=config.strategy.value,
                success=False, error=f"Optimization crashed: {str(e)}",
            )
        finally:
            if self._backup_path:
                restored = False
                try:
                    # Restoration is a new write, not permission inherited from backup creation.
                    await project_scope.refresh_scope()
                    restored = self._restore_jmx(jmx_path, self._backup_path)
                except Exception as e:
                    logger.error(f'AutoTune: could not authorize JMX restoration: {e}')
                if not restored:
                    message = f'Failed to restore original JMX; backup retained at {self._backup_path}'
                    logger.error(message)
                    if result is not None:
                        result.success = False
                        result.error = '; '.join(value for value in (result.error, message) if value)

        if result is None:
            result = AutoTuneResult(
                goal=config.goal.value, strategy=config.strategy.value,
                success=False, error="No strategy executed",
            )
        result.total_duration_seconds = time.time() - start_time
        result.iterations = iterations
        result.total_iterations = len(iterations)
        return result

    # ── Binary Search (single-param) ───────────────────────────

    async def _binary_search(
        self, jmx_path: str, config: AutoTuneConfig,
        iterations: list[AutoTuneIteration],
    ) -> AutoTuneResult:
        param = self._get_optimize_param(config)
        lo = int(param.min_value)
        hi = int(param.max_value)
        best_val = lo
        best_metrics = {}

        for i in range(config.max_iterations):
            if self._cancel_flag:
                return self._cancelled_result(config, best_val, best_metrics)

            mid = (lo + hi) // 2
            overrides = self._build_overrides(config, {param.name: mid})
            it = await self._run_iteration(jmx_path, overrides, config, i + 1)
            iterations.append(it)

            trip_reason = self._check_circuit_breaker(it, config)
            if trip_reason:
                return self._abort_result(config, best_val, best_metrics, trip_reason)

            if it.error:
                hi = mid - 1
                continue

            if it.constraint_met:
                best_val = mid
                best_metrics = it.metrics or {}
                lo = mid + 1
            else:
                hi = mid - 1

            if lo > hi or (hi - lo) < max(1, int(param.step)):
                conv_reason = f"Binary search converged on {param.name}: [{lo}, {hi}] exhausted"
                break
        else:
            conv_reason = f"Max iterations ({config.max_iterations}) reached"

        conv_reason = conv_reason if 'conv_reason' in dir() else "Converged"
        recs = self._generate_recommendations(best_val, best_metrics, config, iterations)
        result = self._success_result(config, best_val, best_metrics, conv_reason, recs)
        result.optimal_params = {param.name: best_val} if result.success else {}
        return result

    # ── Linear Scan (single-param) ─────────────────────────────

    async def _linear_scan(
        self, jmx_path: str, config: AutoTuneConfig,
        iterations: list[AutoTuneIteration],
    ) -> AutoTuneResult:
        param = self._get_optimize_param(config)
        lo = int(param.min_value)
        hi = int(param.max_value)
        step = max(int(param.step), (hi - lo) // config.max_iterations)
        best_val = lo
        best_metrics = {}
        prev_value = None
        last_conc = lo

        for i, val in enumerate(range(lo, hi + 1, step)):
            if i >= config.max_iterations:
                break
            if self._cancel_flag:
                return self._cancelled_result(config, best_val, best_metrics)

            overrides = self._build_overrides(config, {param.name: val})
            it = await self._run_iteration(jmx_path, overrides, config, i + 1)
            iterations.append(it)
            last_conc = val

            trip_reason = self._check_circuit_breaker(it, config)
            if trip_reason:
                return self._abort_result(config, best_val, best_metrics, trip_reason)

            if it.error:
                continue

            if it.constraint_met:
                best_val = val
                best_metrics = it.metrics or {}
            else:
                break

            if prev_value not in (None, 0) and it.constraint_value > 0:
                change_pct = abs(it.constraint_value - prev_value) / prev_value * 100
                if change_pct < config.convergence_threshold_pct:
                    break
            prev_value = it.constraint_value

        conv_reason = (
            f"Linear scan: constraint failed at {param.name}={last_conc}"
            if last_conc > best_val else "Scan complete"
        )
        recs = self._generate_recommendations(best_val, best_metrics, config, iterations)
        result = self._success_result(config, best_val, best_metrics, conv_reason, recs)
        result.optimal_params = {param.name: best_val} if result.success else {}
        return result

    # ── LLM-Guided (multi-dim capable) ─────────────────────────

    async def _llm_guided(
        self, jmx_path: str, config: AutoTuneConfig,
        iterations: list[AutoTuneIteration],
    ) -> AutoTuneResult:
        from app.services.ai.adapters.factory import AIAdapterFactory
        from app.models.schemas import AIChatMessage

        adapter = AIAdapterFactory.create()
        active_params = config.get_active_params()
        is_multi = config.is_multi_dim

        # Track best across all dimensions
        best_concurrency = int(active_params[0].min_value) if active_params else config.min_concurrency
        best_metrics = {}
        best_overrides: dict = {}
        history: list[dict] = []

        # Initialize current values at midpoints
        current_overrides = self._build_overrides(config, {
            p.name: int((p.min_value + p.max_value) / 2)
            for p in active_params
        })

        for i in range(config.max_iterations):
            if self._cancel_flag:
                return self._cancelled_result(config, best_concurrency, best_metrics)

            it = await self._run_iteration(jmx_path, current_overrides, config, i + 1)
            iterations.append(it)

            trip_reason = self._check_circuit_breaker(it, config)
            if trip_reason:
                return self._abort_result(config, best_concurrency, best_metrics, trip_reason)

            if it.error:
                # Back off all params toward minimum
                for p in active_params:
                    current_overrides[p.name] = max(
                        int(p.min_value),
                        current_overrides.get(p.name, int(p.min_value)) // 2,
                    )
                continue

            if it.constraint_met and (not best_metrics or it.concurrent_users > best_concurrency):
                best_concurrency = it.concurrent_users
                best_metrics = it.metrics or {}
                best_overrides = dict(current_overrides)

            # Record history entry
            hist_entry = {
                "iteration": i + 1,
                "concurrency": it.concurrent_users,
                "p99": it.metrics.get("p99_response_time", "N/A") if it.metrics else "N/A",
                "error_rate": it.metrics.get("error_rate", "N/A") if it.metrics else "N/A",
                "throughput": it.metrics.get("throughput", "N/A") if it.metrics else "N/A",
                "constraint_met": it.constraint_met,
                "params": dict(current_overrides),
            }
            history.append(hist_entry)

            # Build LLM prompt
            await project_scope.refresh_scope()
            project_scope.require_path(jmx_path)
            prompt = self._build_llm_prompt(config, history, active_params, is_multi)
            next_overrides = await self._ask_llm(adapter, prompt, current_overrides, active_params, best_overrides)
            current_overrides = next_overrides

            # Convergence check
            if len(iterations) >= 3:
                last_three = iterations[-3:]
                if all(it.constraint_met for it in last_three):
                    break

        conv_reason = "LLM-guided optimization complete"
        recs = self._generate_recommendations(best_concurrency, best_metrics, config, iterations)
        if is_multi and best_overrides:
            recs.insert(0, f"Optimal parameters: {best_overrides}")
        result = self._success_result(config, best_concurrency, best_metrics, conv_reason, recs)
        result.optimal_params = best_overrides if result.success else {}
        return result

    def _build_llm_prompt(
        self, config: AutoTuneConfig, history: list[dict],
        active_params: list, is_multi: bool,
    ) -> str:
        """Build the LLM decision prompt for the next iteration."""
        constraint_desc = f"{config.constraint_metric} {config.constraint_operator} {config.constraint_value}"

        if is_multi:
            param_desc = "\n".join(
                f"- {p.name} ({p.description}): range [{p.min_value}, {p.max_value}], step {p.step}"
                for p in active_params
            )
            decision_format = ', '.join(
                f'"{p.name}": <number>' for p in active_params
            )
            decision_keys = ', '.join(f'"{p.name}"' for p in active_params)

            return f"""You are optimizing a performance test. Goal: {config.goal.value}.

Constraint: {constraint_desc}

Tunable parameters:
{param_desc}

History:
{self._format_history(history, is_multi=True)}

Based on these results, what should be the next values for ALL parameters?
Consider trade-offs: higher ramp-up reduces initial spike, longer duration improves stability measurement, think time adds realism.

Respond with ONLY a JSON object:
{{"params": {{{decision_format}}}, "reason": "<brief rationale>"}}

Required keys in "params": {decision_keys}"""
        else:
            param = active_params[0] if active_params else None
            param_name = param.name if param else "concurrency"
            lo = int(param.min_value) if param else config.min_concurrency
            hi = int(param.max_value) if param else config.max_concurrency

            return f"""You are optimizing a performance test. Goal: {config.goal.value}.

Constraint: {constraint_desc}
Current {param_name} range: [{lo}, {hi}]

History:
{self._format_history(history, is_multi=False)}

Based on these results, what should be the next {param_name} level?
Respond with ONLY a JSON: {{"next_concurrency": <number>, "reason": "<brief>"}}"""

    async def _ask_llm(
        self, adapter, prompt: str, current: dict,
        active_params: list, best_overrides: dict,
    ) -> dict:
        """Send prompt to LLM, parse the response into parameter overrides."""
        from app.models.schemas import AIChatMessage

        try:
            messages = adapter.prepare_chat_messages(
                [AIChatMessage(role="user", content=prompt)], response_tokens=300)
            response = await adapter.chat(
                messages,
                temperature=0.2, max_tokens=300,
            )
            # Extract JSON from response
            match = _re.search(r'\{[^{}]*"params"\s*:\s*\{[^}]+\}[^}]*\}', response)
            if not match:
                match = _re.search(r'\{[^}]+\}', response)

            if match:
                decision = _json.loads(match.group())
            else:
                decision = {}

            # Multi-dim path: {"params": {"concurrency": 100, "ramp_up": 60, ...}}
            if "params" in decision:
                result = dict(current)
                for p in active_params:
                    if p.name in decision["params"]:
                        val = int(decision["params"][p.name])
                        result[p.name] = max(int(p.min_value), min(int(p.max_value), val))
                return result

            # Single-dim path: {"next_concurrency": 100}
            if "next_concurrency" in decision:
                result = dict(current)
                p = active_params[0] if active_params else None
                if p:
                    val = int(decision["next_concurrency"])
                    result[p.name] = max(int(p.min_value), min(int(p.max_value), val))
                return result

            # Fallback: move toward best
            return self._fallback_params(current, active_params, best_overrides)

        except Exception:
            return self._fallback_params(current, active_params, best_overrides)

    def _fallback_params(
        self, current: dict, active_params: list, best_overrides: dict,
    ) -> dict:
        """Conservative fallback: move current params toward best known values."""
        result = {}
        for p in active_params:
            cur = current.get(p.name, int((p.min_value + p.max_value) / 2))
            best = best_overrides.get(p.name, cur) if best_overrides else cur
            # Move halfway toward best
            mid = (cur + best) // 2
            result[p.name] = max(int(p.min_value), min(int(p.max_value), mid))
        return result

    # ── Core: Run One Iteration ────────────────────────────────

    async def _run_iteration(
        self, jmx_path: str, param_overrides: dict,
        config: AutoTuneConfig, iteration_num: int,
    ) -> AutoTuneIteration:
        """Execute one test with given parameter overrides.

        Args:
            jmx_path: Path to the .jmx file.
            param_overrides: Dict like {"concurrency": 100, "ramp_up": 60, "duration": 300}.
            config: AutoTune configuration.
            iteration_num: 1-based iteration number.
        """
        import uuid
        from app.services.jmeter.jmx_parser import parse_jmx
        from app.services.jmeter.jmx_builder import build_jmx

        # A strategy may have awaited execution, cooldown, or model work since its last check.
        await project_scope.refresh_scope()
        project_scope.require_path(jmx_path, write=True)

        # Create backup on first iteration
        if iteration_num == 1:
            self._backup_path = self._backup_jmx(jmx_path)

        # Merge fixed params with overrides for complete JMX modification
        concurrency = param_overrides.get("concurrency", config.min_concurrency)
        ramp_up = param_overrides.get("ramp_up", config.ramp_up_seconds)
        duration = param_overrides.get("duration", config.duration_seconds)
        think_time = param_overrides.get("think_time", getattr(config, 'think_time_ms', 0))

        # Modify JMX with all parameters
        try:
            tree = parse_jmx(jmx_path)
            for child in tree.get("children", []):
                if isinstance(child, dict) and "ThreadGroup" in child.get("testclass", ""):
                    tg_props = child.get("properties", {})

                    if "ThreadGroup.num_threads" in tg_props:
                        tg_props["ThreadGroup.num_threads"] = str(concurrency)
                    if "ThreadGroup.ramp_time" in tg_props:
                        tg_props["ThreadGroup.ramp_time"] = str(ramp_up)
                    if "ThreadGroup.duration" in tg_props:
                        tg_props["ThreadGroup.duration"] = str(duration)
                    if "ThreadGroup.scheduler" in tg_props and duration > 0:
                        tg_props["ThreadGroup.scheduler"] = True

                # Modify think time in ConstantTimer elements
                if think_time > 0 and isinstance(child, dict):
                    self._set_think_time(child, think_time)

            new_xml = build_jmx(tree)
            os.makedirs(os.path.dirname(jmx_path), exist_ok=True)
            with open(jmx_path, "w", encoding="utf-8") as f:
                f.write(new_xml)
        except Exception as e:
            return AutoTuneIteration(
                iteration=iteration_num, concurrent_users=concurrency,
                ramp_up=ramp_up, duration=duration,
                error=f"Failed to update JMX: {e}",
                param_values=dict(param_overrides),
            )

        # Execute test
        from app.services.execution_lifecycle import execution_lifecycle
        exec_id = f"autotune_{iteration_num}_{str(uuid.uuid4())[:6]}"

        try:
            result = await execution_lifecycle.execute(jmx_path, exec_id)
        except Exception as e:
            return AutoTuneIteration(
                iteration=iteration_num, concurrent_users=concurrency,
                ramp_up=ramp_up, duration=duration,
                error=str(e), param_values=dict(param_overrides),
            )

        # Parse results
        jtl_path = os.path.join(result.report_dir, "result.jtl") if result.report_dir else ""
        metrics = None
        constraint_value = 0.0
        constraint_met = False

        if result.status.value != 'completed' or not jtl_path or not os.path.isfile(jtl_path):
            return AutoTuneIteration(iteration=iteration_num, concurrent_users=concurrency,
                ramp_up=ramp_up, duration=duration, execution_id=exec_id,
                error=getattr(result, 'error_message', None) or f'Execution {result.status.value}: missing successful JTL report',
                param_values=dict(param_overrides))
        if jtl_path:
            try:
                from app.services.jmeter.report_parser import ReportParser
                parser = ReportParser()
                summary = getattr(result, 'summary', None) or await asyncio.to_thread(parser.parse_jtl, jtl_path)
                metrics = summary.model_dump()
                if not summary.total_requests or summary.total_requests < 0:
                    raise ValueError('Report contains no measured samples')
                if not math.isfinite(float(summary.error_rate)):
                    raise ValueError('Report error rate is not finite')
                constraint_value, constraint_met = self._evaluate_constraint(metrics, config)
            except Exception as e:
                return AutoTuneIteration(
                    iteration=iteration_num, concurrent_users=concurrency,
                    ramp_up=ramp_up, duration=duration,
                    error=f"Failed to parse results: {e}",
                    param_values=dict(param_overrides),
                )

        # Cooldown between iterations
        if config.cooldown_between_runs > 0:
            await asyncio.sleep(config.cooldown_between_runs)

        it = AutoTuneIteration(
            iteration=iteration_num,
            concurrent_users=concurrency,
            ramp_up=ramp_up,
            duration=duration,
            metrics=metrics,
            constraint_value=constraint_value,
            constraint_met=constraint_met,
            execution_id=exec_id,
            param_values=dict(param_overrides),
        )

        if self._on_iteration:
            await self._on_iteration(it)

        return it

    def _set_think_time(self, node: dict, think_time_ms: int):
        """Recursively set ConstantTimer.delay in the JMX tree."""
        if not isinstance(node, dict):
            return
        if node.get("testclass") == "ConstantTimer":
            props = node.get("properties", {})
            if "ConstantTimer.delay" in props:
                props["ConstantTimer.delay"] = str(think_time_ms)
                return
        for child in node.get("children", []):
            self._set_think_time(child, think_time_ms)

    # ── Helpers ────────────────────────────────────────────────

    def _get_optimize_param(self, config: AutoTuneConfig) -> "TunableParameter":
        """Get the parameter being optimized in single-dim mode."""
        active = config.get_active_params()
        # Try to match by optimize_param name
        for p in active:
            if p.name == config.optimize_param:
                return p
        return active[0] if active else TunableParameter("concurrency", 5, 1000, 5)

    def _build_overrides(self, config: AutoTuneConfig, dynamic: dict) -> dict:
        """Merge fixed config values with dynamic optimization values."""
        overrides = dict(config.get_fixed_params())
        # Map common param names
        name_map = {"concurrency": "concurrency", "ramp_up": "ramp_up",
                     "duration": "duration", "think_time": "think_time"}
        for k, v in dynamic.items():
            mapped = name_map.get(k, k)
            overrides[mapped] = int(v)
        return overrides

    def _format_history(self, history: list[dict], is_multi: bool = False) -> str:
        lines = []
        for h in history:
            if is_multi:
                params_str = ", ".join(f"{k}={v}" for k, v in h.get("params", {}).items())
                lines.append(
                    f"  Iter {h['iteration']}: {params_str} -> "
                    f"P99={h['p99']}, err={h['error_rate']}%, "
                    f"tps={h['throughput']}, ok={h['constraint_met']}"
                )
            else:
                lines.append(
                    f"  Iter {h['iteration']}: {h['concurrency']} users -> "
                    f"P99={h['p99']}, err={h['error_rate']}%, "
                    f"constraint_met={h['constraint_met']}"
                )
        return "\n".join(lines)

    def _generate_recommendations(
        self, best: int, metrics: dict,
        config: AutoTuneConfig, iterations: list[AutoTuneIteration],
    ) -> list[str]:
        recs = []
        if best > 0 and metrics:
            recs.append(
                f"Optimal concurrency: {best} users — "
                f"P99={metrics.get('p99_response_time', 'N/A')}ms, "
                f"Throughput: {metrics.get('throughput', 'N/A')} RPS, "
                f"Error rate: {metrics.get('error_rate', 'N/A')}%"
            )
            safe = int(best * 0.8)
            recs.append(f"Recommended production concurrency with 20% safety margin: {safe} users")
        if len(iterations) >= 3:
            recs.append(
                f"Optimization took {len(iterations)} iterations. "
                f"Consider increasing duration_seconds for longer stability tests."
            )
        return recs

    def _success_result(
        self, config: AutoTuneConfig, concurrency: int, metrics: dict,
        reason: str, recs: list[str],
    ) -> AutoTuneResult:
        if not metrics:
            return self._abort_result(config, 0, {}, 'No accepted candidate with valid measured metrics')
        return AutoTuneResult(
            goal=config.goal.value, strategy=config.strategy.value,
            optimal_concurrency=concurrency, optimal_metrics=metrics,
            convergence_reason=reason, recommendations=recs, success=True,
        )

    @staticmethod
    def _evaluate_constraint(metrics: dict, config: AutoTuneConfig) -> tuple[float, bool]:
        from app.services.jmeter.sla_engine import SLAThreshold
        actual = metrics.get(config.constraint_metric)
        threshold = SLAThreshold(config.constraint_metric, config.constraint_operator, config.constraint_value)
        error = threshold.validation_error(actual)
        if error:
            raise ValueError(error)
        violated, _ = threshold.evaluate(actual)
        return float(actual), not violated

    def _cancelled_result(
        self, config: AutoTuneConfig, concurrency: int, metrics: dict,
    ) -> AutoTuneResult:
        return AutoTuneResult(
            goal=config.goal.value, strategy=config.strategy.value,
            optimal_concurrency=concurrency, optimal_metrics=metrics,
            convergence_reason="cancelled", success=False, error="Cancelled by user",
        )

    def _abort_result(
        self, config: AutoTuneConfig, concurrency: int, metrics: dict, reason: str,
    ) -> AutoTuneResult:
        return AutoTuneResult(
            goal=config.goal.value, strategy=config.strategy.value,
            optimal_concurrency=concurrency, optimal_metrics=metrics,
            convergence_reason=reason,
            recommendations=[f"Tuning aborted: {reason}"],
            success=False, error=reason,
        )


# Module singleton
autotune = AutoTuneAgent()
