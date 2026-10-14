"""FastAPI sidecar entrypoint.

Startup imports this module only. torch and FlagEmbedding stay unloaded.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import chat, clarify, collect, connectors, indexing, library, search, settings, stats, verify
from app.db.session import SIDECAR_ROOT, init_db
from app.llm.registry import ProviderRegistry

_config_dir = os.getenv("CONFIG_DIR", str(SIDECAR_ROOT / "config"))
registry = ProviderRegistry.load(_config_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(os.getenv("DATA_DIR"))
    app.state.registry = registry
    yield


app = FastAPI(title="zhihu-connect-engine sidecar", version="0.1.0", lifespan=lifespan)
app.state.registry = registry


@app.middleware("http")
async def auth(request: Request, call_next):
    expect = os.getenv("SIDECAR_TOKEN")
    if expect and request.headers.get("x-sidecar-token") != expect:
        return JSONResponse(status_code=401, content={"detail": "bad token"})
    return await call_next(request)


app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(collect.router, prefix="/collect", tags=["collect"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(library.router, prefix="/library", tags=["library"])
app.include_router(settings.router, tags=["settings"])
app.include_router(stats.router, prefix="/stats", tags=["stats"])
app.include_router(indexing.router, tags=["index"])
app.include_router(clarify.router, prefix="/clarify", tags=["clarify"])
app.include_router(verify.router, prefix="/verify", tags=["verify"])
app.include_router(connectors.router, prefix="/connectors", tags=["connectors"])


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "version": app.version,
        "data_dir": os.getenv("DATA_DIR"),
        "db": "ready",
        "models": {"embedder": "not_loaded", "reranker": "not_loaded"},
    }
