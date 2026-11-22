"""Core-owned binding contract. No tenant sessions or document imports."""
from dataclasses import dataclass
import json
from typing import Callable, Protocol
from uuid import UUID

from app.conversation_store.conversations import ConversationService
from app.conversation_store.postgres import PostgresResultStore, canonical
from app.conversation_store.schema import Answer
from app.usage.service import UsageError


class SourceAuthorizerProvider(Protocol):
    """Trusted server injection; implementations revalidate current source audiences.

    Scope comes from immutable Core metadata. No provider means no content delivery.
    A provider must enforce its own bounded IO timeouts; Core cannot interrupt an
    arbitrary synchronous callback. This contract does not open tenant databases.
    """

    def authorizer_for(self, user_id: str, conversation_id: str,
                       department_ids: tuple[str, ...]) -> Callable[[str, dict], bool]: ...


@dataclass(frozen=True)
class ConversationBinding:
    user_id: str
    conversation_id: str
    department_ids: tuple[str, ...]


def bind_conversation(service, conversation_id, department_id=None):
    try:
        identity = str(UUID(conversation_id))
    except (ValueError, TypeError, AttributeError):
        raise UsageError(404, "conversation_not_found") from None
    # Owner lookup comes first: an Admin does not inherit another user's history.
    metadata = service.get(identity)
    departments = tuple(metadata["department_ids"])
    if not service.scope_allowed(departments):
        raise UsageError(403, "conversation_scope_forbidden")
    if department_id is not None and (metadata["mode"] != "department"
                                      or departments != (department_id,)):
        raise UsageError(403, "conversation_scope_forbidden")
    return ConversationBinding(service.store.user_id, identity, departments)


class ConversationResultStoreFactory:
    """Preserve metadata_factory(user_id); offer the Documents three-argument shim.

    Each call constructs a new adapter: never mutate a cached factory store's
    authorizer or bind identity/scope from a request's asserted department alone.
    """

    def __init__(self, metadata_factory, central_engine, provider=None):
        self.metadata_factory = metadata_factory
        self.central_engine = central_engine
        self.provider = provider

    def metadata_for(self, user_id):
        return self._store(user_id)

    def __call__(self, user_id, conversation_id, department_id):
        store = self._store(user_id)
        service = ConversationService(store, self.central_engine)
        binding = bind_conversation(service, conversation_id, department_id)
        store.authorize = self._authorizer(service, binding)
        return store

    def _store(self, user_id):
        try:
            base = self.metadata_factory(user_id)
        except Exception:
            raise UsageError(503, "conversation_store_configuration_invalid") from None
        if not isinstance(base, PostgresResultStore) or base.user_id != user_id:
            raise UsageError(503, "conversation_store_configuration_invalid")
        store = PostgresResultStore(base.engine, expected_database=base.database,
                                   authenticated_user_id=user_id)
        store.authorize = self._authorizer(ConversationService(store, self.central_engine))
        return store

    def _authorizer(self, service, expected=None):
        def authorize(user_id, payload):
            try:
                # Also protect direct use of the shim's .authorize callback.
                return verify(user_id, json.loads(canonical(payload)))
            except UsageError as exc:
                if exc.status == 403:
                    raise UsageError(403, "question_source_access_revoked") from None
                raise UsageError(503, "source_authorizer_unavailable") from None
            except Exception:
                raise UsageError(503, "source_authorizer_unavailable") from None

        def verify(user_id, payload):
            if user_id != service.store.user_id:
                raise UsageError(403, "question_source_access_revoked")
            answer = Answer.model_validate(payload)
            binding = bind_conversation(service, answer.conversation_id)
            if (expected is not None and binding != expected) or any(
                    source.department_id not in binding.department_ids for source in answer.sources
            ) or any(d not in binding.department_ids for d in answer.unanswered_departments):
                raise UsageError(403, "question_source_access_revoked")
            if self.provider is None:
                raise UsageError(503, "source_authorizer_unavailable")
            callback = self.provider.authorizer_for(binding.user_id, binding.conversation_id,
                                                    binding.department_ids)
            if not callable(callback):
                raise UsageError(503, "source_authorizer_unavailable")
            if callback(user_id, payload) is not True:
                raise UsageError(403, "question_source_access_revoked")
            # Re-read active identity/RBAC after potentially slow provider IO.
            if bind_conversation(service, binding.conversation_id) != binding:
                raise UsageError(403, "question_source_access_revoked")
            return True
        return authorize
