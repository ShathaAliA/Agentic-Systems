import io
import json
import urllib.request

import pytest

from app.llm import ChatClient, LLMSettings


ENV = {"TRIAL_LLM_BASE_URL": "https://example.test/v1/", "TRIAL_LLM_MODEL": "m", "TRIAL_LLM_API_KEY": "secret-key"}


def test_settings_hide_key_and_require_https():
    s = LLMSettings.from_env(ENV)
    assert s.base_url == "https://example.test/v1" and "secret-key" not in repr(s)
    with pytest.raises(ValueError):
        LLMSettings.from_env({**ENV, "TRIAL_LLM_BASE_URL": "http://example.test/v1"})
    with pytest.raises(ValueError):
        LLMSettings.from_env({k: v for k, v in ENV.items() if k != "TRIAL_LLM_API_KEY"})


def test_chat_posts_to_completions(monkeypatch):
    seen = {}

    def fake(request, timeout):
        seen["url"], seen["auth"] = request.full_url, request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert ChatClient(LLMSettings.from_env(ENV)).chat([{"role": "user", "content": "x"}]) == "hi"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key" and seen["body"]["model"] == "m"
