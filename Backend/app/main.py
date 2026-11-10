from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from sqlalchemy.orm import Session
import traceback

from app.auth.config import AuthSettings
from app.auth.dependencies import get_central_session
from app.auth.routes import router as auth_router
from app.departments.routes import router as departments_router
from app.documents.router import router as documents_router
from app.users.routes import router as users_router
from app.users.routes import administrator
from app.usage.routes import router as usage_router
from app.role_policies.routes import router as role_policies_router


def create_app(*, central_engine=None, auth_settings=None):
    app = FastAPI(
        title="AI Platform API",
        description="Backend API for the AI Platform",
        version="0.1.0",
    )
    app.state.central_engine = central_engine
    app.state.auth_settings = auth_settings or AuthSettings.from_environment()

    # --- Routers ---
    app.include_router(auth_router)
    app.include_router(departments_router)
    app.include_router(documents_router)
    app.include_router(users_router)
    app.include_router(usage_router)
    app.include_router(role_policies_router)


    @app.middleware("http")
    async def prevent_auth_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/health/db" or request.url.path.startswith(("/auth/", "/admin/", "/me/", "/departments/", "/documents/", "/api/documents")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

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
