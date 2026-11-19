"""Documents <-> Core composition: single result authority wiring and the frozen legacy store."""
import json
import re
from pathlib import Path

import pytest

from app.auth.config import AuthSettings
from app.conversation_store.authorization import ConversationResultStoreFactory
from app.documents import result_store as legacy
from app.documents.source_provider import DocumentSourceAuthorizerProvider
from app.integration import SETTING, build_result_store_factory
from app.main import create_app
from app.usage.service import UsageError

APP = Path(__file__).resolve().parents[1] / "app"


def settings():
    return AuthSettings(mode="development_password", app_env="test")


def url_file(tmp_path, url):
    path = tmp_path / "c06.json"
    path.write_text(json.dumps({"url": url}), encoding="utf-8")
    return str(path)


def test_nothing_configured_means_every_injection_stays_disabled(monkeypatch):
    monkeypatch.delenv(SETTING, raising=False)
    app = create_app(central_engine=object(), auth_settings=settings())
    assert app.state.document_result_store_factory is None
    assert app.state.conversation_store_factory is None
    assert app.state.source_authorizer_provider is None
    assert build_result_store_factory(object(), None, environ={}) is None


def test_one_factory_feeds_core_routes_and_documents_ask(monkeypatch):
    monkeypatch.delenv(SETTING, raising=False)
    provider = DocumentSourceAuthorizerProvider(object(), lambda **_: None)
    factory = ConversationResultStoreFactory(lambda user: None, object(), provider)
    app = create_app(central_engine=object(), auth_settings=settings(), result_store_factory=factory)
    assert app.state.document_result_store_factory is factory
    assert app.state.conversation_store_factory is factory.metadata_factory
    assert app.state.source_authorizer_provider is provider


@pytest.mark.parametrize("url", ["sqlite:///x.db", "postgresql+psycopg://user@127.0.0.1/", "postgresql+psycopg://127.0.0.1/db"])
def test_invalid_store_configuration_is_refused(tmp_path, url):
    with pytest.raises(RuntimeError, match="Invalid result-store configuration"):
        build_result_store_factory(object(), None, environ={SETTING: url_file(tmp_path, url)},
                                   engine_factory=lambda *a, **k: pytest.fail("no engine for an invalid URL"))


def test_symlinked_configuration_is_refused(tmp_path):
    real = Path(url_file(tmp_path, "postgresql+psycopg://c06_runtime:x@127.0.0.1/c06"))
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(RuntimeError, match="Symlink"):
        build_result_store_factory(object(), None, environ={SETTING: str(link)})


def test_provider_refuses_multi_department_conversations_and_binds_single_scope():
    provider = DocumentSourceAuthorizerProvider("central", "tenant")
    with pytest.raises(UsageError) as error:
        provider.authorizer_for("user", "conversation", ("a", "b"))
    assert error.value.status == 503 and error.value.code == "source_authorizer_unavailable"
    authorizer = provider.authorizer_for("user", "conversation", ("dept",))
    assert (authorizer.user_id, authorizer.department_id, authorizer.conversation_id) == ("user", "dept", "conversation")


def test_legacy_tenant_result_store_stays_disabled():
    for operation in (legacy.save, legacy.load):
        with pytest.raises(UsageError) as error:
            operation("anything")
        assert error.value.status == 503 and error.value.code == "legacy_tenant_result_store_disabled"


def test_no_application_code_uses_or_writes_the_legacy_question_results_table():
    # Core has its own unrelated QuestionResult dataclass; only Documents' ORM model and raw SQL matter.
    pattern = re.compile(r"from\s+app\.documents\.models\s+import[^\n]*QuestionResult|documents\.models\.QuestionResult"
                         r"|INSERT\s+INTO\s+(public\.)?question_results", re.I)
    offenders = [str(path.relative_to(APP)) for path in APP.rglob("*.py")
                 if pattern.search(path.read_text(encoding="utf-8"))]
    assert offenders == []
