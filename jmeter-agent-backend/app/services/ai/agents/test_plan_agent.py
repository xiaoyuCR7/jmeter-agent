"""TestPlanAgent — generate, validate, and iterate on JMX test plans."""

from typing import Optional

from app.services.ai.adapters.base import BaseAIAdapter
from app.services.ai.agent.base import BaseAgent
from app.services.ai.agent.tool_registry import ToolRegistry
from app.services.ai.agent.tools import get_all_builtin_tools


TEST_PLAN_SYSTEM_PROMPT = """JMeter test plan generator agent. Extract APIs from docs, detect dependencies, generate .jmx files.

## Dependency detection rules
- login/auth/token/signin endpoints → extract token via JSON Extractor (ref_name: auth_token)
- Subsequent requests must use Authorization: Bearer ${auth_token}
- Extract types: json_path ($.data.token), regex ("token":"([^"]+)"), header (Set-Cookie)
- Order endpoints: no-dependency first, dependent later

## Test type params
- baseline: 5-10 users, 10-30min | concurrency: 100-1000 users, 5min
- ramp_up: step 20-50 users, 10min+ | stress: 1000+ users, 2-5min
- stability: 50-200 users, 1hr+ | spike: 2000+ users, 1-2min

## Workflow
Understand requirements → analyze deps → create_jmx → validate_jmx → fix if needed → report.
Report: jmx path, config summary, endpoint list with deps, extraction rules, next steps."""


class TestPlanAgent(BaseAgent):
    """Specialized agent for generating and validating JMX test plans.

    Usage:
        adapter = AIAdapterFactory.create("openai")
        agent = TestPlanAgent(adapter)
        result = await agent.run(
            "为 https://httpbin.org 的 GET /get 和 POST /post 接口生成基准测试"
        )
    """

    def __init__(
        self,
        adapter: BaseAIAdapter,
        tools: Optional[ToolRegistry] = None,
        session_id: Optional[str] = None,
    ):
        if tools is None:
            tools = ToolRegistry()
            tools.register_many(get_all_builtin_tools())

        super().__init__(
            name="TestPlanAgent",
            adapter=adapter,
            tools=tools,
            system_prompt=TEST_PLAN_SYSTEM_PROMPT,
            max_iterations=12,
            session_id=session_id,
        )

    async def generate_from_doc(
        self,
        api_doc: str,
        test_type: str = "baseline",
        base_url: str = "",
        **test_params,
    ) -> dict:
        """Generate a test plan from API documentation.

        Args:
            api_doc: API documentation text (OpenAPI, Swagger, or free text).
            test_type: Type of test.
            base_url: Base URL of the target system.
            **test_params: Override default test parameters (concurrent_users, duration_seconds, etc.)
        """
        task = f"""请根据以下API文档生成测试计划：

测试类型: {test_type}
基础URL: {base_url}
自定义参数: {test_params or '使用默认值'}

API文档:
{api_doc}

请执行以下步骤：
1. 从文档中提取所有API接口（HTTP方法、路径、参数等）
2. 使用 create_jmx 工具为提取的接口生成测试计划
3. 使用 validate_jmx 验证生成的测试计划
4. 报告结果和文件路径"""

        result = await self.run(task)
        return {
            "success": result.success,
            "output": result.output,
            "steps": len(result.plan),
            "iterations": result.iterations,
            "error": result.error,
        }
