import copy
import json

import pytest

from app.services.insights_engine import DriftDetector, CostEstimator
from app.services.ml_analysis import MLAnomalyDetector


def analysis_adapter(chat):
    from app.services.ai.adapters.deepseek_adapter import DeepSeekAdapter
    adapter = object.__new__(DeepSeekAdapter)
    adapter.max_tokens = 8000
    adapter.chat = chat
    return adapter


@pytest.mark.asyncio
async def test_analyzer_budget_rejection_does_not_call_or_retry_model(monkeypatch):
    from unittest.mock import AsyncMock
    from app.core.config import settings
    import app.services.ai.analyzer as module
    analyzer = object.__new__(module.TestAnalyzer)
    analyzer.adapter = analysis_adapter(AsyncMock(return_value='{}'))
    monkeypatch.setattr(settings, 'AGENT_CONTEXT_TOKEN_BUDGET', 4000)
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, 'sleep', sleep)
    result = await analyzer.analyze({}, {}, 'baseline')
    assert result.analysis_status == 'failed'
    analyzer.adapter.chat.assert_not_awaited()
    sleep.assert_not_awaited()


def test_drift_uses_elapsed_days_without_mutating_input():
    history = [
        {"date": "2026-08-21", "p99_response_time": 300, "error_rate": 2},
        {"date": "2026-08-01", "p99_response_time": 100, "error_rate": 0},
        {"date": "2026-08-11", "p99_response_time": 200, "error_rate": 1},
    ]
    original = copy.deepcopy(history)
    result = DriftDetector().analyze(history)
    assert history == original
    assert result.slope_per_day["p99_response_time"] == pytest.approx(10)
    assert result.projected_30day["p99_response_time"] == pytest.approx(600)
    assert result.slope_per_day["error_rate"] == pytest.approx(.1)
    assert result.health == "degrading"


def test_drift_with_insufficient_or_invalid_evidence_is_unknown():
    result = DriftDetector().analyze([
        {"date": "bad", "throughput": 20},
        {"date": "2026-08-01", "throughput": float("nan")},
    ])
    assert result.health == "unknown"
    assert result.slope_per_day == {}
    json.dumps(result.model_dump(), allow_nan=False)


@pytest.mark.parametrize('invalid', [True, False, 10 ** 400], ids=['true', 'false', 'integer-overflow'])
def test_drift_does_not_treat_invalid_measurements_as_evidence(invalid):
    history = [{'date': f'2026-08-{day:02d}', 'p99_response_time': invalid} for day in (1, 2, 3)]
    result = DriftDetector().analyze(history)
    assert result.health == 'unknown'
    assert result.slope_per_day == {}
    json.dumps(result.model_dump(), allow_nan=False)


@pytest.mark.parametrize('measurements', [
    [1e308, 1e308, 1e308],  # the mean overflows
    [0, 1e307, 2e307],  # the 30-day projection overflows
], ids=['mean-overflow', 'projection-overflow'])
@pytest.mark.parametrize('other', [None, [100, 100, 100], [100, 200, 300]],
                         ids=['no-other-metric', 'stable-other', 'degrading-other'])
def test_drift_unrepresentable_trends_are_not_reported_as_stable(measurements, other):
    history = [{'date': f'2026-08-{i + 1:02d}', 'p99_response_time': value}
               for i, value in enumerate(measurements)]
    if other:
        for point, value in zip(history, other):
            point['avg_response_time'] = value
    result = DriftDetector().analyze(history)
    assert 'p99_response_time' not in result.slope_per_day
    assert 'p99_response_time' not in result.projected_30day
    assert result.health == ('degrading' if other == [100, 200, 300] else 'unknown')
    assert any('p99_response_time' in warning for warning in result.warnings)
    json.dumps(result.model_dump(), allow_nan=False)


def test_anomaly_unbounded_ranges_are_json_safe():
    result = MLAnomalyDetector().compare_to_baseline({"throughput": 10}, {"throughput": 100})
    data = result.model_dump()
    assert data["anomalies"][0]["expected_range"][1] is None
    json.dumps(data, allow_nan=False)


def test_missing_baseline_metrics_do_not_become_zero_throughput():
    result = MLAnomalyDetector().compare_to_baseline({}, {"throughput": 100})
    assert result.total_anomalies == 0
    assert "insufficient" in result.summary.lower()


@pytest.mark.parametrize("kwargs", [
    {"concurrent_users": -1, "duration_seconds": 10},
    {"concurrent_users": 10, "duration_seconds": 10, "avg_response_time_ms": float("nan")},
    {"concurrent_users": 10, "duration_seconds": 10, "avg_response_time_ms": 0},
])
def test_cost_estimate_rejects_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        CostEstimator().estimate(**kwargs)


def test_analysis_prompt_retains_late_spikes_and_treats_missing_data_as_unknown():
    from app.services.ai.prompts.test_analyzer import build_analysis_prompt, TEST_ANALYZER_SYSTEM_PROMPT
    timeline = [{'time_range': f'segment-{i}', 'avg_response_time': i, 'max_response_time': i,
                 'throughput': 1, 'error_rate': 0, 'request_count': 10} for i in range(30)]
    prompt = build_analysis_prompt({'timeline_segments': timeline}, {}, 'load')
    assert 'segment-29' in prompt
    assert '500→' not in prompt
    assert 'untrusted' in TEST_ANALYZER_SYSTEM_PROMPT.lower()
    assert 'unknown' in TEST_ANALYZER_SYSTEM_PROMPT.lower()
    assert len(build_analysis_prompt({'error_details': [{'sample_messages': ['x' * 100000]}]}, {}, 'load')) < 12000


