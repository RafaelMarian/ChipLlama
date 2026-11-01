import json

import httpx
import pytest

from chipllama.config import Settings
from chipllama.models import Message, ModelError, make_model
from chipllama.models.openai_compat import OpenAICompatModel

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(text="hello"):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}]})


def test_meta_request_shape_and_auth():
    seen = {}

    def h(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return ok("pong")

    s = Settings(meta_api_key="k-test")
    m = make_model(s, client=mock_client(h))
    out = m.complete([Message("system", "sys"), Message("user", "hi", [PNG])])
    assert out == "pong"
    assert seen["url"] == "https://api.meta.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer k-test"
    b = seen["body"]
    assert b["model"] == "muse-spark-1.3" and b["reasoning_effort"] == "high" and b["max_tokens"] == 16000
    parts = b["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_make_model_uses_meta_settings():
    s = Settings(meta_api_key="k", meta_base_url="https://x.test/v1/", meta_model="m-9",
                 meta_reasoning_effort="low", max_tokens=4000)
    m = make_model(s, client=mock_client(lambda req: ok()))
    assert m.name == "meta"
    assert m.base_url == "https://x.test/v1" and m.model == "m-9"
    assert m.reasoning_effort == "low" and m.max_tokens == 4000


def test_missing_key_is_clear_error():
    with pytest.raises(ModelError, match="API key"):
        make_model(Settings()).complete([Message("user", "x")])


def test_images_only_in_user_messages_and_limit():
    m = OpenAICompatModel("t", "http://x", "k", "m")
    with pytest.raises(ModelError):
        m.build_payload([Message("system", "s", [PNG])])
    with pytest.raises(ModelError, match="too many"):
        m.build_payload([Message("user", "s", [PNG] * 51)])
    with pytest.raises(ModelError, match="unsupported image"):
        m.build_payload([Message("user", "s", [b"BM...."])])


def test_retry_on_429_then_success(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429, text="slow down") if len(calls) < 3 else ok("finally")

    m = OpenAICompatModel("t", "http://x", "k", "m", client=mock_client(h))
    assert m.complete([Message("user", "x")]) == "finally" and len(calls) == 3


def test_400_is_not_retried_and_reports_body():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(400, text="reasoning_effort none invalid")

    m = OpenAICompatModel("t", "http://x", "k", "m", client=mock_client(h))
    with pytest.raises(ModelError, match="400.*reasoning_effort") as e:
        m.complete([Message("user", "x")])
    assert len(calls) == 1 and e.value.status == 400


def test_think_blocks_and_list_content_are_cleaned():
    assert OpenAICompatModel.parse_response({"choices": [{"message": {"content": "<think>x</think>answer"}}]}) == "answer"
    assert OpenAICompatModel.parse_response({"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]}) == "ab"
    with pytest.raises(ModelError):
        OpenAICompatModel.parse_response({"error": "x"})