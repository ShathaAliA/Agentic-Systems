"""Test collection safety, without importing application/database modules."""
import os
from pathlib import Path
import pytest

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
SAFE = BACKEND / "tests"


def pytest_addoption(parser):
    parser.addoption("--isolated-postgres", action="store_true",
                     help="Allow the fresh-cluster integration runners only")
    parser.addoption("--allow-live-provisioning", action="store_true",
                     help="Explicit manual opt-in; changes original development infrastructure")


def pytest_configure(config):
    # Environment flags alone never enable infrastructure tests. Reject explicit
    # legacy paths before collection/import can run their top-level side effects.
    for argument in config.args:
        path = Path(str(argument).split("::", 1)[0]).resolve()
        if path != ROOT and path != BACKEND and path != SAFE and SAFE not in path.parents:
            raise pytest.UsageError("Only Backend/tests may be collected; legacy scripts are not tests")
    if config.getoption("--allow-live-provisioning"):
        if config.getoption("--isolated-postgres"):
            raise pytest.UsageError("Live and isolated infrastructure cannot be combined")
        if os.getenv("RUN_DEVELOPMENT_PROVISIONING") != "1" or os.getenv("DEV_BACKUP_ACK") != "1":
            raise pytest.UsageError("Live provisioning requires explicit backup acknowledgement and opt-in")


def pytest_ignore_collect(collection_path, config):
    path = collection_path.resolve()
    if path == SAFE or SAFE in path.parents or path in SAFE.parents:
        return False
    return True


def pytest_collection_modifyitems(config, items):
    isolated = config.getoption("--isolated-postgres")
    live = config.getoption("--allow-live-provisioning")
    for item in items:
        if item.path.name == "test_development_provisioning.py":
            item.add_marker(pytest.mark.live)
            if not live:
                item.add_marker(pytest.mark.skip(reason="Original development cluster excluded; manual live opt-in required"))
        elif item.get_closest_marker("integration") and not isolated:
            item.add_marker(pytest.mark.skip(reason="Use a fresh-cluster runner with --isolated-postgres"))
