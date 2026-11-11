from threading import BoundedSemaphore

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.auth.jwt import get_current_active_user
from app.checks.caching import CheckExecutionCache, request_execution_cache
from app.models.database import get_db
from app.models.models import User
from app.storage.graph_resolver import GraphResolver

# Neo checks only; legacy Rulebook compatibility requests run in parallel.
_EVALUATION_SLOTS = BoundedSemaphore(1)


def graph_resolver(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)):
    return GraphResolver(db, user)


async def evaluation_slot(request: Request, resolver: GraphResolver = Depends(graph_resolver)):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    graph_id = body.get("graph_id") if isinstance(body, dict) else None
    neo = isinstance(graph_id, str) and resolver.publication_for(graph_id) is not None
    if neo and not _EVALUATION_SLOTS.acquire(blocking=False):
        raise HTTPException(503, "Graph evaluator is busy; retry later", headers={"Retry-After": "1"})
    token = request_execution_cache.set(CheckExecutionCache())
    try:
        yield
    finally:
        request_execution_cache.reset(token)
        if neo:
            _EVALUATION_SLOTS.release()
