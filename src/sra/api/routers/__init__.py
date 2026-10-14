"""
Mount every API resource on a single APIRouter so `create_app()` only
mounts this one router under the versioned `/api/v1` prefix. Adding a
new resource means adding it to this file, not to `app.py`.
"""

from fastapi import APIRouter

from sra.api.routers import admin, auth, chat, digest, entries, search

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(entries.router)
api_router.include_router(digest.router)
api_router.include_router(search.router)
api_router.include_router(chat.router)
