import warnings

warnings.filterwarnings(
    "ignore",
    message=r"Field \"model_name\" in .+ has conflict with protected namespace \"model_\".",
    category=UserWarning,
)

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import logging
from contextlib import asynccontextmanager, AsyncExitStack
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.project_boundary import AuthorizedReportFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.api import routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - 替代 on_startup/on_shutdown"""
    from app.core.config import validate_deployment_security
    validate_deployment_security()
    # 启动时执行
    logger.info("Initializing database...")
    from app.models.db import init_db
    await init_db()
    logger.info("Database initialized.")

    # Sync in-memory cache from persisted DB data
    logger.info("Syncing in-memory store from database...")
    from app.services.storage import store
    from app.services.project_scope import internal_scope
    with internal_scope():
        await store.sync_from_dbstore()
    logger.info("Store synced from database.")

    # ── Security checks ──────────────────────────────────────
    if settings.JWT_SECRET == "change-me-in-production-use-env-var":
        if not settings.DEBUG:
            raise RuntimeError(
                "FATAL: JWT_SECRET is using the default value. "
                "Set JWT_SECRET environment variable before running in production mode."
            )
        logger.warning(
            "SECURITY: JWT_SECRET is using the default value. "
            "Set JWT_SECRET environment variable for production use."
        )
    if settings.DEFAULT_ADMIN_PASSWORD == "admin123":
        if not settings.DEBUG:
            raise RuntimeError(
                "FATAL: DEFAULT_ADMIN_PASSWORD is 'admin123'. "
                "Set DEFAULT_ADMIN_PASSWORD environment variable before running in production mode."
            )
        logger.warning(
            "SECURITY: DEFAULT_ADMIN_PASSWORD is using the default value. "
            "Change it via DEFAULT_ADMIN_PASSWORD env var for production use."
        )
    if "*" in settings.CORS_ORIGINS:
        raise RuntimeError("CORS_ORIGINS must list trusted origins, not '*'")

    from app.services.auth import ensure_default_admin
    await ensure_default_admin()
    from app.services.db_store import db_store
    await db_store.cleanup_expired_tokens()
    logger.info("Default admin user ensured.")

    # Unknown files have no durable owner. Startup must not reclassify an
    # orphaned private report/plan as a shared default-project resource.
    with internal_scope():
        await routes.ensure_default_project()

    # Preload RAG embedding model in background (does not block startup)
    import asyncio as _asyncio
    from app.services.ai.rag.store import _preload_embedding_model
    from app.services.scheduler import runner
    from app.api.mcp_routes import init_mcp_server
    from app.services.ws_manager import ws_manager
    from app.services.execution_lifecycle import execution_lifecycle
    from app.services.ai.adapters.factory import AIAdapterFactory
    from app.services.jmeter import resource_monitor

    async def stop_monitoring():
        if resource_monitor._monitor is not None:
            await resource_monitor._monitor.stop_monitoring()

    async with AsyncExitStack() as cleanup:
        # LIFO cleanup remains guaranteed if startup, serving, or one cleanup fails.
        cleanup.push_async_callback(AIAdapterFactory.close_all)
        cleanup.push_async_callback(stop_monitoring)
        execution_lifecycle.start()
        cleanup.push_async_callback(execution_lifecycle.shutdown)
        cleanup.push_async_callback(runner.stop)
        await runner.start()
        cleanup.push_async_callback(ws_manager.stop)
        ws_manager.start()

        preload_task = _asyncio.create_task(_preload_embedding_model())
        async def stop_preload():
            preload_task.cancel()
            await _asyncio.gather(preload_task, return_exceptions=True)
        cleanup.push_async_callback(stop_preload)
        from app.services.ai.analysis_runtime import analysis_runtime
        analysis_runtime.start()
        cleanup.push_async_callback(analysis_runtime.shutdown)
        from app.services.ai.analysis_receipts import analysis_receipts
        analysis_receipts.start()
        cleanup.push_async_callback(analysis_receipts.shutdown)
        await init_mcp_server()

        try:
            from app.services.jmeter.executor import get_executor
            executor = get_executor()
            if executor.jmeter_path:
                logger.info(f"JMeter executable found at: {executor.jmeter_path}")
        except Exception as e:
            logger.warning(f"JMeter health check failed on startup: {e}")

        yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI驱动的JMeter性能测试平台 - 支持多AI模型、自动生成测试计划、智能分析报告",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# One boundary also covers static reports and WebSocket handshakes.
from app.middleware.authentication import AuthenticationMiddleware
app.add_middleware(AuthenticationMiddleware)

# Explicit browser origins; CLI clients use Bearer authentication.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting middleware
from app.middleware.rate_limiter import RateLimitMiddleware, create_rate_limiter
app.add_middleware(RateLimitMiddleware, limiter=create_rate_limiter())

# Validate Host before deriving same-origin from it (including MCP handshakes).
from app.middleware.authentication import TrustedHostMiddleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

# WebSocket endpoint for real-time test progress
@app.websocket("/ws/executions/{execution_id}")
async def ws_execution(websocket: WebSocket, execution_id: str):
    from app.services.ws_manager import ws_manager
    await ws_manager.connect(execution_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "pong":
                    ws_manager._register_pong(websocket)
            except Exception:
                pass
    except WebSocketDisconnect:
        await ws_manager.disconnect(execution_id, websocket)
    except Exception:
        await ws_manager.disconnect(execution_id, websocket)


# Health check (must be before catch-all)
@app.get("/health")
async def health_check():
    from app.middleware.rate_limiter import create_rate_limiter
    from app.middleware.concurrency_guard import concurrency_guard
    return {
        "status": "healthy",
        "rate_limiter": create_rate_limiter().get_stats(),
        "concurrency": concurrency_guard.get_all_stats(),
    }

# API Routes
app.include_router(routes.router, prefix="/api/v1")

# Auth routes (separate module)
from app.api.auth_routes import router as auth_router
app.include_router(auth_router, prefix="/api/v1")

# Baseline routes (separate module)
from app.api.baseline_routes import router as baseline_router
app.include_router(baseline_router, prefix="/api/v1")

from app.api.project_access_routes import router as project_access_router
app.include_router(project_access_router, prefix="/api/v1")

from app.api.analysis_routes import router as analysis_router
app.include_router(analysis_router, prefix="/api/v1")

# Test data routes (separate module)
from app.api.data_routes import router as data_router
app.include_router(data_router, prefix="/api/v1")

# Schedule routes
from app.api.schedule_routes import router as schedule_router
app.include_router(schedule_router, prefix="/api/v1")

# MCP Server routes
from app.api.mcp_routes import router as mcp_router
app.include_router(mcp_router, prefix="/api/v1")

# Static files for reports
os.makedirs(settings.REPORTS_DIR, exist_ok=True)
app.mount("/reports", AuthorizedReportFiles(directory=settings.REPORTS_DIR), name="reports")

# Frontend static files
frontend_dist = os.path.join(os.path.dirname(settings.BASE_DIR), "jmeter-agent-frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

@app.get("/vite.svg")
async def vite_svg():
    svg_path = os.path.join(frontend_dist, "vite.svg")
    if os.path.exists(svg_path):
        return FileResponse(svg_path)
    return {"detail": "Not found"}

@app.get("/")
async def root():
    index_path = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/api/docs",
    }

@app.get("/{path:path}")
async def catch_all(path: str):
    # Skip API and reports paths - return proper 404
    if path.startswith("api/") or path.startswith("reports/") or path == "health" or path == "vite.svg":
        raise HTTPException(status_code=404, detail="Not found")
    index_path = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
