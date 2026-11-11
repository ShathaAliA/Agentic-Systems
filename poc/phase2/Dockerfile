# Build context is Backend/. No Phase 1 sources or private run state is copied.
FROM python:3.13-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHON_DOTENV_DISABLED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /backend
COPY requirements.txt /tmp/backend-requirements.txt
# Reuse the checked-in Core pins; exclude document/model processing packages.
RUN python -c "from pathlib import Path; lines=Path('/tmp/backend-requirements.txt').read_text().splitlines(); core=lines[:lines.index('markitdown[all]')]; Path('/tmp/core-requirements.txt').write_text('\n'.join(core)+'\npgvector==0.5.1\npython-multipart==0.0.33\n')" \
    && pip install --no-cache-dir -r /tmp/core-requirements.txt
RUN useradd --uid 10001 --create-home backend

FROM base AS test
RUN pip install --no-cache-dir 'pytest>=8,<10' 'httpx>=0.28,<0.29'
COPY . .
USER backend

FROM base AS runtime
COPY app ./app
COPY alembic.ini tenant_alembic.ini ./
COPY alembic ./alembic
COPY tenant_alembic ./tenant_alembic
USER backend
