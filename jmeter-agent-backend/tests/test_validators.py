"""Tests for FactValidator and MetricClaimExtractor."""

import os
import pytest

from app.services.ai.memory.validators import (
    MetricClaimExtractor, FactValidator, HypothesisExtractor,
)
from app.services.ai.memory.fact_ledger import FactLedger


@pytest.fixture
def sample_jtl():
    return os.path.join(os.path.dirname(__file__), "fixtures", "sample.jtl")


@pytest.fixture
def fact(sample_jtl):
    return FactLedger.compute_from_jtl(
        jtl_path=sample_jtl,
        execution_id="exec-val",
        module="api",
        version="v1",
    )


def test_extract_metric_claims():
    text = """
    本次测试 p99 为 250ms，平均响应时间为 195ms。
    吞吐量 TPS 为 1.0 req/s，错误率为 20%。
    总请求数 1000。
    """
    extractor = MetricClaimExtractor()
    claims = extractor.extract(text)

    by_name = {c.metric_name: c for c in claims}
    assert "p99" in by_name
    assert "avg_response_time" in by_name
    assert "throughput" in by_name
    assert "error_rate" in by_name
    assert by_name["p99"].claimed_value == 250.0
    assert by_name["avg_response_time"].claimed_value == 195.0


def test_validator_accepts_correct_numbers(fact):
    validator = FactValidator()
    text = f"""
    本次测试平均响应时间为 {fact.avg_response_time}ms，
    p99 为 {fact.percentiles['p99']}ms，
    吞吐量为 {fact.throughput} req/s，
    错误率为 {fact.error_rate}%。
    """
    result = validator.validate(text, fact)
    assert result.is_valid is True
    assert len(result.mismatches) == 0


def test_validator_rejects_wrong_numbers(fact):
    validator = FactValidator()
    text = f"""
    本次测试 p99 为 999ms（实际应为 {fact.percentiles['p99']}ms），
    吞吐量为 100 req/s（实际应为 {fact.throughput}）。
    """
    result = validator.validate(text, fact)
    assert result.is_valid is False
    assert len(result.mismatches) >= 2

    mismatch_names = {m["metric"] for m in result.mismatches}
    assert "p99" in mismatch_names
    assert "throughput" in mismatch_names


def test_missing_fact_field_cannot_validate_a_numeric_claim(fact):
    fact.percentiles = {}
    result = FactValidator().validate('p99 为 123ms', fact)
    assert not result.is_valid
    assert 'p99' in result.missing_fields


def test_validator_handles_seconds_conversion(fact):
    """Claims in seconds should be converted to ms for response time metrics."""
    validator = FactValidator()
    # fact.avg_response_time is ~205ms => 0.205s
    text = f"平均响应时间为 {fact.avg_response_time / 1000.0}s"
    result = validator.validate(text, fact)
    assert result.is_valid is True


def test_hypothesis_extractor():
    text = """
    整体评估：系统在高并发下表现不佳。
    假设：瓶颈在数据库连接池。
    初步判断：p99 恶化是因为锁竞争。
    建议：增加连接池大小。
    """
    extractor = HypothesisExtractor()
    hyps = extractor.extract(text, execution_id="exec-001", module="api", version="v1")

    statements = {h.statement for h in hyps}
    assert any("连接池" in s for s in statements)
    assert any("锁竞争" in s for s in statements)
