from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Message:
    role: str                       # "system" | "user" | "assistant"
    text: str
    images: list[bytes] = field(default_factory=list)   # raw image bytes (png/jpeg/gif/webp), user role only


class ModelError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ChatModel(Protocol):
    name: str

    def complete(self, messages: list[Message]) -> str: ...


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ModelError("unsupported image type (need png, jpeg, gif or webp)")


def data_url(data: bytes) -> str:
    return f"data:{sniff_mime(data)};base64,{base64.b64encode(data).decode()}"
