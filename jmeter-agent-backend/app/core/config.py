from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import Optional, List, Dict, Any, Literal
import os
import json

# 路径配置文件
PATHS_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "paths_config.json")


def load_paths_config() -> Dict[str, str]:
    """加载用户自定义的路径配置"""
    if os.path.exists(PATHS_CONFIG_FILE):
        try:
            with open(PATHS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_paths_config(config: Dict[str, str]):
    """保存用户自定义的路径配置"""
    with open(PATHS_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


class Settings(BaseSettings):
    APP_NAME: str = "JMeter Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    
    # Server
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    ALLOWED_HOSTS: List[str] = ["localhost", "127.0.0.1", "::1"]

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./jmeter_agent.db"

    # Auth
    JWT_SECRET: str = "change-me-in-production-use-env-var"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60       # access token: 1 hour
    JWT_REFRESH_EXPIRE_DAYS: int = 7   # refresh token: 7 days
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "admin123"
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173",
                               "http://localhost:3000", "http://127.0.0.1:3000"]

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_GLOBAL: str = "200/minute"    # global API rate limit
    RATE_LIMIT_AUTH: str = "10/minute"       # login endpoint rate limit
    RATE_LIMIT_AI: str = "30/minute"         # AI/LLM endpoints rate limit
    RATE_LIMIT_MCP: str = "60/minute"        # MCP server rate limit    
    # Paths
    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    JMETER_HOME: str = os.path.join(os.path.dirname(BASE_DIR), "bin")
    JMETER_EXECUTION_MODE: Literal['local', 'container'] = 'local'
    JMETER_CONTAINER_IMAGE: str = ''  # Operator-provided, locally available image@sha256:digest.
    JMETER_CONTAINER_USER: str = '65534:65534'
    JMETER_CONTAINER_MEMORY_MB: int = 2048
    JMETER_CONTAINER_CPUS: float = 2.0
    
    # Default paths (can be overridden by user config)
    TEST_PLANS_DIR_DEFAULT: str = os.path.join(BASE_DIR, "test_plans")
    REPORTS_DIR_DEFAULT: str = os.path.join(BASE_DIR, "reports")
    BACKUPS_DIR: str = os.path.join(os.path.dirname(BASE_DIR), "backups")
    AI_MODELS_CONFIG_FILE: str = os.path.join(BASE_DIR, "ai_models_config.json")
    TEST_DATA_MAX_UPLOAD_BYTES: int = Field(default=100 * 1024 * 1024, gt=0)
    
    @property
    def TEST_PLANS_DIR(self) -> str:
        """获取测试计划目录（优先使用用户配置）"""
        paths_config = load_paths_config()
        return paths_config.get("test_plans_dir", self.TEST_PLANS_DIR_DEFAULT)
    
    @property
    def REPORTS_DIR(self) -> str:
        """获取报告目录（优先使用用户配置）"""
        paths_config = load_paths_config()
        return paths_config.get("reports_dir", self.REPORTS_DIR_DEFAULT)
    
    # AI Models
    DEFAULT_AI_MODEL: str = "openai"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: Optional[str] = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o"
    
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    
    QWEN_API_KEY: Optional[str] = None
    QWEN_API_BASE: Optional[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-max"
    
    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_API_BASE: Optional[str] = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    
    # JMeter
    JMETER_TIMEOUT: int = 3600  # seconds

    # Resource monitoring thresholds (CPU/memory usage %)
    # Tests will wait or be deferred when usage exceeds these thresholds
    RESOURCE_CPU_THRESHOLD: float = 80.0       # CPU usage % — above this, tests wait
    RESOURCE_MEMORY_THRESHOLD: float = 85.0    # Memory usage % — above this, tests wait
    RESOURCE_COOLDOWN_SECONDS: float = 10.0    # Minimum gap between consecutive tests
    RESOURCE_MAX_WAIT_SECONDS: float = 300.0   # Max time to wait for resources to free up
    RESOURCE_MAX_CONCURRENT_TESTS: int = 2     # Max number of JMeter tests running simultaneously

    # Agent
    AGENT_TOOL_TIMEOUT_SECONDS: float = 600.0    # max seconds per tool call (10 min)
    AGENT_MAX_CONCURRENT: int = 3                # max concurrent agent executions
    ANALYSIS_MAX_CONCURRENT: Optional[int] = None  # registered pipeline; None inherits AGENT_MAX_CONCURRENT
    AGENT_CONTEXT_TOKEN_BUDGET: int = 16384      # input + schemas + reserved output; set to model capacity
    AGENT_MAX_TOOL_CALLS_PER_RESPONSE: int = 8

    @field_validator('ANALYSIS_MAX_CONCURRENT', mode='before')
    @classmethod
    def validate_analysis_concurrency(cls, value):
        if value is None:
            return value
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                raise ValueError('ANALYSIS_MAX_CONCURRENT must be a positive integer') from None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError('ANALYSIS_MAX_CONCURRENT must be a positive integer')
        return value

    # Conversation Memory Persistence
    MEMORY_PERSIST_ENABLED: bool = True
    MEMORY_DB_PATH: str = ""                     # empty = auto-generate under BASE_DIR

    # RAG Store — LanceDB
    RAG_LANCEDB_PATH: str = ""                   # empty = auto-generate under BASE_DIR
    RAG_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"  # sentence-transformers model for embeddings
    RAG_EMBEDDING_DEVICE: str = "cpu"            # cpu / cuda
    RAG_EMBEDDING_MODEL_PATH: str = ""           # local model path — set to avoid huggingface download

    # i18n
    I18N_DEFAULT_LANG: str = "zh-CN"             # default language
    I18N_ENABLED_LANGS: str = "zh-CN,en"         # comma-separated enabled languages

    # CI/CD integration
    WEBHOOK_URL: Optional[str] = None            # URL to POST test completion notifications
    WEBHOOK_ON_COMPLETE: bool = True             # Fire webhook on test completion
    WEBHOOK_ON_FAILURE: bool = True              # Fire webhook on test failure

    # Trae IDE Connection Configuration
    TRAE_API_BASE: Optional[str] = "http://localhost:8000"  # Trae IDE MCP server base URL
    TRAE_API_KEY: Optional[str] = None                        # Trae API authentication key
    TRAE_CONNECTION_TIMEOUT: int = 30                         # Connection timeout in seconds
    TRAE_READ_TIMEOUT: int = 120                              # Read timeout in seconds
    TRAE_DATA_FORMAT: str = "json"                            # Data format: json / jsonrpc
    TRAE_PROTOCOL_VERSION: str = "2024-11-05"                 # MCP protocol version for Trae

    # Codex (Claude Code) Connection Configuration
    CODEX_API_BASE: Optional[str] = None                      # Codex API base URL
    CODEX_API_KEY: Optional[str] = None                       # Codex API authentication key
    CODEX_CONNECTION_TIMEOUT: int = 30                        # Connection timeout in seconds
    CODEX_READ_TIMEOUT: int = 120                             # Read timeout in seconds
    CODEX_DATA_FORMAT: str = "json"                           # Data format: json / jsonrpc
    CODEX_PROTOCOL_VERSION: str = "2024-11-05"                # MCP protocol version for Codex

    # JMeter JVM configuration
    JMETER_HEAP_MIN: str = "256m"                # -Xms initial heap
    JMETER_HEAP_MAX: str = "2g"                  # -Xmx max heap
    JMETER_GC_OPTS: str = "-XX:+UseG1GC -XX:MaxGCPauseMillis=100"  # GC options
    JMETER_EXTRA_OPTS: str = ""                  # Additional JVM options
    # Distributed testing — remote engine hosts
    JMETER_REMOTE_ENGINES: str = ""        # Comma-separated host:port list, e.g. "slave1:1099,slave2:1099"
    JMETER_REMOTE_SSL_ENABLED: bool = False # Whether to use SSL for RMI connections
    JMETER_DISTRIBUTED_MAX_ENGINES: int = 4 # Max concurrent remote engines
    
    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()

# Ensure directories exist
os.makedirs(settings.TEST_PLANS_DIR, exist_ok=True)
os.makedirs(settings.REPORTS_DIR, exist_ok=True)


def validate_deployment_security():
    if not settings.DEBUG or settings.HOST not in ('127.0.0.1', 'localhost', '::1'):
        if settings.JWT_SECRET == 'change-me-in-production-use-env-var' or len(settings.JWT_SECRET) < 32:
            raise RuntimeError('Set a strong JWT_SECRET before production or non-loopback binding')
        if settings.DEFAULT_ADMIN_PASSWORD == 'admin123':
            raise RuntimeError('Change DEFAULT_ADMIN_PASSWORD before production or non-loopback binding')


def safe_path(user_path: str, allowed_dirs: list[str] = None) -> str:
    """Resolve and validate a path is within allowed directories. Prevents path traversal.

    Args:
        user_path: The path to validate (relative or absolute).
        allowed_dirs: List of allowed base directories. Defaults to TEST_PLANS_DIR + REPORTS_DIR.

    Returns:
        Resolved absolute path if safe.

    Raises:
        ValueError: If path escapes allowed directories.
    """
    if allowed_dirs is None:
        allowed_dirs = [
            settings.TEST_PLANS_DIR,
            settings.REPORTS_DIR,
        ]
    resolved = os.path.realpath(os.path.abspath(user_path))
    for base in allowed_dirs:
        base_resolved = os.path.realpath(os.path.abspath(base))
        try:
            if os.path.normcase(os.path.commonpath([resolved, base_resolved])) == os.path.normcase(base_resolved):
                from app.services.project_scope import require_path
                require_path(resolved)
                return resolved
        except ValueError:
            continue
    raise ValueError(f"Access denied: path '{user_path}' is outside allowed directories")


def load_user_model_configs() -> List[Dict[str, Any]]:
    """从 JSON 文件加载用户自定义模型配置"""
    if os.path.exists(settings.AI_MODELS_CONFIG_FILE):
        try:
            with open(settings.AI_MODELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_user_model_configs(configs: List[Dict[str, Any]]):
    """保存用户自定义模型配置到 JSON 文件"""
    with open(settings.AI_MODELS_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(configs, f, ensure_ascii=False, indent=2)


def get_all_model_configs() -> List[Dict[str, Any]]:
    """获取所有模型配置（仅用户自定义，本项目不内置模型）。"""
    return load_user_model_configs()


def sanitize_model_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the model config with the API key redacted.

    The key is never sent to the frontend; it is only stored on disk and
    used server-side when building adapters.
    """
    safe = dict(config)
    if safe.get("api_key"):
        safe["api_key"] = "***"
    return safe
