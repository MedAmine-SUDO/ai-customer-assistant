from dataclasses import dataclass, field
from typing import Protocol


class LLMUnavailable(Exception):
    """The provider refused or failed (rate limit, quota, outage): answer politely, retry later."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0

    def __iadd__(self, other: "Usage") -> "Usage":
        self.prompt_tokens += other.prompt_tokens
        self.cached_tokens += other.cached_tokens
        self.completion_tokens += other.completion_tokens
        self.embedding_tokens += other.embedding_tokens
        return self


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    # Provider fields to send back with the call (Gemini 3's thought_signature is required).
    extra: dict = field(default_factory=dict)


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class LLM(Protocol):
    name: str

    def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]: ...

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult: ...

    def extract_pdf(
        self, pdf: bytes, filename: str, instructions: str, schema: dict
    ) -> tuple[dict, Usage]: ...
