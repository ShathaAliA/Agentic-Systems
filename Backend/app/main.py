from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.http_security import SecuritySettings, install_inner, install_outer
from app.response_security import ResponseSecurityMiddleware
from app.auth.config import AuthSettings
from app.auth.dependencies import get_central_session
from app.auth.routes import router as auth_router
from app.departments.routes import router as departments_router
from app.documents.router import router as documents_router
from app.users.routes import router as users_router
from app.users.routes import administrator
from app.usage.routes import router as usage_router
from app.role_policies.routes import router as role_policies_router
from app.conversation_store.routes import router as conversations_router
from app.handoffs.routes import (admin_router as handoff_admin_router, notifications_router, router as handoffs_router)


def create_app(*, central_engine=None, auth_settings=None, document_result_store_factory=None,
               conversation_store_factory=None, source_authorizer_provider=None, result_store_factory=None,
               security_settings=None):
    auth = auth_settings or AuthSettings.from_environment()
    security = security_settings or SecuritySettings.from_environment(auth.app_env)
    docs = security.docs_enabled  # API documentation only in development/test unless explicitly enabled
    app = FastAPI(
        title="AI Platform API",
        description="Backend API for the AI Platform",
        version="0.1.0",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.security_settings = security
    # Explicit engine injection is for tests; runtime obtains the guarded engine.
    app.state.central_engine = central_engine
    app.state.auth_settings = auth
    # One C06 result authority serves Core's metadata routes and Documents' /ask. The separate
    # injections remain for tests; with nothing injected and nothing configured, both stay None
    # and content/question delivery is disabled (fail closed).
    if (result_store_factory is None and document_result_store_factory is None
            and conversation_store_factory is None):
        from app.integration import SETTING, documents_result_store_factory
        import os
        if os.environ.get(SETTING):
            if central_engine is None:
                from app.database import engine as central_engine
            result_store_factory = documents_result_store_factory(central_engine)
    if result_store_factory is not None:
        # Core's routes wrap the raw one-argument factory with the provider themselves; /ask
        # uses the three-argument shim of the same factory object.
        document_result_store_factory = result_store_factory
        conversation_store_factory = result_store_factory.metadata_factory
        source_authorizer_provider = source_authorizer_provider or result_store_factory.provider
    app.state.document_result_store_factory = document_result_store_factory
    app.state.conversation_store_factory = conversation_store_factory
    app.state.source_authorizer_provider = source_authorizer_provider

    # --- Routers ---
    app.include_router(auth_router)
    app.include_router(departments_router)
    app.include_router(documents_router)
    app.include_router(users_router)
    app.include_router(usage_router)
    app.include_router(role_policies_router)
    app.include_router(conversations_router)
    app.include_router(handoffs_router)
    app.include_router(notifications_router)
    app.include_router(handoff_admin_router)

    install_inner(app, security)
    app.add_middleware(ResponseSecurityMiddleware)
    install_outer(app, security)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request data"})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Database is unavailable"})

    @app.get("/")
    def root():
        return {"message": "Welcome to AI Platform API", "status": "running"}

    @app.get("/health")
    def health():
        return {"status": "healthy", "service": "backend"}

    @app.get("/health/db", dependencies=[Depends(administrator)])
    def database_health(session: Session = Depends(get_central_session)):
        try:
            session.execute(text("SELECT 1"))
            return {"status": "healthy", "database": "connected"}
        except SQLAlchemyError:
            raise HTTPException(503, "Database is unavailable") from None

    return app


app = create_app()
