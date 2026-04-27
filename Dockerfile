FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pipenv

COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy

COPY . .

RUN mkdir -p /app/data

# Railway and other PaaS set $PORT; local default remains 8000 via Settings.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os, urllib.request; p=os.environ.get(\"PORT\",\"8000\"); urllib.request.urlopen(f\"http://127.0.0.1:{p}/api/v1/healthz\")"

# Use main.py so host/port match Settings (reads PORT from the environment).
CMD ["python", "main.py"]
