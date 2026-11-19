"""Single composition root for the C06 result store shared by Core routes and Documents' /ask.

Disabled unless C06_RESULT_STORE_URL_FILE names a private JSON file {"url": ...} for the
restricted runtime role. There is no plain-environment fallback and no default credential.
"""
import json
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.conversation_store.authorization import ConversationResultStoreFactory
from app.conversation_store.postgres import PostgresResultStore
from app.runtime_security import restrict_runtime_engine

SETTING = "C06_RESULT_STORE_URL_FILE"


def build_result_store_factory(central_engine, provider, *, environ=None, engine_factory=create_engine):
    """Return a ConversationResultStoreFactory, or None when the store is not configured."""
    path = (environ if environ is not None else os.environ).get(SETTING)
    if not path:
        return None
    target = Path(path)
    if target.is_symlink():
        raise RuntimeError("Symlink result-store configuration is not supported")
    url = make_url(json.loads(target.read_text(encoding="utf-8"))["url"])
    if url.get_backend_name() != "postgresql" or not url.database or not url.username:
        raise RuntimeError("Invalid result-store configuration")
    engine = restrict_runtime_engine(engine_factory(url, pool_pre_ping=True, hide_parameters=True))
    database = url.database

    def metadata_factory(user_id):
        return PostgresResultStore(engine, expected_database=database, authenticated_user_id=user_id)

    return ConversationResultStoreFactory(metadata_factory, central_engine, provider)


def documents_result_store_factory(central_engine):
    """Compose the factory with Documents' source authorizer; None when not configured."""
    if not os.environ.get(SETTING):
        return None
    from app.database_router import authorized_department_session
    from app.documents.source_provider import DocumentSourceAuthorizerProvider
    provider = DocumentSourceAuthorizerProvider(central_engine, authorized_department_session)
    return build_result_store_factory(central_engine, provider)
