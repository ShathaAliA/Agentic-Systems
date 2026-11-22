FROM python:3.13-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHON_DOTENV_DISABLED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ara && rm -rf /var/lib/apt/lists/*
WORKDIR /backend
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home backend

FROM base AS test
COPY requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY . .
USER backend
CMD ["python", "-m", "pytest", "--version"]

# Last stage is the default; no test tools or operator bootstrap scripts.
FROM base AS runtime
COPY app ./app
COPY alembic.ini tenant_alembic.ini ./
COPY alembic ./alembic
COPY tenant_alembic ./tenant_alembic
USER backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header", "--timeout-keep-alive", "5"]
