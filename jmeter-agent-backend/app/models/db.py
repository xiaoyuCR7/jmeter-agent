"""SQLAlchemy ORM models for persistent storage."""

from uuid import uuid4
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text,
    ForeignKey, JSON, UniqueConstraint, create_engine, event, inspect, select, insert, literal,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.core.time import utc_now_naive


class Base(DeclarativeBase):
    pass


# Additive same-host coordination schema. Runtime admission is not enabled by
# merely creating these tables; all execution entrypoints must join the protocol.
class CoordinationProtocolModel(Base):
    __tablename__ = 'coordination_protocol'

    id = Column(Integer, primary_key=True)
    protocol = Column(Integer, nullable=False)
    capacities_json = Column(Text, nullable=False)
    domain = Column(String(64), nullable=False)


class ProcessOwnerModel(Base):
    __tablename__ = 'process_owners'

    generation = Column(String(32), primary_key=True)
    pid = Column(Integer, nullable=False)
    creation_time = Column(String(100), nullable=False)  # Raw OS identity, never float seconds.
    protocol = Column(Integer, nullable=False)
    heartbeat_at = Column(Float, nullable=False)
    state = Column(String(20), nullable=False)  # active / draining / closed / dead


class CoordinationOperationModel(Base):
    __tablename__ = 'coordination_operations'

    operation_id = Column(String(200), primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    owner_generation = Column(String(32), ForeignKey('process_owners.generation'), nullable=False)
    claim_id = Column(String(32), nullable=False, unique=True)
    resources_json = Column(Text, nullable=False)
    state = Column(String(20), nullable=False)
    sideeffects_possible = Column(Boolean, nullable=False)


class ResourceLeaseModel(Base):
    __tablename__ = 'resource_leases'

    resource_key = Column(String(600), primary_key=True)
    owner_generation = Column(String(32), ForeignKey('process_owners.generation'), nullable=False)
    operation_id = Column(String(200), ForeignKey('coordination_operations.operation_id'), nullable=False)
    claim_id = Column(String(32), nullable=False)
    fence = Column(Integer, nullable=False)
    state = Column(String(20), nullable=False)


# ── User ─────────────────────────────────────────────────

class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(20), default="editor")  # admin / editor / viewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    api_tokens = relationship("ApiTokenModel", back_populates="user", cascade="all, delete-orphan")


