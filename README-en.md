# JMeter Agent

AI-Powered JMeter Performance Testing Platform - Supports multiple AI models, automated test plan generation, and intelligent report analysis

## Project Overview

JMeter Agent is an AI-integrated performance testing platform built on Apache JMeter. The platform aims to simplify the performance testing workflow by leveraging AI technologies to automatically generate test plans, intelligently analyze test results, and provide natural language interaction capabilities.

### Core Value

- **AI Automation Testing**: Automatically generate JMeter test plans through natural language descriptions or API documents
- **Intelligent Report Analysis**: AI-driven test report analysis to automatically identify performance bottlenecks and issues
- **Multi-model Support**: Supports OpenAI, Claude, Qwen, DeepSeek, and other AI models
- **Project Management**: Multi-project, multi-environment test resource management
- **Real-time Monitoring**: WebSocket-based real-time test execution progress monitoring
- **Internationalization**: Support for Chinese/English interface switching

---

## Features

### Test Plan Management

- AI automated test plan generation (supports OpenAPI document import)
- JMX file visual editor
- Multiple test templates: Baseline, Concurrency, Ramp-up, Stress, Stability, Spike
- Test plan version management and backup

### Test Execution

- Background asynchronous test execution
- Real-time progress monitoring (WebSocket)
- Support for cancelling running tests
- Distributed testing support

### AI Analysis Capabilities

- Intelligent report analysis: automatically identify performance issues, resource bottlenecks, error root causes
- Anomaly detection and metric correlation analysis
- Cost estimation and performance drift detection
- Cross-version comparison analysis

### Natural Language Interaction

- AI chat assistant: supports querying test data, analyzing reports, answering JMeter questions
- AI workflow: automatically create projects and test cases from API documents
- Auto-tune: intelligently optimize test configurations

### Project & Environment Management

- Multi-project support
- Multi-environment configuration (development, testing, production)
- Environment variables and header management

### Scheduling & Baselines

- Scheduled test task scheduling
- Performance baseline management
- Automatic test result comparison

---

## Project Structure

```
jmeter-agent/
├── bin/                          # JMeter binaries and configuration
│   ├── jmeter.bat / jmeter.sh    # JMeter startup scripts
│   ├── jmeter.properties         # JMeter configuration file
│   └── report-template/          # Report templates
├── lib/                          # JMeter dependencies
│   ├── ext/                      # JMeter plugin extensions
│   └── *.jar                     # Third-party dependencies
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
├── backups/                      # Test plan backups
├── rag_lancedb/                  # RAG vector database
├── start.bat / start.sh          # One-click startup scripts
└── LICENSE                       # License
```

---

## Installation Guide

### Requirements

- **Python**: 3.11+
- **Node.js**: 20+
- **Java**: 11+ (required for JMeter)

### Quick Start

#### Method 1: Use One-click Startup Script (Recommended)

```bash
# Windows
start.bat

# Linux/Mac
./start.sh
```

#### Method 2: Manual Installation

**1. Install Backend Dependencies**

```bash
cd jmeter-agent-backend
pip install -r requirements.txt
```

**2. Install Frontend Dependencies**

```bash
cd jmeter-agent-frontend
npm install
```

**3. Configure Environment Variables**

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

