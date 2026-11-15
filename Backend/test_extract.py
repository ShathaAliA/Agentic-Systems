"""Test file_processor on poc/phase1 documents."""
import os
import glob
from pathlib import Path

# Ensure we're in Backend
BACKEND = Path(__file__).resolve().parent
os.chdir(BACKEND)

from app.documents.file_processor import process_file

POC_DIR = BACKEND.parent / "poc" / "phase1" / "documents"

for f in sorted(POC_DIR.glob("*")):
    if not f.is_file():
        continue
    try:
        r = process_file(str(f))
        status = r["status"]
        text_len = len((r.get("extraction", {}) or {}).get("original_text") or "")
        reason = r.get("reason", "")
        print(f"{f.name}: {status} | text_len={text_len} | reason={reason}")
    except Exception as e:
        print(f"{f.name}: EXCEPTION — {type(e).__name__}: {e}")