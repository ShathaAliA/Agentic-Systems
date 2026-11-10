"""Only fresh clusters admitted by the existing Core runner. No fixed URLs/drop."""
import uuid
import pytest
from psycopg import sql
from types import SimpleNamespace


def install_fake_embeddings(monkeypatch):
    from app.documents import rag_service
    # These cases assert storage/version/location behavior, not retrieval quality.
    # No model download/provider traffic in an isolated internal Docker network.
    vector = SimpleNamespace(tolist=lambda: [1.0] + [0.0] * 767)
    monkeypatch.setattr(rag_service, "get_embedding_model",
        lambda: SimpleNamespace(encode=lambda _: vector))


@pytest.fixture
def fresh_db(isolated_postgres, monkeypatch):
    install_fake_embeddings(monkeypatch)
    bootstrap, _, _ = isolated_postgres
    name = "dept_" + uuid.uuid4().hex
    with bootstrap.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.connection.driver_connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    # Retained for recovery; the owning runner stops the entire new cluster.
    yield {"db_name": name, "url": bootstrap.url.set(database=name)}
