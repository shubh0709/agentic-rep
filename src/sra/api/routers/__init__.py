"""
Aggregate router: every resource router mounts here, and `api/app.py`
mounts this one router under the versioned `/api/v1` prefix. Adding a
new resource means adding it to this file, not to `app.py`.
"""

from fastapi import APIRouter

from sra.api.routers import chat, digest, entries, search

api_router = APIRouter()
api_router.include_router(entries.router)
api_router.include_router(digest.router)
api_router.include_router(search.router)
api_router.include_router(chat.router)
