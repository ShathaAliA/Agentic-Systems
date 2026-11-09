# System Dependencies

## Required
- **PostgreSQL 16 + pgvector** (Docker: `pgvector/pgvector:pg16`)
- **Python 3.12+** (with venv)
- **All packages** from `requirements.txt`

## Optional (OCR only)
- **Tesseract OCR** — required for image/PDF-scanned text extraction.
  - Windows: https://github.com/UB-Mannheim/tesseract/wiki
  - Add `tesseract.exe` to PATH.
  - **Without it:** image/PDF-scanned files are accepted but return empty text
    (no crash). Arabic OCR requires `tesseract-ocr-ara` language pack.

### Verify Tesseract:
```powershell
where.exe tesseract
tesseract --version
tesseract --list-langs   # should include 'ara' for Arabic