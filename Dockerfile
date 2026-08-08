# SkillyCMS - college management system
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    STATIC_ROOT=/app/staticfiles \
    MEDIA_ROOT=/data/media

WORKDIR /app

# libpq for psycopg, zlib/jpeg for Pillow. Build tools are removed again so
# they do not ship in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev libjpeg62-turbo-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential libpq-dev

COPY . .

# Collected at build time so start-up is fast and the container needs no write
# access to the code directory. WhiteNoise serves these; no web server needed.
RUN SECRET_KEY=build-only-not-used DJANGO_SETTINGS_MODULE=college_management_system.settings \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 1000 skilly \
    && mkdir -p /data/media \
    && chown -R skilly:skilly /app /data
USER skilly

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "college_management_system.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-"]
