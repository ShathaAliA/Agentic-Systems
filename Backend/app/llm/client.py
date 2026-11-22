"""Minimal chat-completions client for any OpenAI-compatible endpoint (stdlib only)."""
import json
import urllib.error
import urllib.request

from .config import LLMSettings


class LLMError(RuntimeError):
    """Raised without the key, request body or response body."""


class ChatClient:
    def __init__(self, settings=None):
        self.settings = settings or LLMSettings.from_env()

    def chat(self, messages, **options):
        s = self.settings
        body = json.dumps({"model": s.model, "messages": messages, **options}).encode()
        request = urllib.request.Request(
            s.base_url + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + s.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=s.timeout_seconds) as response:
                data = json.load(response)
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as error:
            raise LLMError(f"Model request failed with HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError):
            raise LLMError("Model request failed") from None
