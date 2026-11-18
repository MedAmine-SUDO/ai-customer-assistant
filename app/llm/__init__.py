from functools import cache

from app.core.config import settings
from app.llm.base import LLM, ChatResult, LLMUnavailable, ToolCall, Usage

__all__ = ["LLM", "ChatResult", "LLMUnavailable", "ToolCall", "Usage", "get_llm"]


@cache
def get_llm() -> LLM:
    # Gemini goes through its OpenAI-compatible endpoint: same client, other URL and key.
    if settings.llm_provider in ("openai", "gemini"):
        from app.llm.openai_llm import OpenAILLM

        return OpenAILLM()
    from app.llm.mock_llm import MockLLM

    return MockLLM()
