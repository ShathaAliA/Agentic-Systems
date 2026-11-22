"""OpenAI-compatible model client. Not wired into any route: callers opt in explicitly."""
from .client import ChatClient
from .config import LLMSettings

__all__ = ["ChatClient", "LLMSettings"]
