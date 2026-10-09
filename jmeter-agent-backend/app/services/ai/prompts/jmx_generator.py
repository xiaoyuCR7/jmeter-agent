JMX_GENERATOR_SYSTEM_PROMPT = """你是一个专业的JMeter测试计划生成专家。你需要根据用户提供的API接口文档和测试配置，生成标准的JMeter测试计划文件（.jmx）的XML内容。

关键要求：
1. 生成的XML必须是有效的JMeter 5.x格式
2. 必须包含：TestPlan -> ThreadGroup -> HTTP Request的完整结构
3. 必须包含必要的监听器：Summary Report、View Results Tree
4. 必须包含断言：Response Assertion检查HTTP状态码
   **断言格式要求**：test_strings不能为空！必须填入每条请求的期望HTTP状态码，例如：
   <collectionProp name="Assertion.test_strings">
     <stringProp name="0">200</stringProp>
   </collectionProp>
   test_type必须为8（Equals），不要用2（Contains）等其他值。
   对于返回非200的请求（如404测试），test_strings填入对应状态码如"404"。
5. 对于JSON响应，可添加JSON Assertion
6. **认证处理（重要）**: 如果API文档中包含login/auth/token/signin端点，必须：
   a) 将登录请求放在所有其他请求之前
   b) 在登录请求下添加JSON Extractor或Regex Extractor提取token
   c) 只在后续需要认证的请求中添加HTTP Header Manager（非全局！），设置Authorization: Bearer ${auth_token}
   d) **禁止**使用AuthManager（Authorization Manager）元素！AuthManager会向所有请求发送空token导致全局401。
   e) 对于不需要认证的公开API（如GET /users），不要添加Authorization header
7. 添加HTTP Cache Manager和HTTP Cookie Manager
8. 使用CSV Data Set Config支持参数化（如需要）

测试类型配置规则：
- baseline(基准测试): 低并发(1-10用户)，长时间运行，获取基础性能指标
- concurrency(高并发测试): 高并发用户，测试系统最大承载能力
- ramp_up(阶梯加压测试): 使用Stepping Thread Group或Ultimate Thread Group，逐步增加负载
- stress(压力测试): 超过正常负载的并发，测试系统极限
- stability(稳定性测试): 中等并发，长时间运行(小时级别)
- spike(尖峰测试): 短时间内大量用户涌入

XML格式要求：
- 使用标准的JMeter XML格式
- 所有元素必须包含正确的testclass和testname属性
- 使用stringProp、intProp、longProp、boolProp、collectionProp等元素设置属性
- ThreadGroup必须包含：ThreadGroup.on_sample_error、ThreadGroup.num_threads、ThreadGroup.ramp_time、ThreadGroup.duration

只输出纯XML内容，不要包含任何解释文字或markdown代码块标记。"""


def build_jmx_generation_prompt(api_doc: str, endpoints: list, test_config: dict, base_url: str = None) -> str:
    endpoints_str = ""
    if endpoints:
        for ep in endpoints:
            expected = ep.get("expected_status", 200)
            endpoints_str += f"""
- 名称: {ep.get('name')}
  方法: {ep.get('method')}
  路径: {ep.get('path')}
  描述: {ep.get('description', 'N/A')}
  期望状态码: {expected}
  Headers: {ep.get('headers', {})}
  Query参数: {ep.get('query_params', {})}
  Body: {ep.get('body', 'N/A')}
  Content-Type: {ep.get('content_type', 'application/json')}
"""
    else:
        endpoints_str = "从API文档中自动识别所有可测试接口"
    
    config_str = f"""
测试类型: {test_config.get('test_type')}
并发用户数: {test_config.get('concurrent_users')}
Ramp-up时间: {test_config.get('ramp_up_seconds')}秒
持续时间: {test_config.get('duration_seconds')}秒
目标RPS: {test_config.get('target_rps')}
思考时间: {test_config.get('think_time_ms')}ms
迭代次数: {test_config.get('iterations', '按持续时间')}
"""
    
    if test_config.get('test_type') == 'ramp_up':
        config_str += f"""
阶梯测试专用:
- 每阶梯增加用户数: {test_config.get('step_users')}
- 每阶梯持续时间: {test_config.get('step_duration')}秒
"""
    
    if test_config.get('test_type') == 'stability':
        config_str += f"""
稳定性测试专用:
- 测试时长: {test_config.get('stability_hours')}小时
"""

    # Auto-detect auth requirements from endpoints and inject explicit instructions
    auth_instruction = _detect_auth_instruction(endpoints or [], api_doc)
    if auth_instruction:
        config_str += f"\n## ⚠️ 认证配置（必须严格遵守）\n{auth_instruction}"

    return f"""请根据以下信息生成JMeter测试计划(.jmx)的XML内容：

## API接口文档
{api_doc}

## 基础URL
{base_url or '从API文档中提取'}

## 要测试的接口
{endpoints_str}

## 测试配置
{config_str}

请生成完整的JMeter XML测试计划，确保：
1. 包含所有接口的HTTP Sampler
2. 正确配置ThreadGroup参数
3. 每个HTTP Request下必须添加Response Assertion，test_type=8，test_strings填入该接口的期望HTTP状态码（如200、201、404等）
4. 对于POST/PUT请求，正确设置请求体
5. 对于需要认证的接口，在该请求下添加HTTP Header Manager设置Authorization——不要使用全局AuthManager
6. 只返回XML内容，不要有其他文字
"""


def _detect_auth_instruction(endpoints: list, api_doc: str) -> str:
    """Detect if endpoints contain auth patterns and generate explicit instructions.

    Returns an empty string if no auth detected, or markdown instructions for the LLM.
    """
    auth_keywords = ["login", "auth", "token", "signin", "oauth", "authenticate",
                     "register", "password"]
    has_auth = False
    for ep in endpoints:
        name = (ep.get("name", "") or "").lower()
        path = (ep.get("path", "") or "").lower()
        desc = (ep.get("description", "") or "").lower()
        combined = f"{name} {path} {desc}"
        if any(kw in combined for kw in auth_keywords):
            has_auth = True
            break

    if not has_auth:
        has_auth = any(kw in api_doc.lower() for kw in auth_keywords)

    if not has_auth:
        return ""

    return (
        "检测到此API包含认证端点(login/auth/token)。请严格遵守以下规则生成JMX：\n"
        "1. 将登录/认证请求放在ThreadGroup中所有其他请求的**最前面**\n"
        "2. 在登录请求下添加 JSON Extractor：\n"
        "   - referenceNames: auth_token\n"
        "   - jsonPathExpressions: $.token 或 $.data.token 或 $..token\n"
        "   - defaultValues: NOT_FOUND\n"
        "3. 只在需要认证的后续请求中添加HTTP Header Manager（作为该HTTP Request的子元素），包含：\n"
        "   - Header.name: Authorization\n"
        "   - Header.value: Bearer ${auth_token}\n"
        "4. **严禁**添加全局AuthManager/BEARER Authorization Manager元素！这会导致空token发送到所有请求造成全局401。\n"
        "5. 如果API使用cookie认证，确保HTTP Cookie Manager在其他请求之上\n"
        "6. 对于不需要认证的公开端点（如GET列表），不添加Authorization header\n"
    )