def test_version_comparison_does_not_invent_missing_or_zero_baseline_percentages():
    from app.services.insights_engine import VersionComparator
    data = VersionComparator().compare({'error_rate': 0}, {'error_rate': 2}).model_dump()
    assert len(data['diffs']) == 1
    assert data['diffs'][0]['delta_pct'] is None
    assert data['diffs'][0]['delta_absolute'] == 2
    assert data['diffs'][0]['direction'] == 'worse'
    assert 'throughput' in data['unavailable_metrics']
    json.dumps(data, allow_nan=False)


@pytest.mark.parametrize('baseline,target', [({}, {}), ({'throughput': 50}, {}),
    ({'throughput': float('inf')}, {'throughput': 10}), ({'throughput': None}, {'throughput': 1})])
def test_version_comparison_without_valid_pairs_is_unknown(baseline, target):
    from app.services.insights_engine import VersionComparator
    data = VersionComparator().compare(baseline, target).model_dump()
    assert data['overall_verdict'] == 'unknown'
    assert not data['diffs']
    json.dumps(data, allow_nan=False)


def test_version_comparison_keeps_real_percentage_and_accepts_partial_endpoint_data():
    from app.services.insights_engine import VersionComparator
    data = VersionComparator().compare({'throughput': 100, 'per_label': {'a': {'p99_response_time': None}}},
                                      {'throughput': 50, 'per_label': {'a': {'p99_response_time': 'unknown'}}}).model_dump()
    assert data['diffs'][0]['delta_pct'] == -50
    assert data['overall_verdict'] == 'minor_regression'


def test_run_comparison_formats_regressions_and_improvements():
    from types import SimpleNamespace
    from app.services.jmeter.regression_engine import RegressionEngine
    report = RegressionEngine().compare(SimpleNamespace(avg_response_time=100, throughput=10),
                                        SimpleNamespace(avg_response_time=150, throughput=20))
    assert report.overall_verdict == 'mixed'
    assert '+50.0%' in report.summary
    assert '+100.0%' in report.summary


def test_run_comparison_zero_baseline_has_absolute_change_and_invalid_data_is_unknown():
    from types import SimpleNamespace
    from app.services.jmeter.regression_engine import RegressionEngine
    engine = RegressionEngine()
    report = engine.compare(SimpleNamespace(error_rate=0), SimpleNamespace(error_rate=2))
    assert report.to_dict()['deltas'][0]['delta_pct'] is None
    assert report.to_dict()['deltas'][0]['delta_abs'] == 2
    assert report.regressions  # New errors still flag a regression without inventing a percentage.
    assert 'absolute' in report.summary
    missing = engine.compare(SimpleNamespace(throughput=float('nan')), SimpleNamespace(throughput=10))
    assert missing.overall_verdict == 'unknown'
    json.dumps(missing.to_dict(), allow_nan=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('response', ['[]', '{}', 'not JSON', '{"overall_assessment": [], "conclusion": "x"}'])
async def test_analysis_invalid_model_output_is_explicit_and_not_retried(response):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services.ai.analyzer import TestAnalyzer
    analyzer = object.__new__(TestAnalyzer)
    analyzer.adapter = analysis_adapter(AsyncMock(return_value=response))
    result = await analyzer.analyze({}, {}, 'load')
    assert result.analysis_status == 'invalid'
    assert not result.key_findings
    analyzer.adapter.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_analysis_does_not_retry_auth_error_or_expose_provider_secret(monkeypatch):
    import httpx
    import openai
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services.ai.analyzer import TestAnalyzer
    response = httpx.Response(401, request=httpx.Request('POST', 'https://example.invalid'))
    analyzer = object.__new__(TestAnalyzer)
    analyzer.adapter = analysis_adapter(AsyncMock(side_effect=openai.AuthenticationError('secret-key', response=response, body=None)))
    result = await analyzer.analyze({}, {}, 'load')
    assert result.analysis_status == 'failed'
    assert 'secret-key' not in result.model_dump_json()
    analyzer.adapter.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_analysis_retries_transient_failure_once_then_accepts_schema(monkeypatch):
    import httpx
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import app.services.ai.analyzer as module
    analyzer = object.__new__(module.TestAnalyzer)
    analyzer.adapter = analysis_adapter(AsyncMock(side_effect=[httpx.ConnectError('offline'),
        '{"overall_assessment":"Insufficient evidence", "conclusion":"Unknown"}']))
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, 'sleep', sleep)
    result = await analyzer.analyze({}, {}, 'load')
    assert result.analysis_status == 'completed'
    assert result.evidence_status == 'unverified'
    assert analyzer.adapter.chat.await_count == 2
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_analysis_sdk_retries_do_not_multiply_outer_attempts(monkeypatch):
    import httpx
    from openai import AsyncOpenAI
    from unittest.mock import AsyncMock
    import app.services.ai.analyzer as module
    from app.services.ai.adapters.openai_adapter import OpenAIAdapter
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(429, json={'error': {'message': 'fixture rate limit', 'type': 'rate_limit'}})
    adapter = object.__new__(OpenAIAdapter)
    adapter.max_tokens = 8000
    adapter.model = 'fixture'
    adapter.client = AsyncOpenAI(api_key='test-only', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    analyzer = object.__new__(module.TestAnalyzer)
    analyzer.adapter = adapter
    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock())
    try:
        result = await analyzer.analyze({}, {}, 'load')
        assert result.analysis_status == 'failed'
        assert len(requests) == 3
    finally:
        await adapter.retire()
