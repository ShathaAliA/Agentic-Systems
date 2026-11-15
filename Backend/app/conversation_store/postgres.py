"""PostgreSQL implementation of C04; no connection configuration or public routes."""
from contextlib import contextmanager
from hashlib import sha256
import json
import re
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from app.conversation_store.schema import Answer
from app.usage.external_results import StoredQuestionResult
from app.usage.service import UsageError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validated(record):
    try:
        if (not isinstance(record, StoredQuestionResult) or type(record.user_id) is not str
                or not 1 <= len(record.user_id) <= 36
                or not re.fullmatch(r"[0-9a-f]{64}", record.fingerprint)):
            raise ValueError()
        answer = Answer.model_validate(record.payload)
        expected = "no_answer" if answer.status == "no_answer" else "answered"
        if record.outcome != expected or answer.question_request_id != record.request_id:
            raise ValueError()
        return answer.model_dump(mode="json")
    except (ValueError, TypeError, ValidationError):
        raise UsageError(422, "invalid_stored_answer") from None


class PostgresResultStore:
    """Trusted internal adapter. Caller identity is bound, not selected per read.

    source_authorizer must revalidate active identity, scope and all source
    versions/audiences with their owning service. None disables delivery before
    connecting. No document integration is supplied here. RLS is defense in depth;
    trusted code, not clients, sets the user context on the restricted connection.
    """

    def __init__(self, engine, *, expected_database: str, authenticated_user_id: str,
                 source_authorizer: Callable[[str, dict], bool] | None = None):
        if (engine.dialect.name != "postgresql" or not expected_database
                or engine.url.database != expected_database or type(authenticated_user_id) is not str
                or not 1 <= len(authenticated_user_id) <= 36):
            raise UsageError(503, "result_store_configuration_invalid")
        self.engine, self.database, self.user_id = engine, expected_database, authenticated_user_id
        self.authorize = source_authorizer

    @contextmanager
    def connection(self):
        try:
            with self.engine.begin() as conn:
                # Guard every checkout, including pools populated before construction.
                unsafe = conn.execute(text("""
                    SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
                      OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles inherited WHERE
                          pg_has_role(current_user,inherited.oid,'MEMBER') AND
                          (inherited.rolsuper OR inherited.rolcreatedb OR inherited.rolcreaterole
                           OR inherited.rolreplication OR inherited.rolbypassrls OR left(inherited.rolname,3)='pg_'))
                      OR EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname=current_database()
                          AND pg_has_role(current_user,datdba,'MEMBER'))
                    FROM pg_catalog.pg_roles WHERE rolname=current_user
                """)).scalar_one()
                if unsafe:
                    raise UsageError(503, "result_store_privileged_identity")
                identity = conn.execute(text("SELECT current_database(), kind, version FROM public.c06_store_identity")).all()
                if identity != [(self.database, "conversation_results", 1)]:
                    raise UsageError(503, "result_store_identity_invalid")
                conn.execute(text("SELECT set_config('app.c06_user_id', :user, true)"), {"user": self.user_id})
                yield conn
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise UsageError(409, "immutable_result_conflict") from None
            raise UsageError(503, "result_store_unavailable") from None
        except SQLAlchemyError:
            raise UsageError(503, "result_store_unavailable") from None

    def save_once(self, result: StoredQuestionResult) -> None:
        payload = validated(result)
        if result.user_id != self.user_id:
            raise UsageError(403, "result_owner_forbidden")
        encoded = canonical(payload)
        with self.connection() as conn:
            conn.execute(text("""INSERT INTO public.c06_results
                (request_id,owner_id,fingerprint,outcome,schema_version,payload,payload_hash)
                VALUES (:request,:owner,:fingerprint,:outcome,1,CAST(:payload AS jsonb),:hash)"""),
                dict(request=result.request_id, owner=result.user_id, fingerprint=result.fingerprint,
                     outcome=result.outcome, payload=encoded, hash=sha256(encoded.encode()).hexdigest()))
            for ordinal, source in enumerate(payload["sources"]):
                conn.execute(text("""INSERT INTO public.c06_result_sources(request_id,owner_id,ordinal,source)
                    VALUES (:request,:owner,:ordinal,CAST(:source AS jsonb))"""),
                    dict(request=result.request_id, owner=result.user_id, ordinal=ordinal, source=canonical(source)))

    def load_authorized(self, user_id: str, request_id: str) -> StoredQuestionResult | None:
        if user_id != self.user_id:
            raise UsageError(403, "result_owner_forbidden")
        if self.authorize is None:
            raise UsageError(503, "source_authorizer_unavailable")
        with self.connection() as conn:
            row = conn.execute(text("""SELECT fingerprint,outcome,schema_version,payload,payload_hash
                FROM public.c06_results WHERE request_id=:request AND owner_id=:owner"""),
                dict(request=request_id, owner=user_id)).mappings().one_or_none()
            if row is None:
                return None
            refs = conn.execute(text("""SELECT source FROM public.c06_result_sources
                WHERE request_id=:request AND owner_id=:owner ORDER BY ordinal"""),
                dict(request=request_id, owner=user_id)).scalars().all()
        record = StoredQuestionResult(user_id, request_id, row["fingerprint"], row["outcome"], row["payload"])
        try:
            payload = validated(record)
        except UsageError:
            raise UsageError(503, "question_result_integrity_error") from None
        if (row["schema_version"] != 1 or payload["sources"] != refs
                or sha256(canonical(payload).encode()).hexdigest() != row["payload_hash"]):
            raise UsageError(503, "question_result_integrity_error")
        # The callback only sees a copy: a callback cannot rewrite the delivered record.
        decision = self.authorize(user_id, json.loads(canonical(payload)))
        if decision is not True:
            raise UsageError(403, "question_source_access_revoked")
        return record

