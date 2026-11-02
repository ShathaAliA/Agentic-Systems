"""Separate CLI worker; the API only registers work, never executes this module."""
import argparse
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from app.credentials import CredentialStore, configured_url
from app.models import Department, DepartmentDatabase
from .errors import ProvisioningError, SAFE_ERROR_CODES
from .postgres import PostgresProvisioner, identities
from .tenant_migrations import TenantMigrator


@contextmanager
def department_lock(engine, department_id):
    # Session-level lock survives the short status transactions. PG releases it
    # on process/connection death; the persisted claim token fences stale writers.
    with engine.connect() as conn:
        key = "department-provisioning:" + department_id
        locked = conn.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": key})
        conn.commit()
        try:
            yield locked
        finally:
            if locked:
                conn.rollback()
                conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key": key})
                conn.commit()


@dataclass(frozen=True)
class Job:
    department_id: str
    database: str
    credential_key: str
    claim: str
    attempt: int


class Worker:
    def __init__(self, registry_engine, postgres, secrets, migrator, max_attempts=3, retry_seconds=30, lock=department_lock):
        if max_attempts < 1 or retry_seconds < 1:
            raise ValueError("Retry settings must be positive")
        self.engine, self.postgres, self.secrets, self.migrator = registry_engine, postgres, secrets, migrator
        self.max_attempts, self.retry_seconds, self.lock = max_attempts, retry_seconds, lock

    def run(self, department_id, retry_failed=False):
        with self.lock(self.engine, department_id) as locked:
            if not locked:
                return "busy"
            now = datetime.now(timezone.utc)
            with Session(self.engine) as session, session.begin():
                registry = session.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == department_id).with_for_update())
                department = session.get(Department, department_id)
                if registry is None or department is None:
                    raise ProvisioningError("department_not_registered")
                if registry.status == "active":
                    if not registry.database_name.startswith("dept_"):
                        return "active"  # Legacy HR/Finance are outside this worker's scope.
                    if registry.verified_contract == self.migrator.contract_id and self.migrator.available:
                        return "active"
                    # Contract changed/unavailable: remove routing readiness before
                    # doing anything else; never let a previous foundation stamp win.
                    registry.status = department.status = "provisioning"
                if department.status not in {"provisioning", "failed"}:
                    raise ProvisioningError("department_state_conflict")
                retry_at = registry.next_retry_at
                if retry_at and retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                if registry.status == "failed" and not retry_failed and (retry_at is None or retry_at > now):
                    return "deferred"
                if registry.attempt_count >= self.max_attempts:
                    registry.status = department.status = "failed"
                    registry.next_retry_at = None
                    registry.last_error_code = "attempts_exhausted"
                    registry.claim_token = None
                    registry.verified_contract = None
                    return "failed"
                registry.status = department.status = "provisioning"
                registry.attempt_count += 1
                registry.claim_token = str(uuid.uuid4())
                registry.last_attempt_at = now
                registry.next_retry_at = None
                registry.last_error_code = None
                job = Job(department_id, registry.database_name, registry.credential_key, registry.claim_token, registry.attempt_count)
            try:
                if not self.migrator.available:
                    raise ProvisioningError("tenant_contract_unavailable")
                owner, runtime = identities(job.database, job.credential_key)
                credentials = self.secrets.ensure(job.credential_key, runtime)
                self.postgres.ensure_resources(job.database, owner, credentials, job.department_id)
                revision = self.migrator.migrate(self.postgres.migration_url(job.database), owner, job.department_id)
                self.postgres.grant_runtime(job.database, owner, credentials)
                self.postgres.verify(job.database, owner, credentials, job.department_id, revision, self.migrator.required_tables)
            except Exception as exc:
                # Never persist raw DB/DSN/secret-bearing exception strings.
                code = exc.code if isinstance(exc, ProvisioningError) else "infrastructure_error"
                if code not in SAFE_ERROR_CODES:
                    code = "infrastructure_error"
                self.finish(job, "failed", code=code)
                return "failed"
            self.finish(job, "active", revision=revision)
            return "active"

    def finish(self, job, status, code=None, revision=None):
        with Session(self.engine) as session, session.begin():
            row = session.scalar(select(DepartmentDatabase).where(DepartmentDatabase.department_id == job.department_id).with_for_update())
            if row.claim_token != job.claim:
                raise ProvisioningError("stale_claim")
            row.status = session.get(Department, job.department_id).status = status
            row.last_error_code = code
            row.claim_token = None
            row.applied_revision = revision if status == "active" else row.applied_revision
            row.verified_contract = self.migrator.contract_id if status == "active" else None
            row.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=self.retry_seconds * 2 ** (job.attempt - 1)) if status == "failed" and job.attempt < self.max_attempts else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--department-id")
    parser.add_argument("--retry-failed", action="store_true", help="Bypass retry delay, not attempt limit")
    parser.add_argument("--poll", type=float, default=0, help="Poll seconds; default is one pass")
    args = parser.parse_args()
    engine = create_engine(configured_url("PROVISIONING_REGISTRY_URL"), hide_parameters=True, pool_pre_ping=True)
    worker = Worker(engine, PostgresProvisioner(configured_url("PROVISIONING_ADMIN_URL")), CredentialStore(os.environ["DEPARTMENT_CREDENTIALS_DIR"]), TenantMigrator.from_contract(os.environ["TENANT_CONTRACT_FILE"]), max_attempts=int(os.getenv("PROVISIONING_MAX_ATTEMPTS", "3")))
    try:
        while True:
            with Session(engine) as session:
                ids = [args.department_id] if args.department_id else list(session.scalars(select(DepartmentDatabase.department_id).where(DepartmentDatabase.status.in_(["provisioning", "failed"]))))
            failed = False
            for department_id in ids:
                try:
                    status = worker.run(department_id, args.retry_failed)
                except Exception:
                    # CLI output never contains a driver exception or DSN.
                    status = "worker_error"
                print(json.dumps({"department_id": department_id, "status": status}), flush=True)
                failed |= status in {"failed", "worker_error"}
            if args.poll <= 0:
                return 1 if failed else 0
            time.sleep(args.poll)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