# JWT secret (must be changed in production)
JWT_SECRET=your-secure-jwt-secret
DEFAULT_ADMIN_PASSWORD=your-admin-password
```

**4. Start Services**

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

### Login

- Default username: `admin`
- Default password: `admin123` (please change after first login)

### Create Test Plan

1. Select or create a project in the left project tree
2. Click "New Test Case"
3. Select a test template (Baseline, Concurrency, etc.)
4. Configure test parameters (concurrent users, duration, ramp-up time, etc.)
5. Input API endpoint information or import OpenAPI document
6. Click "Generate" button, AI will automatically generate the test plan

### Execute Test

1. Select the test plan to execute from the test plan list
2. Click "Execute" button
3. Optional: select AI model for analysis
4. View real-time test progress on the execution page

### View Report

1. After test completion, click the report link
2. View:
   - Test overview (response time, throughput, error rate)
   - AI analysis report (automatically identified issues and optimization suggestions)
   - JMeter native HTML report
   - Execution logs

### AI Chat

1. Click "AI Chat" in the left navigation bar
2. You can:
   - Ask test-related questions
   - Query test data
   - Get performance optimization suggestions
   - Auto-create projects from API documents via "AI Workflow"

---

## Configuration Details

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Backend service port | 8000 |
| `DEBUG` | Debug mode | true |
| `JWT_SECRET` | JWT signing secret | change-me-in-production |
| `JWT_EXPIRE_MINUTES` | Access Token expiration time | 60 |
| `DEFAULT_ADMIN_USERNAME` | Default admin username | admin |
| `DEFAULT_ADMIN_PASSWORD` | Default admin password | admin123 |
| `DEEPSEEK_API_KEY` | DeepSeek API key | - |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `QWEN_API_KEY` | Qwen API key | - |
| `MEMORY_PERSIST_ENABLED` | Conversation memory persistence | true |
| `RATE_LIMIT_ENABLED` | API rate limiting | true |
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
- Ensure Java 11+ is installed
- Check if `jmeter.bat` or `jmeter.sh` exists in `bin/` directory
- Or set `JMETER_HOME` environment variable to JMeter installation directory

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

## Analysis of Potential Redundant/Obsolete Files

The following files may be redundant, temporary, or test artifacts. Consider cleaning them:

### 1. Compilation Cache Files

| File/Directory | Type | Description |
|----------------|------|-------------|
| `**/__pycache__/` | Compilation cache | Python compiled `.pyc` files, auto-generated at runtime, not needed for version control |
| `jmeter-agent-backend/.pytest_cache/` | Test cache | pytest test cache, can be safely deleted |

### 2. Log Files

| File | Description |
|------|-------------|
| `.codegraph/daemon.log` | CodeGraph tool log |
| `jmeter-agent-backend/server_stdout.log` | Backend service log |
| `jmeter-agent-backend/server_stderr.log` | Backend error log |
| `jmeter-agent-backend/reports/**/jmeter.log` | JMeter execution logs (keep only recent test records) |
| `jmeter.log` | JMeter log at root directory |
| `bin/jmeter.log` | JMeter sample log |

### 3. Temporary Test Files

| File | Description |
|------|-------------|
| `jmeter-agent-backend/test_exec_id.txt` | Temporary test execution ID file |
| `jmeter-agent-backend/test_plan_id.txt` | Temporary test plan ID file |
| `jmeter-agent-backend/test_project_id.txt` | Temporary project ID file |

### 4. Development Tool Configurations

| File/Directory | Description |
|----------------|-------------|
| `.claude/` | Claude IDE configuration |
| `.codegraph/` | CodeGraph tool configuration and database |
| `.impeccable/` | Impeccable code quality tool configuration |

### 5. Backup Files

| Directory | Description |
|-----------|-------------|
| `backups/` | Test plan backups (historical backups can be cleaned as needed) |

### 6. RAG Database Files

| Directory | Description |
|-----------|-------------|
| `rag_lancedb/` | LanceDB vector database (required in production, can be cleaned in development) |

### 7. Dependency Files

| File | Description |
|------|-------------|
| `jmeter-agent-backend/requirements.in` | Human-maintained direct runtime dependencies |
| `jmeter-agent-backend/requirements.txt` | Generated exact runtime dependency lock |
| `jmeter-agent-backend/requirements-dev.in` | Human-maintained development and test tools |
| `jmeter-agent-backend/requirements-dev.txt` | Generated exact development dependency lock |

### Recommended Actions

1. **Add `.gitignore`**: Ensure the above files are not tracked by version control
2. **Clean Cache**: Periodically clean `__pycache__` and `.pytest_cache`
3. **Log Management**: Set up log rotation policy to avoid oversized log files
4. **Backup Strategy**: Periodically clean expired backup files

---

## License

[Apache License 2.0](LICENSE)

---

## Contact

For questions or suggestions, please contact us through:

- Submit an Issue
- Send an email
