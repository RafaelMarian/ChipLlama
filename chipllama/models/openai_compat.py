"""OpenAI-compatible Chat Completions client (plain httpx).

Single backend:
  * Meta Model API  https://api.meta.ai/v1/chat/completions  (Bearer MODEL_API_KEY, models muse-spark-1.x)
Image parts use the OpenAI shape {"type":"image_url","image_url":{"url":"data:<mime>;base64,..."}}.
"""
from __future__ import annotations

import re
import time

import httpx

from .base import Message, ModelError, data_url, ChatModel

MAX_IMAGES = 50  # Meta Model API limit per request


class OpenAICompatModel:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, *, reasoning_effort: str = "",
                 max_tokens: int = 16000, temperature: float | None = None,
                 client: httpx.Client | None = None, retries: int = 2, timeout: float = 300.0):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self._client = client or httpx.Client(timeout=timeout)

    # -- request building (kept separate so tests can assert the wire format)
    def build_payload(self, messages: list[Message]) -> dict:
        msgs = []
        n_images = 0
        for m in messages:
            if m.images and m.role != "user":
                raise ModelError("images are only allowed in user messages")
            if m.images:
                n_images += len(m.images)
                parts = [{"type": "text", "text": m.text}]
                parts += [{"type": "image_url", "image_url": {"url": data_url(b)}} for b in m.images]
                msgs.append({"role": m.role, "content": parts})
            else:
                msgs.append({"role": m.role, "content": m.text})
        if n_images > MAX_IMAGES:
            raise ModelError(f"too many images ({n_images} > {MAX_IMAGES})")
        payload = {"model": self.model, "messages": msgs, "max_tokens": self.max_tokens}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        return payload

    @staticmethod
    def parse_response(data: dict) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"unexpected response shape: {str(data)[:300]}") from e
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = content or ""
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()

    def complete(self, messages: list[Message]) -> str:
        if not self.api_key:
            raise ModelError("no API key configured (MODEL_API_KEY in .env, see .env.example)")
        payload = self.build_payload(messages)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self._client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as e:
                last = ModelError(f"network error: {e}")
            else:
                if r.status_code == 200:
                    return self.parse_response(r.json())
                last = ModelError(f"HTTP {r.status_code} from {self.name}: {r.text[:400]}", r.status_code)
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt < self.retries:
                time.sleep(min(2 ** attempt, 8))
        assert last is not None
        raise last


def make_model(settings, client: httpx.Client | None = None) -> ChatModel:
    """Build the Meta Model API client from settings (single backend)."""
    return OpenAICompatModel("meta", settings.meta_base_url, settings.meta_api_key, settings.meta_model,
                             reasoning_effort=settings.meta_reasoning_effort, max_tokens=settings.max_tokens, client=client)
