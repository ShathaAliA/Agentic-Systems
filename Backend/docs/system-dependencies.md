# System dependencies

- Python 3.13 (the tested Core/runtime target).
- PostgreSQL 17+ with pgvector for the full tenant document schema.
- Install requirements-test.txt for verification; it includes requirements.txt.
- Arabic scanned PDF/image support requires Tesseract and the ara language pack.
  OCR availability is required for that acceptance path, not an optional success
  with empty extracted text. OCR quality has not been certified by the sync fixes.

pgvector is an untrusted extension. A separate DBA/bootstrap identity must install
it in each NEW tenant database before owner-scoped migrations; never grant the API
or regular provisioning worker superuser access. The full-schema synthetic worker
CI test demonstrates this split. Do not apply it to HR/Finance or activate Legal.

The legacy Backend/scripts PoC setup/E2E/generator were removed. The approved PoC
branch is deferred; no PoC commands are part of this synchronization check.

```powershell
tesseract --version
tesseract --list-langs
```

For paid questions, configure reviewed OpenAI model prices and bounded usage as
specified in sync-blocker-verification.md. Missing configuration fails closed.
