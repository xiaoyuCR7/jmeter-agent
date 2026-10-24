# JMeter Agent

A local, requirement-driven AI agent for JMeter performance testing

## Project Overview

JMeter Agent is a locally deployed, single-user, **requirement-driven** performance testing agent built on Apache JMeter 5.6.3. The JMeter runtime is **not bundled in this repository**; install it separately (see [Installation Guide](#installation-guide)) before running real load tests.

It closes the loop of AI-assisted performance testing:

1. **Test document → requirements**: AI extracts quantifiable requirements (TPS, P90/P95/P99, error rate, concurrency, duration) as a reviewable draft. Every item quotes its source sentence, and the quote is checked against the document.
2. **Requirements → test plan**: the documented load profile prefills new plans; AI generates and validates the JMX.
3. **Run → deterministic evidence**: every run gets per-requirement verdicts, a comparison with the baseline and the previous run (overall and per transaction), and Prometheus server metrics for the run window (saturation, correlation with response time).
4. **Evidence → AI analysis**: conclusions must cite requirement IDs and measured values; anything unsupported is marked as a hypothesis with a way to verify it.

The same evidence is exposed over MCP, so Claude Code, Cursor and similar assistants see exactly what the report page shows.

---

## Features

### Performance requirements

- Paste a document or load a .txt / .md file; AI drafts requirements and flags quotes it could not find in the text
- Overall and per-transaction requirements (matched by sampler label; the worst matching label decides)
- The load profile carries over into new test plans

### Test plans and execution

- AI test plan generation (natural language or OpenAPI import) and a visual JMX tree editor
- Templates: baseline, concurrency, ramp-up, stress, stability, spike
- Background execution with WebSocket progress and cancellation; concurrent JMeter runs are capped so they do not skew each other
- SelfHeal repairs extraction rules broken by API changes (with confirmation)

### Evidence and analysis

- "Verdict & evidence" panel on every report: requirement table, history comparison, server metrics plotted against response time
- Streaming JTL parser with exact percentiles and memory independent of sample count
- One-click "set as baseline"; baselines keep a metrics snapshot
- Prometheus presets for host, JVM + HikariCP and MySQL; collected per run window and cached
- Fact-sheet-driven AI analysis with number validation, a hypothesis ledger and a RAG knowledge base

### MCP

- Streamable HTTP (`/api/v1/mcp`, recommended) and legacy SSE (`/api/v1/mcp/sse`)
- Evidence tools such as `get_execution_evidence`, `extract_performance_requirements`, `save_performance_requirements`, `promote_execution_to_baseline`; writes require confirmation in the UI
- Prompts: `requirements-to-plan`, `analyze-execution`

### Local single-user mode

- No login or accounts; the server only binds to loopback addresses
- Host and Origin checks still stop other websites in your browser from calling the local API

---

## Project Structure

```
jmeter-agent/
├── jmeter-agent-backend/         # Backend service (Python/FastAPI)
│   ├── app/
│   │   ├── api/                  # API routes
│   │   ├── core/                 # Core configuration and utilities
│   │   ├── i18n/                 # Internationalization translations
│   │   ├── middleware/           # Middleware (rate limiting, concurrency control)
│   │   ├── models/               # Data models
│   │   └── services/             # Business services
│   │       ├── ai/               # AI services (model adapters, analysis, agents)
│   │       └── jmeter/           # JMeter services (executor, parser)
│   ├── tests/                    # Unit tests
│   ├── requirements.txt          # Dependency list
│   └── .env.example              # Environment variable examples
├── jmeter-agent-frontend/        # Frontend application (Vue 3/TypeScript)
│   ├── src/
│   │   ├── api/                  # API client
│   │   ├── router/               # Router configuration
│   │   ├── store/                # State management (Pinia)
│   │   ├── utils/                # Utility functions
│   │   └── views/                # Page components
│   └── package.json              # Frontend dependencies
├── docs/                         # Project documentation
├── start.bat / start.sh          # One-click startup scripts
└── LICENSE                       # License
```

> **Directories not included in the repository** (excluded via `.gitignore`):
> - `bin/`, `lib/` — Apache JMeter 5.6.3 runtime; install it yourself following the [Installation Guide](#installation-guide)
> - `backups/`, `rag_lancedb/`, `jmeter-agent-backend/test_plans/`, `jmeter-agent-backend/reports/` — runtime-generated backups, vector store, and test artifacts

---

## Installation Guide

### Requirements

- **Operating system**: Windows or Linux (macOS is not supported yet: the multi-process coordination layer verifies local filesystems and process identity only on these two platforms)
- **Python**: 3.11+
- **Node.js**: 20+
- **Java**: 11+ (required by JMeter; verify with `java -version`)
- **Apache JMeter**: 5.6.3 (**not bundled**; install as described below; not required for running unit tests)

### Prepare the JMeter Runtime

Download `apache-jmeter-5.6.3` from the [Apache JMeter downloads page](https://jmeter.apache.org/download_jmeter.cgi), then make it discoverable by the backend in one of these ways:

**Option A: Extract into the project root (zero configuration, recommended)**

Extract the `bin/` and `lib/` folders from the archive into the project root (alongside `jmeter-agent-backend/`):

```
jmeter-agent/
├── bin/jmeter.bat          # Windows; bin/jmeter on Linux
├── lib/
└── jmeter-agent-backend/
```

**Option B: Set the `JMETER_HOME` environment variable**

Point it to JMeter's `bin` directory (pointing to the JMeter installation root also works):

```bash
# Windows (persistent)
setx JMETER_HOME "C:\apache-jmeter-5.6.3\bin"

# Linux (add to ~/.bashrc or ~/.zshrc)
export JMETER_HOME=/opt/apache-jmeter-5.6.3/bin
```

**Option C: Add it to your system `PATH`**

Add `apache-jmeter-5.6.3/bin` to `PATH`; the executor will discover `jmeter` / `jmeter.bat` automatically.

> Verify with `jmeter --version`. JMeter is only needed for executing real load tests and generating HTML reports — viewing/editing test plans and running the unit test suites do not require it.

### Quick Start

#### Method 1: Use One-click Startup Script (Recommended)

> Prerequisite: complete [Prepare the JMeter Runtime](#prepare-the-jmeter-runtime) above and ensure Java 11+ is available.

```bash
# Windows
start.bat

# Linux/Mac
./start.sh
```

#### Method 2: Manual Installation

**1. Prepare the JMeter Runtime**

Follow [Prepare the JMeter Runtime](#prepare-the-jmeter-runtime) above.

**2. Install Backend Dependencies**

```bash
cd jmeter-agent-backend
pip install -r requirements.txt
```

**3. Install Frontend Dependencies**

```bash
cd jmeter-agent-frontend
npm install
```

**4. Configure Environment Variables**

Copy `.env.example` to `.env` and configure:

```bash
cd jmeter-agent-backend
cp .env.example .env
```

Edit `.env` file, configure AI model API keys:

```env
# Configure at least one AI model
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# Optional configurations
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
QWEN_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

```

**5. Start Services**

```bash
# Start backend (port 8000)
cd jmeter-agent-backend
python -m app.main

# Start frontend (port 3000)
cd jmeter-agent-frontend
npm run dev
```

### Access URLs

- **Frontend Interface**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/api/docs

---

## Usage Guide

### No Login Required

This project is meant for private local deployment: there is no login or account system. The server refuses to bind to anything but a loopback address such as `127.0.0.1`; do not expose it publicly.

### Layout

The sidebar has three entries: **Projects**, **Runs** and **Settings**, plus a "Start from a document" button for new projects.

- **Projects**: one workspace per project, with Overview (loop setup and recent runs), Requirements, Test plans, Baselines and Settings (basics, environments, server monitoring) tabs.
- **Runs**: every run across projects in one table, filterable by project and status, showing the requirement verdict, key metrics and AI analysis status.
- **Settings**: AI models, assistants (MCP), system paths, appearance and language.

### Start from a document

1. Click "Start from a document"
2. Upload or paste an API / requirements document and describe what to test (for example "checkout at 500 concurrent users for 10 minutes, P95 within 1 s"), or just give an OpenAPI URL (sample: `reqres-openapi.yaml`)
3. When the project and plans are ready, click "Review requirements": your text is prefilled for extraction; check the AI draft and save it

### Create and edit test plans

1. On a project's Test plans tab, click "New test plan" and pick a template (baseline, concurrency, ramp-up, ...)
2. Concurrency and duration are prefilled from the requirements load profile
3. The editor has a form view and a JMX tree view; switching saves the current view only if it changed

### Execute tests

1. Click "Run" on a test plan
2. The Runs page shows live progress, logs and a cancel button
3. Runs queue automatically when the load generator is busy; the top bar shows its CPU / memory

### View reports

A report starts with the requirement verdict and key metrics, then tabs:

- Verdict & evidence: per-requirement results, comparison with the baseline and the previous run (with "Set as baseline"), Prometheus server metrics
- Trends: response time, throughput and error rate over time, plus percentiles
- Transactions and Errors
- AI analysis (downloadable as Markdown); the header also offers the JTL file and JMeter's HTML report

---

## Configuration Details

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Backend service port | 8000 |
| `DEBUG` | Debug mode | true |
| `DEEPSEEK_API_KEY` | DeepSeek API key | - |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `QWEN_API_KEY` | Qwen API key | - |
| `JMETER_HOME` | Directory containing the JMeter executable (its `bin` dir or the JMeter install root) | `<project-root>/bin` |
| `MEMORY_PERSIST_ENABLED` | Conversation memory persistence | true |
| `RESOURCE_CPU_THRESHOLD` | CPU usage threshold (%) | 80 |
| `RESOURCE_MEMORY_THRESHOLD` | Memory usage threshold (%) | 85 |

### JMeter JVM Configuration

```env
JMETER_HEAP_MIN=256m
JMETER_HEAP_MAX=2g
JMETER_GC_OPTS=-XX:+UseG1GC -XX:MaxGCPauseMillis=100
```

### Path Configuration

The system automatically creates the following directories:
- `test_plans/` - Test plan storage
- `reports/` - Test report storage
- `backups/` - Backup files

---

## API Interface

### Core API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/test-plans` | Get test plan list |
| POST | `/api/v1/test-plans/generate` | AI generate test plan |
| POST | `/api/v1/test-plans/{id}/execute` | Execute test plan |
| GET | `/api/v1/executions` | Get execution records |
| GET | `/api/v1/executions/{id}/report` | Get test report |
| POST | `/api/v1/ai/chat` | AI chat |
| GET | `/api/v1/projects` | Get project list |
| POST | `/api/v1/projects` | Create project |
| GET | `/api/v1/ai/models` | Get AI model list |
| GET/PUT | `/api/v1/projects/{id}/requirements` | Read / save performance requirements |
| POST | `/api/v1/projects/{id}/requirements/extract` | AI requirement draft from document text |
| GET/PUT | `/api/v1/projects/{id}/monitoring` | Prometheus configuration |
| GET | `/api/v1/executions/{id}/evidence` | Requirement verdicts, history comparison, server metrics |
| POST | `/api/v1/executions/{id}/promote-baseline` | Set a run as the baseline |
| POST | `/api/v1/mcp` | MCP Streamable HTTP endpoint |

Full API documentation: http://localhost:8000/api/docs

---

## Contribution Guide

### Development Environment Setup

1. Fork the project
2. Clone to local: `git clone https://github.com/your-username/jmeter-agent.git`
3. Install dependencies:
   - Backend development: `pip install -r jmeter-agent-backend/requirements-dev.txt`
   - Frontend: `npm install`
4. Create feature branch: `git checkout -b feature/your-feature`

### Code Standards

- **Python**: Follow PEP8 standards, use `black` formatter
- **TypeScript**: Follow ESLint standards
- **Commit Messages**: Use semantic commit format

### Testing

```bash
# Backend tests
cd jmeter-agent-backend
pytest tests

# Frontend tests
cd jmeter-agent-frontend
npm run test
```

### Submit PR

1. Ensure all tests pass
2. Update documentation if needed
3. Create Pull Request and describe the changes

---

## Troubleshooting

### Common Issues

#### 1. JMeter Not Found

**Symptom**: Startup error `JMeter executable not found`

**Solution**:
- Ensure Java 11+ is installed (`java -version`)
- Make sure Apache JMeter 5.6.3 is installed (see [Prepare the JMeter Runtime](#prepare-the-jmeter-runtime)). The backend locates it via any one of:
  - `bin/jmeter.bat` (Windows) or `bin/jmeter` (Linux) exists in the project root
  - `JMETER_HOME` points to JMeter's `bin` directory (or its installation root)
  - JMeter's `bin` directory is on your system `PATH` (verify with `jmeter --version`)

#### 2. AI Model Connection Failed

**Symptom**: Test report analysis fails or AI chat has no response

**Solution**:
- Check if API Key in `.env` file is correctly configured
- Ensure network access to AI model services
- Test model connection in settings page

#### 3. Port Already in Use

**Symptom**: Startup fails with port already in use error

**Solution**:
- Use `PORT` environment variable to specify another port:
  ```bash
  # Windows
  set PORT=8001
  start.bat
  
  # Linux/Mac
  PORT=8001 ./start.sh
  ```

#### 4. Test Execution Timeout

**Symptom**: Test execution takes too long or no response

**Solution**:
- Increase `JMETER_TIMEOUT` environment variable (unit: seconds)
- Check heap memory settings in JMeter configuration
- Check if target service is running normally

#### 5. Frontend Cannot Connect to Backend

**Symptom**: Frontend page shows connection error

**Solution**:
- Ensure backend service is started
- Check proxy configuration in frontend `vite.config.ts`
- Confirm firewall is not blocking port access

### Log Viewing

```bash
# Backend logs
tail -f jmeter-agent-backend/server_stdout.log
tail -f jmeter-agent-backend/server_stderr.log

# JMeter execution logs (per test)
view reports/{execution_id}/jmeter.log
```

---

## License

[Apache License 2.0](LICENSE)

---

## Contact

For questions or suggestions, please open an Issue.
