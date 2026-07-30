"""FastAPI application factory with lifespan management."""
from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = structlog.get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan handler.

    Startup:
      - Initialize database connection pool
      - Initialize tool registry (auto-discovery)
      - Connect to MCP servers

    Shutdown:
      - Close all connections cleanly
    """
    settings = get_settings()

    # ------------------------------------------------------------------ Startup
    logger.info("startup.begin", env=settings.APP_ENV)

    # 1. Init DB pool
    try:
        from app.db.session import init_engine

        await init_engine(
            settings.DATABASE_URL,
            echo=(settings.LOG_LEVEL == "DEBUG"),
        )
        logger.info("startup.db_pool_ready")
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup.db_pool_failed", error=str(exc))

    # 2. Init tool registry (auto-discovery)
    try:
        from app.tools.registry import get_registry

        registry = get_registry()
        registry.discover()
        logger.info("startup.tool_registry_ready", tools=len(registry))
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup.tool_registry_failed", error=str(exc))

    # 3. Connect MCP servers
    try:
        if settings.VEROSIGHT_ENABLED and settings.VEROSIGHT_MODE in ("mcp", "both"):
            from app.mcp.manager import get_mcp_manager

            mcp_manager = get_mcp_manager()
            await mcp_manager.connect_all()
            logger.info("startup.mcp_connected")
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup.mcp_connect_failed", error=str(exc))

    logger.info("startup.complete")
    yield

    # ----------------------------------------------------------------- Shutdown
    logger.info("shutdown.begin")

    # Close MCP connections
    try:
        from app.mcp.manager import get_mcp_manager

        mcp_manager = get_mcp_manager()
        await mcp_manager.close_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("shutdown.mcp_close_failed", error=str(exc))

    # Close DB pool
    try:
        from app.db.session import close_engine

        await close_engine()
        logger.info("shutdown.db_pool_closed")
    except Exception as exc:  # noqa: BLE001
        logger.warning("shutdown.db_close_failed", error=str(exc))

    logger.info("shutdown.complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Agentic Loop",
        description=(
            "A research agent that thinks, executes tools, and stops "
            "when it has a verified final answer."
        ),
        version="0.1.0",
        docs_url="/docs" if settings.APP_ENV == "local" else None,
        redoc_url="/redoc" if settings.APP_ENV == "local" else None,
        lifespan=lifespan,
    )

    # Middleware registration (Order: RequestID -> RateLimit -> Duration Logging -> CORS)
    from app.api.middleware import logging_middleware, rate_limit_middleware, request_id_middleware

    app.middleware("http")(logging_middleware)
    app.middleware("http")(rate_limit_middleware)
    app.middleware("http")(request_id_middleware)

    # CORS (allow localhost for local development)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000", "http://127.0.0.1:8080"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register global exception handlers
    from app.api.exceptions import register_exception_handlers

    register_exception_handlers(app)

    # Include routes
    _register_routes(app)

    return app


def _register_routes(app: FastAPI) -> None:
    """Register all API routers."""
    from app.api.routes.agent import router as agent_router
    from app.api.routes.conversations import router as conversations_router
    from app.api.routes.health import router as health_router
    from app.api.routes.metrics import router as metrics_router
    from app.api.routes.runs import router as runs_router
    from app.api.routes.tools import router as tools_router

    app.include_router(agent_router)
    app.include_router(runs_router)
    app.include_router(conversations_router)
    app.include_router(tools_router)
    app.include_router(health_router)
    app.include_router(metrics_router)


# Module-level app instance (used by uvicorn)
app = create_app()
