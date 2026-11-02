from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.auth.config import AuthSettings
from app.auth.routes import router as auth_router


def create_app(*, central_engine=None, auth_settings=None):
    app = FastAPI(title="AI Platform API", description="Backend API for the AI Platform", version="0.1.0")
    # Explicit engine injection is for tests; runtime obtains the guarded engine.
    app.state.central_engine = central_engine
    app.state.auth_settings = auth_settings or AuthSettings.from_environment()
    app.include_router(auth_router)

    @app.middleware("http")
    async def prevent_auth_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc):
        # Default validation errors can echo passwords and forged token data.
        return JSONResponse(status_code=422, content={"detail": "Invalid request data"})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        # Never return SQL parameters, DSNs, password hashes or driver exceptions.
        return JSONResponse(status_code=503, content={"detail": "Database is unavailable"})

    @app.get("/")
    def root():
        return {"message": "Welcome to AI Platform API", "status": "running"}

    @app.get("/health")
    def health():
        return {"status": "healthy", "service": "backend"}

    @app.get("/health/db")
    def database_health():
        from app.database import check_database_connection
        try:
            check_database_connection()
            return {"status": "healthy", "database": "connected"}
        except SQLAlchemyError:
            raise HTTPException(503, "Database is unavailable") from None

    return app


app = create_app()
