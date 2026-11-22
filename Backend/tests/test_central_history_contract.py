"""Read migration scripts/ORM metadata without env.py, engines or databases."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from alembic.script import ScriptDirectory

from app.usage.models import UsageRequest

BACKEND = Path(__file__).resolve().parents[1]


def test_central_graph_is_connected_single_head_without_database_configuration():
    # ScriptDirectory reads revision metadata; never invoke command.upgrade or
    # the central env.py, which resolves database credentials even in offline mode.
    with patch("sqlalchemy.create_engine", side_effect=AssertionError("Database engine must not be created")):
        scripts = ScriptDirectory(str(BACKEND / "alembic"))
        heads = scripts.get_heads()
        history = list(scripts.walk_revisions())
    assert len(heads) == 1
    assert scripts.get_bases() == ["b8e1db57a78c"]
    assert history[0].revision == heads[0]
    assert {item.revision for item in history} >= {"usage001", "rbac001"}
    assert heads == ["handoff001_policy"]
    assert {"usage002_result_payload", "usage003_merge", "usage004_fence"} <= {item.revision for item in history}


def test_reviewed_historical_migration_files_are_not_rewritten():
    baseline = json.loads((BACKEND / "tests/fixtures/central_history_baseline.json").read_text(encoding="utf-8-sig"))
    for file in baseline["files"]:
        path = BACKEND / "alembic/versions" / file["name"]
        # Normalize checkout line endings so Windows/Linux compare the same source.
        content = path.read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(content).hexdigest() == file["sha256"], file["name"]


def test_usage_request_metadata_has_no_question_or_result_payload_column():
    allowed = {"id", "user_id", "request_key", "fingerprint", "policy_version", "timezone",
               "question_month", "question_day", "state", "outcome", "estimated_micro_usd",
               "held_micro_usd", "claim_id", "created_at", "started_at", "closed_at"}
    assert set(UsageRequest.__table__.columns.keys()) == allowed
    assert all(column.type.__class__.__name__ not in {"JSON", "JSONB", "LargeBinary"}
               for column in UsageRequest.__table__.columns)
