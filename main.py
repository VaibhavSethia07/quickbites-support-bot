"""
QuickBites Support Bot — entry point.

Start with:
    pipenv run uvicorn main:app --reload --port 8000
or:
    pipenv run python main.py
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(debug=settings.debug)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-warm expensive singletons at startup."""
    logger.info("QuickBites Support Bot starting…")

    # Pre-warm the policy RAG service (loads embedding model)
    try:
        from app.services.rag import get_rag_service

        rag = get_rag_service()
        _ = rag.full_text  # Triggers file load
        logger.info("Policy document loaded")
    except Exception as exc:
        logger.warning("Failed to pre-warm RAG service: %s", exc)

    # Pre-warm the LangGraph graph (compiles the graph)
    try:
        from app.agent.graph import get_graph

        get_graph()
        logger.info("LangGraph agent compiled and ready")
    except Exception as exc:
        logger.warning("Failed to pre-warm LangGraph agent: %s", exc)

    logger.info("Startup complete — listening on %s:%d", settings.host, settings.port)
    yield
    logger.info("Shutting down…")


app = FastAPI(
    title="QuickBites Support Bot",
    description=(
        "GenAI-powered customer support agent for QuickBites. "
        "Resolves order issues, issues refunds, files complaints, "
        "and escalates to humans when appropriate."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )
