"""LLM settings from the environment. The API key is never logged or shown in repr."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        base_url = (env.get("LLM_BASE_URL") or "").strip().rstrip("/")
        model = (env.get("LLM_MODEL") or "").strip()
        if not base_url or not model:
            raise ValueError("LLM_BASE_URL and LLM_MODEL are required")
        parsed = urlparse(base_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("LLM_BASE_URL must use https")
        key = (env.get("LLM_API_KEY") or "").strip()
        key_file = env.get("LLM_API_KEY_FILE")
        if not key and key_file:
            path = Path(key_file)
            if path.is_symlink():
                raise ValueError("Symlink key files are not supported")
            key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError("LLM_API_KEY or LLM_API_KEY_FILE is required")
        timeout = float(env.get("LLM_TIMEOUT_SECONDS") or 60)
        return cls(base_url, model, key, timeout)
