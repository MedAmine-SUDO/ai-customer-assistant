import base64
import json
import logging

import openai
from openai import OpenAI

from app.core.config import settings
from app.llm.base import ChatResult, LLMUnavailable, ToolCall, Usage

log = logging.getLogger(__name__)
# Errors worth a polite "try again in a minute" rather than a crash (free-tier limits included).
TRANSIENT = (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)


def _is_reasoning_model(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4", "gemini"))


class OpenAILLM:
    """OpenAI, or Gemini through its OpenAI-compatible endpoint: same client, other URL and key."""

    def __init__(self) -> None:
        self.name = settings.llm_provider
        gemini = self.name == "gemini"
        self.client = OpenAI(
            api_key=settings.gemini_api_key if gemini else settings.openai_api_key,
            base_url=(settings.gemini_base_url if gemini else settings.openai_base_url) or None,
            timeout=40,
            # With a backup model, switch to it at once rather than retrying an overloaded one.
            max_retries=0 if settings.fallback_model else 2,
        )
        self._send_effort = bool(settings.reasoning_effort)

    def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        try:
            resp = self.client.embeddings.create(
                model=settings.embedding_model, input=texts, dimensions=settings.embedding_dimensions
            )
        except TRANSIENT as exc:
            raise LLMUnavailable(str(exc)) from exc
        data = resp.data
        # OpenAI numbers the results; Gemini leaves index empty and returns them in input order.
        if all(d.index is not None for d in data):
            data = sorted(data, key=lambda d: d.index)
        vectors = [d.embedding for d in data]
        tokens = getattr(resp.usage, "total_tokens", 0) or 0
        if not tokens:
            # Gemini leaves the usage block out: estimate about 4 characters per token.
            tokens = sum(len(t) for t in texts) // 4
        return vectors, Usage(embedding_tokens=tokens)

    def _complete(self, model: str, **kwargs):
        try:
            return self._complete_once(model, **kwargs)
        except LLMUnavailable:
            fallback = settings.fallback_model
            if not fallback or fallback == model:
                raise
            log.warning("%s unavailable, trying %s", model, fallback)
            return self._complete_once(fallback, retries=1, **kwargs)

    def _complete_once(self, model: str, retries: int | None = None, **kwargs):
        client = self.client if retries is None else self.client.with_options(max_retries=retries)
        try:
            if self._send_effort and _is_reasoning_model(model):
                try:
                    return client.chat.completions.create(
                        model=model, reasoning_effort=settings.reasoning_effort, **kwargs
                    )
                except openai.BadRequestError as exc:
                    if "reasoning" not in str(exc).lower():
                        raise
                    # A model that doesn't take this effort level: carry on with its default.
                    log.warning("reasoning_effort=%s rejected by %s", settings.reasoning_effort, model)
                    self._send_effort = False
            return client.chat.completions.create(model=model, **kwargs)
        except TRANSIENT as exc:
            raise LLMUnavailable(str(exc)) from exc

    @staticmethod
    def _usage(resp) -> Usage:
        u = resp.usage
        if u is None:
            return Usage()
        details = getattr(u, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        return Usage(
            prompt_tokens=u.prompt_tokens, cached_tokens=cached, completion_tokens=u.completion_tokens
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        kwargs = {"messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = self._complete(settings.chat_model, **kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCall(
                id=c.id,
                name=c.function.name,
                arguments=json.loads(c.function.arguments or "{}"),
                extra=c.model_extra or {},
            )
            for c in (msg.tool_calls or [])
            if c.type == "function"
        ]
        return ChatResult(content=msg.content or "", tool_calls=calls, usage=self._usage(resp))

    def extract_pdf(self, pdf: bytes, filename: str, instructions: str, schema: dict) -> tuple[dict, Usage]:
        data_url = "data:application/pdf;base64," + base64.b64encode(pdf).decode()
        # Gemini's compatible endpoint takes a PDF as a data URL in an image_url part.
        document = (
            {"type": "image_url", "image_url": {"url": data_url}}
            if self.name == "gemini"
            else {"type": "file", "file": {"filename": filename, "file_data": data_url}}
        )
        resp = self._complete(
            settings.extraction_model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": [document, {"type": "text", "text": "Extract this document."}]},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "document", "strict": True, "schema": schema},
            },
        )
        return json.loads(resp.choices[0].message.content), self._usage(resp)
