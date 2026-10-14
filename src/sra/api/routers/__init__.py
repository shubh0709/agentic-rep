"""
Mount every API resource on a single APIRouter so `create_app()` only
mounts this one router under the versioned `/api/v1` prefix. Adding a
new resource means adding it to this file, not to `app.py`.
"""

from fastapi import APIRouter, Depends

from sra.api.rate_limit import limit_authenticated
from sra.api.routers import admin, auth, chat, digest, entries, quiz, search

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.router)
for protected_router in (
    entries.router,
    digest.router,
    search.router,
    chat.router,
    quiz.router,
):
    api_router.include_router(protected_router, dependencies=[Depends(limit_authenticated)])