class ApiTokenModel(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    token_hash = Column(String(256), nullable=False, unique=True)
    scopes = Column(JSON, default=list)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    user = relationship("UserModel", back_populates="api_tokens")


class AnalysisReceiptModel(Base):
    """Request identity and outcomes; never a queue or a cross-store transaction."""
    __tablename__ = "analysis_receipts"
    __table_args__ = (UniqueConstraint('owner_id', 'request_id', name='uq_analysis_receipt_owner_key'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    request_id = Column(String(64), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    execution_id = Column(String(50), nullable=False)
    generation = Column(String(32), nullable=False)
    state = Column(String(20), nullable=False, default='running')
    phase = Column(String(30), nullable=False, default='accepted')
    partial_effects_possible = Column(Boolean, nullable=False, default=False)
    archive_data = Column(JSON, nullable=True)
    result_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class AnalysisReceiptOwnerModel(Base):
    """Receipt execution identity only; never inputs, credentials or event bodies."""
    __tablename__ = 'analysis_receipt_owners'

    receipt_id = Column(Integer, ForeignKey('analysis_receipts.id', ondelete='CASCADE'), primary_key=True)
    process_generation = Column(String(32), ForeignKey('process_owners.generation'), nullable=False)
    operation_id = Column(String(200), ForeignKey('coordination_operations.operation_id'), nullable=True)
    claim_id = Column(String(32), nullable=True)


class ToolConfirmationModel(Base):
    """Short-lived encrypted decision bound to one user incarnation and claim."""
    __tablename__ = 'tool_confirmations'

    id = Column(String(32), primary_key=True)
    owner_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    owner_incarnation = Column(String(32), nullable=False)
    tool_name = Column(String(100), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    encrypted_arguments = Column(Text, nullable=False)
    process_generation = Column(String(32), ForeignKey('process_owners.generation'), nullable=False)
    operation_id = Column(String(200), ForeignKey('coordination_operations.operation_id'), nullable=False)
    claim_id = Column(String(32), nullable=False)
    state = Column(String(20), nullable=False)
    approved = Column(Boolean, nullable=True)
    expires_at = Column(Float, nullable=False, index=True)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)


class MCPSessionRouteModel(Base):
    """Short-lived owner route for one legacy MCP SSE connection."""
    __tablename__ = 'mcp_session_routes'

    id = Column(String(64), primary_key=True)
    owner_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    owner_incarnation = Column(String(32), nullable=False)
    process_generation = Column(String(32), ForeignKey('process_owners.generation'), nullable=False)
    state = Column(String(20), nullable=False)
    expires_at = Column(Float, nullable=False, index=True)
    created_at = Column(Float, nullable=False)


class MCPMessageModel(Base):
    """Encrypted POST envelope awaiting its exact SSE-owning process."""
    __tablename__ = 'mcp_session_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey('mcp_session_routes.id', ondelete='CASCADE'), nullable=False, index=True)
    encrypted_payload = Column(Text, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    created_at = Column(Float, nullable=False)


class WSWorkerSubscriptionModel(Base):
    """Per-process interest only; subscriber identities and tokens stay local."""
    __tablename__ = 'ws_worker_subscriptions'

    execution_id = Column(String(50), primary_key=True)
    process_generation = Column(String(32), ForeignKey('process_owners.generation'), primary_key=True)
    subscriber_count = Column(Integer, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)


class WSDeliveryModel(Base):
    """Encrypted, expiring event addressed to one subscribed process."""
    __tablename__ = 'ws_event_deliveries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String(50), nullable=False, index=True)
    target_generation = Column(String(32), ForeignKey('process_owners.generation'), nullable=False, index=True)
    encrypted_payload = Column(Text, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    expires_at = Column(Float, nullable=False, index=True)
    created_at = Column(Float, nullable=False)


class BusinessDataRevisionModel(Base):
    """Single monotonic invalidation token for the three legacy business caches."""
    __tablename__ = 'business_data_revision'

    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False)


# ── Project ──────────────────────────────────────────────

class ProjectModel(Base):
    __tablename__ = "projects"

    id = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    base_url = Column(String(500), default="")
    environment_data = Column(JSON, default=dict)  # serialized ProjectEnvironment
    environments_data = Column(JSON, nullable=True)  # serialized ProjectEnvironments
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive)
    version = Column(Integer, nullable=False, default=1, server_default="1")

    test_plans = relationship("TestPlanModel", back_populates="project", cascade="all, delete-orphan")
    environments = relationship("EnvironmentModel", back_populates="project", cascade="all, delete-orphan")
    baselines = relationship("BaselineModel", back_populates="project", cascade="all, delete-orphan")


class ProjectAccessPolicyModel(Base):
    __tablename__ = 'project_access_policies'

    project_id = Column(String(50), ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    mode = Column(String(20), nullable=False, default='legacy_shared')
    owner_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    version = Column(Integer, nullable=False, default=1)


class ProjectMemberModel(Base):
    __tablename__ = 'project_members'

    project_id = Column(String(50), ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    role = Column(String(20), nullable=False)


class UserAccessRevisionModel(Base):
    __tablename__ = 'user_access_revisions'

    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    incarnation = Column(String(32), nullable=False, default=lambda: uuid4().hex)


class ProjectAccessAuditModel(Base):
    """Policy identifiers and roles only; never resource content or credentials."""
    __tablename__ = 'project_access_audits'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(50), ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    actor_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    before_policy = Column(JSON, nullable=True)
    after_policy = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class EnvironmentModel(Base):
    __tablename__ = "environments"

    id = Column(String(50), primary_key=True)
    project_id = Column(String(50), ForeignKey("projects.id"), nullable=False)
    name = Column(String(100), nullable=False)
    base_url = Column(String(500), default="")
    variables = Column(JSON, default=dict)
    headers = Column(JSON, default=dict)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now_naive)

    project = relationship("ProjectModel", back_populates="environments")


# ── Test Plan ────────────────────────────────────────────

class TestPlanModel(Base):
    __tablename__ = "test_plans"

    id = Column(String(50), primary_key=True)
    project_id = Column(String(50), ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    test_type = Column(String(30), default="baseline")
    jmx_path = Column(String(500), default="")
    jmx_content = Column(Text, nullable=True)  # raw JMX XML
    test_config = Column(JSON, default=dict)
    endpoints_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive)
    version = Column(Integer, nullable=False, default=1, server_default="1")

    project = relationship("ProjectModel", back_populates="test_plans")
    executions = relationship("ExecutionModel", back_populates="test_plan", cascade="all, delete-orphan")


# ── Execution ────────────────────────────────────────────

class ExecutionModel(Base):
    __tablename__ = "executions"

    id = Column(String(50), primary_key=True)
    project_id = Column(String(50), ForeignKey("projects.id"), nullable=False, index=True)
    test_plan_id = Column(String(50), ForeignKey("test_plans.id"), nullable=True)
    status = Column(String(20), default="pending")
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    jmx_path = Column(String(500), default="")
    report_dir = Column(String(500), default="")
    log_path = Column(String(500), default="")
    error_message = Column(Text, nullable=True)
    resource_snapshot_pre = Column(JSON, nullable=True)
    resource_snapshot_post = Column(JSON, nullable=True)
    ai_analysis_data = Column(JSON, nullable=True)  # serialized ReportAnalysis
    summary_data = Column(JSON, nullable=True)       # serialized DetailedTestSummary
    source_jmx_path = Column(String(500), nullable=True)
    source_sha256 = Column(String(64), nullable=True)
    context_snapshot = Column(JSON, nullable=True)
    report_status = Column(String(20), default="pending")
    report_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    version = Column(Integer, nullable=False, default=1, server_default="1")

    test_plan = relationship("TestPlanModel", back_populates="executions")


# ── Baseline ─────────────────────────────────────────────

class BaselineModel(Base):
    __tablename__ = "performance_baselines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(50), ForeignKey("projects.id"), nullable=False, index=True)
    test_plan_id = Column(String(50), ForeignKey("test_plans.id"), nullable=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    execution_id = Column(String(50), nullable=True)  # which run this baseline is based on
    thresholds = Column(JSON, default=dict)  # {"p99_max": 500, "error_rate_max": 0.01}
    metrics_snapshot = Column(JSON, default=dict)  # snapshot of key metrics
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now_naive)

    project = relationship("ProjectModel", back_populates="baselines")


# ── Schedule ─────────────────────────────────────────────

class ScheduleModel(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(50), ForeignKey("projects.id"), nullable=False, index=True)
    test_plan_id = Column(String(50), ForeignKey("test_plans.id"), nullable=True)
    name = Column(String(200), nullable=False)
    cron_expression = Column(String(100), nullable=False)  # e.g. "0 */6 * * *"
    is_enabled = Column(Boolean, default=True)
    jmx_path = Column(String(500), default="")  # resolved at schedule time
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    run_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive)


class SchedulePrincipalModel(Base):
    """Missing row means historical job; a deleted creator must not mean legacy."""
    __tablename__ = 'schedule_principals'

    schedule_id = Column(Integer, ForeignKey('schedules.id', ondelete='CASCADE'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    incarnation = Column(String(32), nullable=False)


class ScheduleDispatchModel(Base):
    """Permanent batch ledger, including abandoned and uncertain executions.

    Deliberately no schedule FK: deleting a schedule must not erase deduplication.
    This table is not a queue and no state authorizes replay after a crash.
    """
    __tablename__ = 'schedule_dispatches'
    __table_args__ = (UniqueConstraint('schedule_id', 'planned_at', name='uq_schedule_dispatch_batch'),)

    execution_id = Column(String(50), primary_key=True)
    schedule_id = Column(Integer, nullable=False)
    planned_at = Column(DateTime, nullable=False)
    owner_generation = Column(String(32), ForeignKey('process_owners.generation'), nullable=False)
    operation_id = Column(String(200), ForeignKey('coordination_operations.operation_id'), nullable=False)
    claim_id = Column(String(32), nullable=False)
    state = Column(String(20), nullable=False)  # committed / running / completed / abandoned / unknown
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


# ── Database Engine ──────────────────────────────────────

from app.core.config import settings

# SQLite with WAL mode for concurrent reads
_engine = None
_async_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        db_url = settings.DATABASE_URL
        _engine = create_async_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        )
        # Enable WAL mode on SQLite
        if "sqlite" in db_url:
            @event.listens_for(_engine.sync_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
    return _engine


def get_session_factory() -> async_sessionmaker:
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _async_session_factory


async def init_db():
    """Create all tables on startup."""
    engine = get_engine()
    from app.services.startup_lock import startup_migration_lock
    async with startup_migration_lock(engine), engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Inspect before adding compatible nullable/defaulted columns. Real
        # migration failures must surface instead of masquerading as duplicates.
        def migrate(connection):
            additions = {
                "executions": {
                    "summary_data": "JSON", "source_jmx_path": "VARCHAR(500)",
                    "source_sha256": "VARCHAR(64)",
                    "context_snapshot": "JSON",
                    "report_status": "VARCHAR(20) DEFAULT 'pending'",
                    "report_error": "TEXT",
                    "version": "INTEGER NOT NULL DEFAULT 1",
                },
                "projects": {"environments_data": "JSON", "version": "INTEGER NOT NULL DEFAULT 1"},
                "test_plans": {"version": "INTEGER NOT NULL DEFAULT 1"},
            }
            for table, columns in additions.items():
                existing = {column["name"] for column in inspect(connection).get_columns(table)}
                for name, declaration in columns.items():
                    if name not in existing:
                        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        await conn.run_sync(migrate)
        # Existing projects retain authenticated workspace sharing. Never infer
        # an owner, overwrite a private policy, or rewrite project contents.
        missing = select(
            ProjectModel.id, literal('legacy_shared'), literal(None), literal(1),
        ).where(~select(ProjectAccessPolicyModel.project_id).where(
            ProjectAccessPolicyModel.project_id == ProjectModel.id,
        ).exists())
        await conn.execute(insert(ProjectAccessPolicyModel).from_select(
            ['project_id', 'mode', 'owner_id', 'version'], missing,
        ))
        if await conn.scalar(select(BusinessDataRevisionModel.id).where(BusinessDataRevisionModel.id == 1)) is None:
            await conn.execute(insert(BusinessDataRevisionModel).values(id=1, revision=0))


async def get_db() -> AsyncSession:
    """Dependency: yield an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
