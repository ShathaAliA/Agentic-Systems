"""Core-only Phase 2A entry point. Document processing and model routes are disabled."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

from safety import CENTRAL, PURPOSE, REPO, load, target, validate_runtime_url


def make_app(engine, run_id):
    from sqlalchemy import text
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql" and connection.scalar(text("SELECT current_database()")) != CENTRAL:
            raise RuntimeError("wrong_phase2_database")
        if connection.execute(text("SELECT run_id,purpose FROM _poc2_identity")).one() != (run_id, PURPOSE):
            raise RuntimeError("wrong_phase2_run_identity")
    from fastapi import Depends, HTTPException
    from fastapi.responses import JSONResponse
    from app.main import create_app
    from app.auth.config import AuthSettings
    from app.auth.dependencies import get_current_user, get_central_session
    from app.services.authorization import has_permission
    app = create_app(central_engine=engine,
        auth_settings=AuthSettings(mode="development_password", app_env="test"))

    @app.middleware("http")
    async def foundation_only(request, call_next):
        if request.url.path.startswith("/api/documents"):
            return JSONResponse(status_code=503, content={"detail": "phase2a_documents_and_models_disabled"},
                headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/poc/status")
    def status():
        return {"run_id": run_id, "purpose": PURPOSE, "documents_enabled": False, "llm_enabled": False}

    @app.get("/poc/permissions/{department_id}/{permission_code}")
    def permission(department_id: str, permission_code: str,
                   user=Depends(get_current_user), session=Depends(get_central_session)):
        if not has_permission(session, user.id, permission_code, department_id):
            raise HTTPException(403, "Permission denied")
        return {"allowed": True, "department_id": department_id, "permission": permission_code}
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--container", action="store_true")
    args = parser.parse_args()
    if args.container:
        from sqlalchemy.engine import make_url
        state = json.loads(Path("/run/poc2-api/run.json").read_text())
        config = json.loads(Path("/run/poc2-api/url.json").read_text())
        url = make_url(config["url"])
        validate_runtime_url(url, "database", 5432)
        if (state["run_id"] != args.run or not re.fullmatch(r"[0-9a-f]{32}", args.run)
                or state["purpose"] != PURPOSE or url.host != "database"
                or url.database != CENTRAL or url.username != "poc2_api"):
            raise SystemExit("Invalid isolated API configuration")
    else:
        folder, state = load(args.run)
        host, port = target(state)
        config = json.loads((folder / "api/url.json").read_text())
        from sqlalchemy.engine import make_url
        url = make_url(config["url"])
        validate_runtime_url(url, host, port)
        if url.host != host or url.port != port or url.database != CENTRAL or url.username != "poc2_api":
            raise SystemExit("Invalid isolated API configuration")
    sys.path.insert(0, os.getenv("POC2_BACKEND_ROOT", str(REPO / "Backend")))
    from sqlalchemy import create_engine
    from app.runtime_security import restrict_runtime_engine
    import uvicorn
    engine = restrict_runtime_engine(create_engine(url, hide_parameters=True))
    uvicorn.run(make_app(engine, args.run), host="0.0.0.0" if args.container else "127.0.0.1", port=args.port)
