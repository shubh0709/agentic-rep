#!/bin/sh
# Entrypoint for the SRA container.
# - If DATABASE_URL points at a TCP host, wait until the port accepts connections.
# - Run alembic upgrade head against the configured database.
# - exec the CMD (uvicorn by default).

set -eu

if [ -n "${DATABASE_URL:-}" ]; then
    case "$DATABASE_URL" in
        postgresql://*|postgres://*)
            HOST=$(python3 -c "from urllib.parse import urlsplit; print(urlsplit('$DATABASE_URL').hostname or '')")
            PORT=$(python3 -c "from urllib.parse import urlsplit; print(urlsplit('$DATABASE_URL').port or 5432)")
            if [ -n "$HOST" ] && [ "$HOST" != "localhost" ] && [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "::1" ]; then
                echo "Waiting for Postgres at $HOST:$PORT..."
                python3 - <<'PY'
import os
import socket
import sys
import time
from urllib.parse import urlsplit

url = os.environ.get("DATABASE_URL", "")
parsed = urlsplit(url)
host = parsed.hostname or "127.0.0.1"
port = parsed.port or 5432
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        with socket.create_connection((host, port), timeout=1):
            print("Database is ready.")
            sys.exit(0)
    except OSError:
        time.sleep(1)
print(f"Database at {host}:{port} not ready after 60s", file=sys.stderr)
sys.exit(1)
PY
            fi
            ;;
    esac
fi

alembic upgrade head

exec "$@"
