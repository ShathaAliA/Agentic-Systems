"""Documents' implementation of Core's SourceAuthorizerProvider (Gate 4.2 contract).

Wraps the existing single-tenant DocumentSourceAuthorizer. Scope comes from Core's immutable
conversation metadata, never from a request. No cross-tenant broker: a conversation spanning
several departments is refused (content disabled) until a reviewed multi-tenant design exists.
"""
from app.documents.source_authorization import DocumentSourceAuthorizer
from app.usage.service import UsageError


class DocumentSourceAuthorizerProvider:
    def __init__(self, central_engine, tenant_session):
        """tenant_session: authorized_department_session or an equivalent checked factory."""
        self.central_engine, self.tenant_session = central_engine, tenant_session

    def authorizer_for(self, user_id, conversation_id, department_ids):
        if len(department_ids) != 1:
            raise UsageError(503, "source_authorizer_unavailable")
        return DocumentSourceAuthorizer(self.central_engine, self.tenant_session, user_id=user_id,
                                        department_id=department_ids[0], conversation_id=conversation_id)
