import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Argon2id, 64 MiB, three iterations. No caller-supplied salts or weak fallback.
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
_dummy_hash = hasher.hash(secrets.token_urlsafe(32))


def verify_password(encoded: str | None, password: str) -> bool:
    try:
        valid = hasher.verify(encoded or _dummy_hash, password)
        return bool(encoded) and valid
    except (InvalidHashError, VerificationError):
        return False


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12 to 128 characters")
    return hasher.hash(password)
