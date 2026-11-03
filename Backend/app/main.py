from fastapi import FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from app.documents.router import router as documents_router
from app.database import check_database_connection

app = FastAPI(
    title="AI Platform API",
    description="Backend API for the AI Platform",
    version="0.1.0",
)
app.include_router(documents_router)


@app.get("/")
def root():
    return {
        "message": "Welcome to AI Platform API",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "backend",
    }


@app.get("/health/db")
def database_health():
    try:
        check_database_connection()

        return {
            "status": "healthy",
            "database": "connected",
        }

    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable",
        )
