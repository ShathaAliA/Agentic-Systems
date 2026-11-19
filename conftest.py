"""Root discovery fence; infrastructure opt-ins live in Backend/conftest.py."""
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent
SAFE = ROOT / "Backend/tests"


def pytest_configure(config):
    for argument in config.args:
        path = Path(str(argument).split("::", 1)[0]).resolve()
        if path not in {ROOT, SAFE.parent, SAFE} and SAFE not in path.parents:
            raise pytest.UsageError("Only Backend/tests may be collected; legacy scripts are not tests")


def pytest_ignore_collect(collection_path, config):
    path = collection_path.resolve()
    return not (path == SAFE or SAFE in path.parents or path in SAFE.parents)
