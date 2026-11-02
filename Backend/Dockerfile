FROM python:3.13-slim-bookworm
WORKDIR /backend
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY . .
RUN useradd --uid 10001 --create-home backend
USER backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
