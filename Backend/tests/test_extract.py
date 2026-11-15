"""P2-3: Assert-based extraction tests for poc/phase1 documents."""
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

from app.documents.file_processor import process_file

POC_DIR = BACKEND.parent / "poc" / "phase1" / "documents"

EXPECTED = {
    "D1-v1.pdf": {"status": "accepted", "min_len": 100},
    "D1-v2.pdf": {"status": "accepted", "min_len": 100},
    "D2.pdf": {"status": "accepted", "min_len": 50},   # scanned (OCR)
    "D3.xlsx": {"status": "accepted", "min_len": 30},
    "D4.docx": {"status": "accepted", "min_len": 30},
    "D5.md": {"status": "accepted", "min_len": 30},
    "D6.pdf": {"status": "accepted", "min_len": 100},
}


@pytest.mark.parametrize("filename", sorted(EXPECTED))
def test_process_file(filename):
    path = POC_DIR / filename
    assert path.exists(), f"Missing test file: {path}"

    r = process_file(str(path))
    expected = EXPECTED[filename]

    assert r["status"] == expected["status"], (
        f"{filename}: expected {expected['status']}, got {r['status']} "
        f"(reason={r.get('reason')})"
    )

    text = (r.get("extraction", {}) or {}).get("original_text") or ""
    assert len(text) >= expected["min_len"], (
        f"{filename}: text_len={len(text)} < {expected['min_len']}"
    )


def test_no_false_success_on_empty_file(tmp_path):
    """Empty file must be rejected (ValueError at detect_file stage)."""
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")

    with pytest.raises(ValueError, match="File is empty"):
        process_file(str(empty))