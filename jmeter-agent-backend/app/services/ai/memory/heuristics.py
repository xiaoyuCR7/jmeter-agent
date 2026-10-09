"""Performance Heuristics — cold-start domain priors, NOT writable memory.

These are engineering common-sense rules used when the hypothesis ledger is empty.
They guide early analysis but are never learned from or modified by LLM output.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HeuristicRule:
    """A single immutable heuristic rule."""
    condition: str
    implication: str
    rationale: Optional[str] = None


PERFORMANCE_HEURISTICS: list[HeuristicRule] = [
    HeuristicRule(
        condition="错误率突增（error_rate 从 <1% 跳升至 >5%）",
        implication="优先怀疑资源耗尽（CPU/内存/连接池/线程池）或外部依赖故障，而非代码逻辑错误",
        rationale="突发错误通常由容量上限触发，而非代码路径改变。",
    ),
    HeuristicRule(
        condition="p99 / p95 显著恶化但 p50 保持稳定",
        implication="通常指向队列、GC、连接池等待或锁竞争，而非普遍处理变慢",
        rationale="中位数稳定说明大多数请求正常，长尾恶化指向尾部阻塞。",
    ),
    HeuristicRule(
        condition="吞吐量不再随并发数线性增长",
        implication="系统已到达拐点，瓶颈可能是网络 I/O、数据库、锁或下游服务",
        rationale="线性区结束意味着某个资源成为硬约束。",
    ),
    HeuristicRule(
        condition="响应时间随时间持续上升",
        implication="怀疑内存泄漏、连接池耗尽、缓存膨胀或资源未释放",
        rationale="渐进式恶化通常与累积状态有关。",
    ),
    HeuristicRule(
        condition="高并发下 CPU 高但吞吐低",
        implication="可能存在锁竞争、频繁 GC 或低效序列化",
        rationale="CPU 高但未产生有效吞吐说明忙在无效竞争。",
    ),
    HeuristicRule(
        condition="错误集中在特定接口或 label",
        implication="优先排查该接口依赖的下游服务或数据库表",
        rationale="局部错误比全局错误更容易定位到具体组件。",
    ),
    HeuristicRule(
        condition="压测结束后错误率立刻下降",
        implication="错误与负载强相关，属于容量型问题而非功能性缺陷",
        rationale="负载相关错误多为资源或竞争问题。",
    ),
]


def get_heuristics_prompt(
    max_rules: int = 10,
    prefix: str = "## 领域启发式规则（仅作冷启动参考，不可写入知识库）\n\n",
) -> str:
    """Format heuristics as a system prompt fragment."""
    lines = [prefix]
    for i, rule in enumerate(PERFORMANCE_HEURISTICS[:max_rules], 1):
        lines.append(f"{i}. **当 {rule.condition} 时**：{rule.implication}")
        if rule.rationale:
            lines.append(f"   - 原因：{rule.rationale}")
    lines.append("")
    lines.append("请将以上规则仅作为生成假设的启发，所有假设默认状态为 untested，需经后续测试验证。")
    return "\n".join(lines)


def get_relevant_heuristics(keywords: list[str], max_rules: int = 5) -> list[HeuristicRule]:
    """Return heuristics whose condition/implication contains any of the keywords."""
    keywords_lower = [k.lower() for k in keywords]
    matched = []
    for rule in PERFORMANCE_HEURISTICS:
        text = f"{rule.condition} {rule.implication} {rule.rationale or ''}".lower()
        if any(kw in text for kw in keywords_lower):
            matched.append(rule)
        if len(matched) >= max_rules:
            break
    return matched
