# JMeter Agent

AI 驱动的 JMeter 性能测试平台 —— 支持多 AI 模型、自动生成测试计划、智能分析报告、MCP 协议集成

> English version: [README-en.md](README-en.md)

---

## 目录

- [项目概述](#项目概述)
- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [安装指南](#安装指南)
- [使用说明](#使用说明)
- [配置详情](#配置详情)
- [API 接口](#api-接口)
- [CI/CD 集成](#cicd-集成)
- [开发指南](#开发指南)
- [故障排除](#故障排除)
- [许可证](#许可证)

---

## 项目概述

JMeter Agent 是一个集成了人工智能能力的性能测试平台，基于 Apache JMeter 5.6.3 构建。JMeter 运行时**不包含在仓库内**，执行真实压测前需自行准备（安装方式见[安装指南](#安装指南)）。平台通过 AI 技术自动生成测试计划、智能分析测试结果，并提供自然语言交互、MCP 协议集成等能力，旨在大幅简化性能测试流程。

---

## 功能特性

### 测试计划管理

- AI 自动生成测试计划（支持自然语言描述与 OpenAPI 文档导入）
- JMX 文件可视化树形编辑器（组件注册表 + 函数注册表）
- 多种测试模板：基准测试、高并发测试、阶梯加压、压力测试、稳定性测试、尖峰测试
- 测试计划版本管理与自动备份

### 测试执行

- 后台异步执行测试
- 实时进度监控（WebSocket 推送）
- 支持取消正在运行的测试
- 资源保护：CPU / 内存阈值监控，超阈值自动排队等待，限制最大并发测试数
- 分布式测试执行器支持
- SLA 校验引擎与回归检测引擎

### AI 分析能力

- 智能报告分析：自动识别性能问题、资源瓶颈、错误根因
- 异常检测与指标关联分析（ML 分析引擎）
- 成本估算与性能漂移检测
- 跨版本对比分析（基线对比）
- 分析报告导出下载

### AI 对话与 Agent

- AI 对话助手：查询测试数据、分析报告、解答 JMeter 问题（支持对话记忆持久化）
- AI 工作流：从 API 文档自动创建项目和测试用例（SSE 流式输出）
- 自动调优（AutoTune）：智能优化测试配置参数
- RAG 知识库：基于 LanceDB 向量数据库，支持文档检索、历史归档、模块对比

### 项目与环境管理

- 多项目支持
- 多环境配置（开发、测试、生产）
- 环境变量和 Header 管理，支持激活环境切换
- 测试数据文件上传与预览
- 界面支持中英文切换

### 定时任务与基线

- 定时测试任务调度（支持启用 / 停用切换）
- 性能基线管理
- 自动对比测试结果与基线

### 用户与权限

- JWT 认证（Access Token + Refresh Token）
- 多用户管理与角色控制
- 登录接口独立限流

### MCP 协议集成

- 内置 MCP Server（SSE + 消息端点）
- 支持 Trae IDE、Codex 等 AI 工具直接调用平台能力

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+ / FastAPI / Uvicorn / SQLAlchemy 2.0 (async) / SQLite (aiosqlite) |
| AI 接入 | openai SDK / anthropic SDK / 自定义 OpenAI 兼容适配器 |
| 向量检索 | LanceDB（RAG 知识库） |
| 认证 | JWT (PyJWT) + bcrypt (passlib) |
| 前端 | Vue 3 / TypeScript / Vite 6 / Element Plus / Pinia / Vue Router / ECharts 6 |
| 测试引擎 | Apache JMeter 5.6.3（需自行安装，见[安装指南](#安装指南)） |
| 测试 | pytest（后端） / Vitest（前端） |

---

## 项目结构

```
jmeter-agent/
├── jmeter-agent-backend/         # 后端服务（Python / FastAPI）
│   ├── app/
│   │   ├── api/                  # API 路由（认证、测试计划/执行、AI、RAG、系统、MCP）
│   │   ├── core/                 # 配置、i18n、通用工具
│   │   ├── middleware/           # 限流与并发控制
│   │   ├── models/               # 数据库模型与 Pydantic 模式
│   │   └── services/
│   │       ├── ai/               # 模型适配、Agent、RAG、报告分析
│   │       └── jmeter/           # 测试执行、JMX 构建/解析、SLA、调度
│   ├── tests/                    # 后端测试（pytest）
│   ├── requirements*.txt         # 依赖清单与锁文件
│   └── .env.example              # 环境变量模板
├── jmeter-agent-frontend/        # 前端应用（Vue 3 / TypeScript / Vite）
│   ├── src/                      # 页面、组件、API 客户端、状态管理
│   └── tests/                    # 前端测试（Vitest）
├── .github/workflows/            # CI：质量门禁与性能门禁
├── docs/                         # 项目文档（修复记录）
├── start.bat / start.sh          # 一键启动脚本
├── reqres-openapi.yaml           # OpenAPI 导入示例
├── gitlab-ci-example.yml         # GitLab CI 集成示例
└── LICENSE                       # Apache 2.0
```

> **不包含在仓库中的目录**（由 `.gitignore` 排除）：
> - `bin/`、`lib/` —— Apache JMeter 5.6.3 运行时，需按[安装指南](#安装指南)自行准备
> - `backups/`、`rag_lancedb/`、`jmeter-agent-backend/test_plans/`、`jmeter-agent-backend/reports/` —— 运行时自动生成的备份、向量库与测试产物

---

## 安装指南

### 环境要求

- **操作系统**: Windows 或 Linux（暂不支持 macOS：多进程协调层只在这两个平台上校验本地文件系统和进程身份）
- **Python**: 3.11+
- **Node.js**: 20+
- **Java**: 11+（JMeter 运行依赖，使用 `java -version` 验证）
- **Apache JMeter**: 5.6.3（仓库**不内置**，按下述步骤安装；仅运行单元测试时不需要）

### 准备 JMeter 运行时

从 [Apache JMeter 下载页](https://jmeter.apache.org/download_jmeter.cgi) 下载 `apache-jmeter-5.6.3` 压缩包，任选一种方式让后端能定位到它：

**方式 A：解压到项目根目录（零配置，推荐）**

将压缩包内的 `bin/` 和 `lib/` 解压到本项目根目录（与 `jmeter-agent-backend/` 同级），最终结构为：

```
jmeter-agent/
├── bin/jmeter.bat          # Windows；Linux 为 bin/jmeter
├── lib/
└── jmeter-agent-backend/
```

**方式 B：设置 `JMETER_HOME` 环境变量**

指向 JMeter 的 `bin` 目录（指向 JMeter 安装根目录也可识别）：

```bash
# Windows（永久生效）
setx JMETER_HOME "C:\apache-jmeter-5.6.3\bin"

# Linux（写入 ~/.bashrc 或 ~/.zshrc）
export JMETER_HOME=/opt/apache-jmeter-5.6.3/bin
```

**方式 C：加入系统 `PATH`**

将 `apache-jmeter-5.6.3/bin` 加入 `PATH`，执行器会自动发现 `jmeter` / `jmeter.bat`。

> 安装后可用 `jmeter --version` 验证。仅查看/编辑测试计划、运行前后端单元测试不需要 JMeter；执行真实压测和生成 HTML 报告时必须安装。

### 快速开始

#### 方式一：一键启动脚本（推荐）

> 前置条件：已完成上述 [JMeter 运行时准备](#准备-jmeter-运行时)，且 Java 11+ 可用。

```bash
# Windows
start.bat

# Linux
./start.sh
```

脚本会自动检测端口占用，并分别启动后端（8000）与前端（3000）服务。

#### 方式二：手动安装

**1. 准备 JMeter 运行时**

按上文 [准备 JMeter 运行时](#准备-jmeter-运行时) 完成安装。

**2. 安装后端依赖**

```bash
cd jmeter-agent-backend
pip install -r requirements.txt
```

**3. 安装前端依赖**

```bash
cd jmeter-agent-frontend
npm install
```

**4. 配置环境变量**

```bash
cd jmeter-agent-backend
cp .env.example .env
```

编辑 `.env`，至少配置一个 AI 模型的 API Key：

```env
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 可选：其他模型
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
QWEN_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 生产环境务必修改
JWT_SECRET=your-secure-jwt-secret
DEFAULT_ADMIN_PASSWORD=your-admin-password
```

**5. 启动服务**

```bash
# 后端（端口 8000）
cd jmeter-agent-backend
python -m app.main

# 前端（端口 3000）
cd jmeter-agent-frontend
npm run dev
```

### 访问地址

- **前端界面**：http://localhost:3000
- **后端 API**：http://localhost:8000
- **API 文档（Swagger）**：http://localhost:8000/api/docs

---

## 使用说明

### 免登录访问

本项目面向本地私人部署，无需登录：未携带凭证的请求自动以默认管理员（`DEFAULT_ADMIN_USERNAME`，默认 `admin`）身份执行。CLI/MCP 客户端仍可携带 `Authorization: Bearer <token>`，此时按该令牌校验。请只绑定在 `127.0.0.1` 等回环地址上，不要暴露到公网。

### 创建测试计划

1. 在左侧项目树中选择或创建项目
2. 点击"新建测试用例"
3. 选择测试模板（基准测试、高并发测试等）
4. 配置测试参数（并发用户数、持续时间、Ramp-up 时间等）
5. 输入 API 端点信息，或导入 OpenAPI 文档（示例文件：`reqres-openapi.yaml`）
6. 点击"生成"，AI 将自动生成测试计划
7. 如需微调，使用 JMX 可视化编辑器编辑组件树

### 执行测试

1. 在测试计划列表中选择计划，点击"执行"
2. 可选：选择用于分析的 AI 模型
3. 在执行页面通过 WebSocket 实时查看进度
4. 系统会在资源占用超阈值时自动排队，可在系统页面查看资源状态

### 查看报告

测试完成后可查看：

- 测试概览（响应时间、吞吐量、错误率）
- AI 分析报告（问题识别与优化建议，支持下载）
- JMeter 原生 HTML 报告
- Dashboard 图表数据与执行日志

### AI 对话与工作流

1. 点击左侧导航"AI 对话"
2. 支持：询问测试相关问题、查询测试数据、获取性能优化建议
3. 通过"AI 工作流"从 API 文档一键创建项目 + 测试用例
4. 通过"自动调优"让 AI 优化测试配置

### MCP 集成

在 Trae / Codex 等支持 MCP 的工具中配置连接本平台的 MCP Server（端点见 API 接口表），即可让外部 AI 工具直接调用平台的测试执行、报告查询等能力。

---

## 配置详情

### 环境变量

常用配置如下（**生产环境务必修改** `JWT_SECRET` 与默认管理员密码；服务绑定非本机地址时同样必须修改）：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `HOST` / `PORT` | 后端监听地址与端口 | `127.0.0.1` / `8000` |
| `DEBUG` | 调试模式 | `true` |
| `DATABASE_URL` | 数据库连接 | `sqlite+aiosqlite:///./jmeter_agent.db` |
| `JWT_SECRET` | JWT 签名密钥（生产必改） | `change-me-in-production-use-env-var` |
| `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` | 默认管理员账号与密码 | `admin` / `admin123` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `QWEN_API_KEY` / `DEEPSEEK_API_KEY` | 各模型 API Key（至少配置一个），配套 `*_API_BASE`、`*_MODEL` 可选 | - |
| `DEFAULT_AI_MODEL` | 默认 AI 模型 | `openai` |
| `JMETER_HOME` | JMeter 可执行文件目录（`bin` 或安装根目录均可） | `<项目根>/bin` |
| `JMETER_TIMEOUT` | 单次测试超时（秒） | `3600` |
| `RESOURCE_CPU_THRESHOLD` / `RESOURCE_MEMORY_THRESHOLD` | 触发排队的 CPU / 内存阈值（%） | `80` / `85` |
| `RESOURCE_MAX_CONCURRENT_TESTS` | 最大并发测试数 | `2` |

> Token 有效期、限流（`RATE_LIMIT_*`）、Agent 并发与超时、对话记忆开关等完整配置见 [.env.example](jmeter-agent-backend/.env.example)。AI 模型也可以在系统"设置"页面通过界面管理（存储于运行时生成的 `ai_models_config.json`）。

### JMeter JVM 配置

```env
JMETER_HEAP_MIN=256m
JMETER_HEAP_MAX=2g
JMETER_GC_OPTS=-XX:+UseG1GC -XX:MaxGCPauseMillis=100
```

### 路径配置

系统运行时自动创建以下目录（可在系统设置中修改存储路径）：

- `jmeter-agent-backend/test_plans/` —— 测试计划存储
- `jmeter-agent-backend/reports/` —— 测试报告存储
- `backups/` —— 备份文件

---

## API 接口

所有接口统一前缀 `/api/v1`（MCP SSE 除外），需认证的接口通过 `Authorization: Bearer <token>` 访问。

| 模块 | 代表端点 | 能力 |
|------|----------|------|
| 认证与用户 | `/auth/login`、`/auth/refresh`、`/auth/users` | JWT 登录刷新、用户与角色管理 |
| 测试计划 | `/test-plans`、`/test-plans/generate`、`/test-plans/{id}/jmx/*` | AI 生成、JMX 组件树编辑、原文查看与下载 |
| 测试执行 | `/test-plans/{id}/execute`、`/executions/{id}`（`cancel`、`report`、`html-report`、`dashboard-data`、`log`） | 异步执行、取消、实时进度、报告与日志 |
| 项目与环境 | `/projects`、`/projects/{id}/environments` | 多项目、多环境与激活环境切换 |
| AI 与 Agent | `/ai/models`、`/ai/workflow/stream`、`/agent/run`、`/agent/autotune`、`/openapi/import` | 模型配置与连通性测试、Agent、自动调优、OpenAPI 导入 |
| 分析 / RAG / 基线 / 调度 | `/analysis/reports`、`/rag/*`、`/baselines`、`/schedules`、`/data/*` | 报告分析下载、知识库检索、基线对比、定时任务、数据文件 |
| 系统与 MCP | `/system/*`、`/jmeter/components`、`/jmeter/functions`、`/mcp/sse`、`/mcp/messages` | 系统状态与资源、JMeter 注册表、MCP 接入 |

完整的请求 / 响应 schema 与在线调试见交互式文档：http://localhost:8000/api/docs

---

## CI/CD 集成

仓库的 `.github/workflows/quality.yml` 会在 Windows、Linux 上执行后端可移植测试，并在 Linux 的安全 SQLite 绑定上执行完整协调测试；同时校验 Python 依赖锁、前端依赖审计、测试与生产构建。

仓库还提供 `gitlab-ci-example.yml` 示例，展示如何在 GitLab CI 流水线中：

1. 启动 JMeter Agent 后端
2. 调用 API 执行指定测试计划
3. 等待执行完成并拉取报告
4. 基于 SLA / 错误率判定流水线成败

复制该文件为 `.gitlab-ci.yml` 并按需修改环境变量即可接入。

---

## 开发指南

### 环境设置

```bash
git clone <repo-url>
cd jmeter-agent

# 后端（开发与测试工具也使用精确锁）
cd jmeter-agent-backend && pip install -r requirements-dev.txt

# 前端
cd ../jmeter-agent-frontend && npm install
```

### 代码规范

- **Python**：遵循 PEP 8，建议使用 `black` 格式化
- **TypeScript / Vue**：遵循 ESLint 规范
- **提交信息**：使用语义化提交格式（如 `feat:` / `fix:` / `docs:`）

### 运行测试

```bash
# 后端测试
cd jmeter-agent-backend
pytest tests

# 前端测试
cd jmeter-agent-frontend
npm run test:run        # 单次运行
npm run test:coverage   # 覆盖率
```

### 前端构建

```bash
cd jmeter-agent-frontend
npm run build     # vue-tsc 类型检查 + 产物构建
npm run preview   # 本地预览构建产物
```

运行时直接依赖维护在 `requirements.in`，开发依赖维护在 `requirements-dev.in`；提交前应使用锁文件头部记录的命令重新生成对应的 `.txt` 文件。

### 提交 PR

1. Fork 项目并创建功能分支：`git checkout -b feature/your-feature`
2. 确保所有测试通过
3. 更新相关文档
4. 创建 Pull Request 并描述变更内容

---

## 故障排除

### 1. JMeter 未找到

**现象**：启动时提示 `JMeter executable not found`

**解决**：
- 确保已安装 Java 11+（`java -version` 验证）
- 确认已安装 Apache JMeter 5.6.3（见[准备 JMeter 运行时](#准备-jmeter-运行时)），后端按以下顺序定位，满足任一即可：
  - 项目根目录下存在 `bin/jmeter.bat`（Windows）或 `bin/jmeter`（Linux）
  - 环境变量 `JMETER_HOME` 指向 JMeter 的 `bin` 目录（指向其安装根目录也可）
  - JMeter 的 `bin` 目录已加入系统 `PATH`（可先用 `jmeter --version` 验证）

### 2. AI 模型连接失败

**现象**：报告分析失败或 AI 对话无响应

**解决**：
- 检查 `.env` 中 API Key 是否正确
- 确认网络可访问对应模型服务
- 在"设置"页面使用模型连通性测试功能排查

### 3. 端口被占用

**现象**：启动失败，提示端口已被占用

**解决**：使用 `PORT` 环境变量指定其他端口

```bash
# Windows
set PORT=8001
start.bat

# Linux
PORT=8001 ./start.sh
```

### 4. 测试执行超时或排队

**现象**：测试长时间不开始或无响应

**解决**：
- 增大 `JMETER_TIMEOUT`（秒）
- 检查系统资源是否超过 `RESOURCE_CPU_THRESHOLD` / `RESOURCE_MEMORY_THRESHOLD`（测试会自动排队等待资源释放，最长等待 `RESOURCE_MAX_WAIT_SECONDS`）
- 适当调大 `RESOURCE_MAX_CONCURRENT_TESTS`
- 检查目标服务是否正常运行

### 5. 前端无法连接后端

**现象**：前端页面显示连接错误

**解决**：
- 确认后端服务已启动
- 检查 `jmeter-agent-frontend/vite.config.ts` 中的代理配置
- 确认防火墙未阻止端口访问

### 日志查看

```bash
# 后端日志
tail -f jmeter-agent-backend/server_stdout.log
tail -f jmeter-agent-backend/server_stderr.log

# 单次测试的 JMeter 日志
jmeter-agent-backend/reports/{execution_id}/jmeter.log
```

---

## 许可证

[Apache License 2.0](LICENSE)

---

## 联系方式

如有问题或建议，欢迎提交 Issue。
