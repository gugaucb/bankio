FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin bankio

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Static assets are baked into the image. The inline DJANGO_SECRET_KEY is a
# throwaway value scoped to this single RUN layer only — never persisted via
# ARG/ENV — because settings refuse to boot in production mode without one.
RUN DJANGO_SECRET_KEY=build-time-collectstatic-only python manage.py collectstatic --noinput

USER bankio

EXPOSE 8000

# Gunicorn: production-grade WSGI server (runserver is development-only).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "60", \
     "--graceful-timeout", "30", "--access-logfile", "-"]
