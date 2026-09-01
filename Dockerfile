FROM python:3.14-slim

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

# Static assets are baked into the image. The secret below is a non-secret
# placeholder satisfying settings' production guard inside this RUN layer only;
# using a BuildKit secret mount would keep even the string out of history.
RUN --mount=type=secret,id=django_secret_key,required=false \
    DJANGO_SECRET_KEY="$([ -f /run/secrets/django_secret_key ] && cat /run/secrets/django_secret_key || echo build-time-collectstatic-only)" \
    python manage.py collectstatic --noinput

USER bankio

EXPOSE 8000

# Gunicorn: production-grade WSGI server (runserver is development-only).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "60", \
     "--graceful-timeout", "30", "--access-logfile", "-"]
