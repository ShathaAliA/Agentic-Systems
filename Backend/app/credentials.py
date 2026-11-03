"""Credential references shared by the worker and the read-only runtime resolver."""
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def reference(value: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,149}", value):
        raise ValueError("Invalid credential reference")
    return value


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


class CredentialStore:
    """Local development adapter. Production can replace this with a secrets manager.

    Files contain no DSNs and are named by the registry reference. The worker alone
    writes; the runtime mounts this directory read-only. Never place it under Git.
    """

    def __init__(self, directory):
        self.directory = Path(directory)

    def get(self, key):
        path = self.directory / (reference(key) + ".json")
        if path.is_symlink():
            raise ValueError("Symlink credentials are not supported")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), encoding="utf-8") as stream:
            data = json.load(stream)
        if not data.get("username") or not data.get("password"):
            raise ValueError("Invalid credential file")
        return Credentials(data["username"], data["password"])

    def ensure(self, key, username):
        reference(key)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            existing = self.get(key)
        except FileNotFoundError:
            existing = None
        if existing is None:
            # Prepare fully, fsync, then link atomically without overwriting a
            # winning concurrent writer. A killed writer cannot leave half JSON.
            temporary = self.directory / (".pending-" + secrets.token_hex(16))
            value = {"username": username, "password": secrets.token_urlsafe(36)}
            try:
                with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
                    json.dump(value, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.directory / (key + ".json"))
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
            existing = self.get(key)
        if existing.username != username:
            raise ValueError("Credential identity conflict")
        return existing


def resolve_department_credentials(key):
    key = reference(key.upper())
    # Existing HR/Finance environment references remain compatible.
    username, password = os.getenv(f"{key}_DB_USER"), os.getenv(f"{key}_DB_PASSWORD")
    if username and password:
        return Credentials(username, password)
    directory = os.getenv("DEPARTMENT_CREDENTIALS_DIR")
    if directory:
        return CredentialStore(directory).get(key)
    raise RuntimeError("Department credential reference is not configured")


def configured_url(name):
    path = os.getenv(name + "_FILE")
    if path:
        # Path, not credential contents, is exposed in process configuration.
        return json.loads(Path(path).read_text(encoding="utf-8"))["url"]
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} or {name}_FILE must be configured")
    return value
