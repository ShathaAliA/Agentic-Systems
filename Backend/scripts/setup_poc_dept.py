"""Setup two PoC departments (HR + Finance) for end-to-end testing.

Does NOT touch Legal / hr_db / finance_db / existing tenants.
Creates two new dept_<hex> databases with tenant_0004 schema.
Inserts departments + department_databases rows.
Runtime credentials use `postgres` (PoC only — NOT production).

Usage:
    python scripts/setup_poc_dept.py

After running, note the department IDs printed. Use them in E2E tests.
To clean up: python scripts/setup_poc_dept.py --cleanup
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND / "tenant_alembic.ini"
STATE_FILE = BACKEND / "scripts" / ".poc_state.json"

ADMIN_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5435/postgres"
CENTRAL_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5435/wadi_db"
PG_HOST = "127.0.0.1"
PG_PORT = 5435
PG_USER = "postgres"
PG_PASS = "postgres"


def make_db_name(prefix: str) -> str:
    hex_ = uuid.uuid4().hex
    return f"dept_poc_{prefix}_{hex_[:16]}"


def create_dept_database(db_name: str, dept_id: str, dept_name: str):
    print(f"\n→ Creating {db_name} (dept={dept_name})")

    # 1) Create DB
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        c.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()
    print(f"  ✓ Database created.")

    # 2) Migrate to tenant_0004
    mig_url = f"postgresql+psycopg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{db_name}"
    mig = create_engine(mig_url, hide_parameters=True)
    cfg = Config(str(CONFIG_PATH))
    with mig.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "tenant_0004")
        conn.execute(
            text("INSERT INTO public._provisioning_identity(singleton, department_id) VALUES (1, :id)"),
            {"id": dept_id},
        )
    mig.dispose()
    print(f"  ✓ Migrated to tenant_0004.")

    # 3) Insert into central registry
    credential_key = db_name.upper()
    central = create_engine(CENTRAL_URL, hide_parameters=True)
    with central.begin() as conn:
        conn.execute(
            text("INSERT INTO departments (id, name, status) VALUES (:id, :name, 'active')"),
            {"id": dept_id, "name": dept_name},
        )
        conn.execute(
            text(
                "INSERT INTO department_databases "
                "(id, department_id, database_name, credential_key, status, created_at) "
                "VALUES (:id, :dept, :db, :ck, 'active', NOW())"
            ),
            {
                "id": str(uuid.uuid4()),
                "dept": dept_id,
                "db": db_name,
                "ck": credential_key,
            },
        )
    central.dispose()
    print(f"  ✓ Registered in central DB.")

    return {"department_id": dept_id, "db_name": db_name, "credential_key": credential_key}


def cleanup(state: dict):
    print(f"\n→ Cleanup: {len(state['departments'])} departments")
    central = create_engine(CENTRAL_URL, hide_parameters=True)
    with central.begin() as conn:
        for d in state["departments"]:
            conn.execute(text("DELETE FROM department_databases WHERE department_id = :id"), {"id": d["department_id"]})
            conn.execute(text("DELETE FROM departments WHERE id = :id"), {"id": d["department_id"]})
    central.dispose()

    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as c:
        for d in state["departments"]:
            c.execute(text(f'DROP DATABASE IF EXISTS "{d["db_name"]}"'))
    admin.dispose()
    print("  ✓ Cleaned up.")

    if STATE_FILE.exists():
        STATE_FILE.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    if args.cleanup:
        if not STATE_FILE.exists():
            print("No state file found. Nothing to clean.")
            return
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        cleanup(state)
        return

    # Fresh setup
    if STATE_FILE.exists():
        print("⚠ State file exists. Run with --cleanup first.")
        sys.exit(1)

    state = {"departments": []}

    # HR dept
    hr_id = str(uuid.uuid4())
    hr = create_dept_database(make_db_name("hr"), hr_id, f"PoC HR {hr_id[:8]}")
    state["departments"].append(hr)

    # Finance dept
    fin_id = str(uuid.uuid4())
    fin = create_dept_database(make_db_name("finance"), fin_id, f"PoC Finance {fin_id[:8]}")
    state["departments"].append(fin)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"\n✅ PoC departments ready:")
    print(f"   HR:       {hr['department_id']}")
    print(f"   Finance:  {fin['department_id']}")
    print(f"\n   State saved to: {STATE_FILE}")
    print(f"\n   To cleanup: python scripts/setup_poc_dept.py --cleanup")


if __name__ == "__main__":
    main()