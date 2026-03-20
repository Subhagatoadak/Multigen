"""LLM adapter implementations."""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Dict, Generator, Mapping, Optional

from .interfaces import LLMAdapter, SamplingParams
from .schemas import Message


class _BaseLLMAdapter:
    """Shared helper implementing retry/backoff and streaming wrappers."""

    model: str

    def __init__(self, model: str, *, max_retries: int = 3, backoff: float = 0.5) -> None:
        self.model = model
        self._max_retries = max_retries
        self._backoff = backoff

    # pylint: disable=too-many-arguments
    def _with_retry(
        self,
        fn: Callable[[], Any],
        *,
        cost_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Any:
        attempt = 0
        while True:
            try:
                result = fn()
                if cost_callback is not None:
                    cost_callback({"model": self.model, "attempt": attempt + 1})
                return result
            except Exception as exc:  # pragma: no cover - defensive, tests use FunctionAdapter
                attempt += 1
                if attempt >= self._max_retries:
                    raise
                time.sleep(self._backoff * attempt)

    def _ensure_iterable(self, data: Any) -> Iterable[Any]:
        if isinstance(data, Iterable) and not isinstance(data, (str, bytes, Message)):
            return data
        return (chunk for chunk in [data])


class EnvOpenAIAdapter(_BaseLLMAdapter):
    """Adapter that proxies calls to OpenAI's API if available."""

    def __init__(self, model: str, api_key: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._client = None
        if self.api_key:
            try:  # pragma: no cover - optional dependency
                import openai

                self._client = openai.OpenAI(api_key=self.api_key)
            except Exception as exc:  # pragma: no cover
                raise RuntimeError("openai package not available") from exc

    def _call_openai(self, messages: Sequence[Message], **params: Any) -> str:
        if not self._client:  # pragma: no cover - network path disabled in tests
            raise RuntimeError("OpenAI client not configured")
        def _dump(message: Message) -> Dict[str, Any]:
            serializer = getattr(message, "model_dump", None)
            return serializer() if serializer else message.dict()  # type: ignore[attr-defined]

        response = self._client.chat.completions.create(
            model=self.model,
            messages=[_dump(message) for message in messages],
            **params,
        )
        message = response.choices[0].message
        if hasattr(message, "content"):
            return message.content  # type: ignore[return-value]
        if isinstance(message, Mapping):
            return message.get("content", "")  # type: ignore[return-value]
        return str(message)  # pragma: no cover - defensive fallback

    def generate(
        self,
        prompt: str,
        *,
        sampling: Optional[SamplingParams] = None,
        stream: bool = False,
        response_format: Optional[Dict[str, Any]] = None,
        cost_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> str | Iterable[str]:
        message = Message(role="user", content=prompt)
        chat_result = self.chat(
            [message],
            sampling=sampling,
            stream=stream,
            response_format=response_format,
            cost_callback=cost_callback,
        )
        if stream:
            iterable = self._ensure_iterable(chat_result)

            def _generator() -> Iterator[str]:
                for chunk in iterable:
                    yield chunk.content if isinstance(chunk, Message) else str(chunk)

            return _generator()
        if isinstance(chat_result, Message):
            return chat_result.content
        if isinstance(chat_result, Iterable):
            parts = []
            for chunk in chat_result:
                parts.append(chunk.content if isinstance(chunk, Message) else str(chunk))
            return "".join(parts)
        return str(chat_result)

    def chat(
        self,
        messages: Sequence[Message],
        *,
        sampling: Optional[SamplingParams] = None,
        stream: bool = False,
        response_format: Optional[Dict[str, Any]] = None,
        cost_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Message | Iterable[Message]:
        params: Dict[str, Any] = {"temperature": sampling.get("temperature", 0.0) if sampling else 0.0}
        if response_format:
            params["response_format"] = response_format

        if stream:
            params["stream"] = True

            def call_stream() -> Iterable[Message]:
                raw = self._call_openai(messages, **params)
                # _call_openai returns str today; if OpenAI streaming is used, this branch is skipped
                yield Message(role="assistant", content=str(raw))

            # If openai streaming is available, create yields chunks; otherwise return single-message iterable
            result = self._with_retry(call_stream, cost_callback=cost_callback)
            if isinstance(result, Iterable):
                return (chunk if isinstance(chunk, Message) else Message(role="assistant", content=str(chunk)) for chunk in result)
            return (Message(role="assistant", content=str(result)),)

        def call() -> str:
            return self._call_openai(messages, **params)

        content = self._with_retry(call, cost_callback=cost_callback)
        return Message(role="assistant", content=content)


class FunctionAdapter(_BaseLLMAdapter):
    """Adapter wrapping a Python callable."""

    def __init__(self, fn: Callable[[str], str], model: str = "function", **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self._fn = fn

    def generate(
        self,
        prompt: str,
        *,
        sampling: Optional[SamplingParams] = None,
        stream: bool = False,
        response_format: Optional[Dict[str, Any]] = None,
        cost_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> str | Iterable[str]:
        def call() -> str:
            return self._fn(prompt)

        result = self._with_retry(call, cost_callback=cost_callback)
        if stream:
            return (chunk for chunk in [result])
        return result

    def chat(
        self,
        messages: Sequence[Message],
        *,
        sampling: Optional[SamplingParams] = None,
        stream: bool = False,
        response_format: Optional[Dict[str, Any]] = None,
        cost_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Message | Iterable[Message]:
        prompt = "\n".join(f"{m.role}: {m.content}" for m in messages)
        result = self.generate(prompt, sampling=sampling, stream=stream, response_format=response_format, cost_callback=cost_callback)
        if stream and isinstance(result, Iterable):
            return (Message(role="assistant", content=chunk) for chunk in result)  # type: ignore[misc]
        return Message(role="assistant", content=result if isinstance(result, str) else "".join(result))


class AnthropicAdapter(_BaseLLMAdapter):  # pragma: no cover - placeholder
    def generate(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    def chat(self, *args: Any, **kwargs: Any) -> Message:
        raise NotImplementedError


class GeminiAdapter(_BaseLLMAdapter):  # pragma: no cover - placeholder
    def generate(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    def chat(self, *args: Any, **kwargs: Any) -> Message:
        raise NotImplementedError


class vLLMAdapter(_BaseLLMAdapter):  # pragma: no cover - placeholder
    def generate(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    def chat(self, *args: Any, **kwargs: Any) -> Message:
        raise NotImplementedError


class LlamaCppAdapter(_BaseLLMAdapter):  # pragma: no cover - placeholder
    def generate(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    def chat(self, *args: Any, **kwargs: Any) -> Message:
        raise NotImplementedError


__all__ = [
    "EnvOpenAIAdapter",
    "FunctionAdapter",
    "AnthropicAdapter",
    "GeminiAdapter",
    "vLLMAdapter",
    "LlamaCppAdapter",
]
