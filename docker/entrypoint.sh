#!/bin/sh
# Container start-up: wait for the database, apply migrations, then hand over
# to the command (gunicorn by default).
#
# Deliberately does NOT create an administrator - that is what the first-run
# setup wizard at /setup/ is for. Baking credentials into an image or an
# environment variable is exactly what we are trying to avoid.
set -e

echo "[entrypoint] waiting for the database..."
python - <<'PY'
import os, sys, time
import dj_database_url

url = os.environ.get("DATABASE_URL", "")
if not url:
    print("[entrypoint] no DATABASE_URL set - using the local SQLite file")
    sys.exit(0)

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "college_management_system.settings")
django.setup()
from django.db import connections
from django.db.utils import OperationalError

for attempt in range(60):
    try:
        connections["default"].ensure_connection()
        print("[entrypoint] database is up")
        sys.exit(0)
    except OperationalError as exc:
        print(f"[entrypoint] not ready ({attempt + 1}/60): {exc}")
        time.sleep(2)

print("[entrypoint] database never became reachable", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] applying migrations..."
python manage.py migrate --noinput

mkdir -p "${MEDIA_ROOT:-/data/media}"

echo "[entrypoint] starting: $*"
exec "$@"
