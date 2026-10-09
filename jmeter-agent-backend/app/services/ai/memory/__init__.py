"""Anti-pollution memory system for JMeter Agent.

Exports:
- fact_ledger: deterministic ground-truth metrics from JTL
- hypothesis_ledger: falsifiable interpretation ledger
- fact_validator: validates LLM numeric claims against facts
- hypothesis_extractor: extracts structured hypotheses from LLM text
- heuristics: cold-start domain priors
"""

from app.services.ai.memory.fact_ledger import FactLedger, fact_ledger
from app.services.ai.memory.hypothesis_ledger import HypothesisLedger, hypothesis_ledger
from app.services.ai.memory.validators import (
    FactValidator,
    MetricClaimExtractor,
    HypothesisExtractor,
    fact_validator,
    claim_extractor,
    hypothesis_extractor,
)
from app.services.ai.memory.heuristics import (
    PERFORMANCE_HEURISTICS,
    get_heuristics_prompt,
    get_relevant_heuristics,
)

__all__ = [
    "FactLedger",
    "fact_ledger",
    "HypothesisLedger",
    "hypothesis_ledger",
    "FactValidator",
    "MetricClaimExtractor",
    "HypothesisExtractor",
    "fact_validator",
    "claim_extractor",
    "hypothesis_extractor",
    "PERFORMANCE_HEURISTICS",
    "get_heuristics_prompt",
    "get_relevant_heuristics",
]
