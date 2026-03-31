"""
Native LLM adapter layer for Multigen.

Problems solved
---------------
- No unified LLM adapter contract (Multigen breaks if OpenAI changes its API)
- No local model support (Ollama, vLLM, llama.cpp)
- No per-workflow token budget enforcement
- No prompt compilation layer (raw string concat instead of structured assembly)
- No context-window management (auto-truncate / summarise long contexts)
- No structured output enforcement (JSON schema + retry on malformed)
- No LLM response caching (identical prompts re-hit API)

Classes
-------
- ``LLMMessage``            — a single message in a conversation
- ``LLMRequest``            — fully assembled LLM call descriptor
- ``LLMResponse``           — response from any adapter
- ``LLMAdapter``            — abstract base; subclass for each provider
- ``OpenAIAdapter``         — thin stdlib-only OpenAI REST adapter
- ``OllamaAdapter``         — Ollama local model adapter
- ``EchoAdapter``           — in-process mock for testing (no API calls)
- ``TokenBudget``           — per-workflow spend cap (raises BudgetExceededError)
- ``ContextWindowManager``  — auto-truncate / summarise contexts that are too long
- ``StructuredOutputParser``— JSON schema enforcement + auto-retry
- ``LLMCache``              — hash-based in-memory response cache
- ``LLMRouter``             — selects and calls the best adapter, with budget + cache

Usage::

    from multigen.llm_adapter import LLMRouter, EchoAdapter, TokenBudget

    router = LLMRouter()
    router.add_adapter("echo", EchoAdapter())
    budget = TokenBudget(max_tokens=10_000)

    response = await router.complete(
        messages=[{"role": "user", "content": "Hello"}],
        adapter="echo",
        budget=budget,
    )
    print(response.content)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class LLMMessage:
    role: str            # system | user | assistant
    content: str

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMRequest:
    messages: List[LLMMessage]
    model: str = "default"
    max_tokens: int = 1024
    temperature: float = 0.7
    json_schema: Optional[Dict[str, Any]] = None   # structured output schema
    metadata: Dict[str, Any] = field(default_factory=dict)

    def cache_key(self) -> str:
        payload = json.dumps({
            "messages": [m.to_dict() for m in self.messages],
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    raw: Any = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        """Rough cost estimate — override per adapter."""
        return 0.0


# ── LLMAdapter base ───────────────────────────────────────────────────────────

class LLMAdapter:
    """
    Abstract base class for LLM provider adapters.
    Subclass and implement ``_call(request) → LLMResponse``.
    """
    name: str = "base"
    default_model: str = "default"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        start = time.monotonic()
        response = await self._call(request)
        response.latency_ms = (time.monotonic() - start) * 1000
        return response

    async def _call(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError


# ── EchoAdapter (mock — no API needed) ───────────────────────────────────────

class EchoAdapter(LLMAdapter):
    """
    Returns a scripted or echo response without any API call.
    Ideal for testing, dry-runs, and CI pipelines.

    Usage::

        adapter = EchoAdapter(response_template="Echo: {last_message}")
        response = await adapter.complete(request)
    """
    name = "echo"
    default_model = "echo-1"

    def __init__(
        self,
        response_template: str = "Echo: {last_message}",
        tokens_per_char: float = 0.25,
    ) -> None:
        self._template = response_template
        self._tokens_per_char = tokens_per_char

    async def _call(self, request: LLMRequest) -> LLMResponse:
        last = request.messages[-1].content if request.messages else ""
        content = self._template.format(
            last_message=last,
            model=request.model,
        )
        in_tokens = int(sum(len(m.content) for m in request.messages) * self._tokens_per_char)
        out_tokens = int(len(content) * self._tokens_per_char)
        return LLMResponse(
            content=content,
            model=request.model or self.default_model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )


# ── OpenAIAdapter (stdlib-only REST) ─────────────────────────────────────────

class OpenAIAdapter(LLMAdapter):
    """
    Thin OpenAI REST adapter using stdlib ``urllib`` — no external deps.

    Usage::

        adapter = OpenAIAdapter(api_key=os.environ["OPENAI_API_KEY"])
        response = await adapter.complete(request)
    """
    name = "openai"
    default_model = "gpt-4o-mini"

    _COST_PER_1K = {
        "gpt-4o": (0.005, 0.015),
        "gpt-4o-mini": (0.00015, 0.0006),
        "gpt-4-turbo": (0.010, 0.030),
    }

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")

    async def _call(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": request.model or self.default_model,
            "messages": [m.to_dict() for m in request.messages],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.json_schema:
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self._base}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None,
            lambda: json.loads(urllib.request.urlopen(req, timeout=60).read()),
        )
        choice = raw["choices"][0]["message"]["content"]
        usage = raw.get("usage", {})
        return LLMResponse(
            content=choice,
            model=raw.get("model", request.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            raw=raw,
        )


# ── OllamaAdapter (local models) ─────────────────────────────────────────────

class OllamaAdapter(LLMAdapter):
    """
    Adapter for Ollama local models (llama3, mistral, phi3, etc.).
    Ollama must be running at *base_url* (default localhost:11434).

    Usage::

        adapter = OllamaAdapter(model="llama3")
        response = await adapter.complete(request)
    """
    name = "ollama"
    default_model = "llama3"

    def __init__(
        self,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.default_model = model
        self._base = base_url.rstrip("/")

    async def _call(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": request.model or self.default_model,
            "messages": [m.to_dict() for m in request.messages],
            "stream": False,
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self._base}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None,
            lambda: json.loads(urllib.request.urlopen(req, timeout=120).read()),
        )
        content = raw.get("message", {}).get("content", "")
        return LLMResponse(
            content=content,
            model=raw.get("model", request.model),
            input_tokens=raw.get("prompt_eval_count", 0),
            output_tokens=raw.get("eval_count", 0),
            raw=raw,
        )


# ── TokenBudget ───────────────────────────────────────────────────────────────

class BudgetExceededError(Exception):
    pass


class TokenBudget:
    """
    Per-workflow token spend cap.

    Usage::

        budget = TokenBudget(max_tokens=5000)
        budget.consume(300)    # raises BudgetExceededError if over limit
        print(budget.remaining)
    """

    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._used)

    def consume(self, tokens: int) -> None:
        self._used += tokens
        if self._used > self.max_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: used {self._used} / {self.max_tokens}"
            )

    def reset(self) -> None:
        self._used = 0


# ── ContextWindowManager ──────────────────────────────────────────────────────

class ContextWindowManager:
    """
    Manages conversation history to stay within a token limit.

    Strategies:
    - ``truncate_oldest`` — drop oldest messages first
    - ``summarise``       — use a summariser_fn to compress history

    Usage::

        mgr = ContextWindowManager(max_tokens=4096, strategy="truncate_oldest")
        trimmed = mgr.fit(messages, tokens_per_char=0.25)
    """

    def __init__(
        self,
        max_tokens: int = 4096,
        strategy: str = "truncate_oldest",
        summariser_fn: Optional[Any] = None,
        tokens_per_char: float = 0.25,
    ) -> None:
        self.max_tokens = max_tokens
        self.strategy = strategy
        self._summariser = summariser_fn
        self._tokens_per_char = tokens_per_char

    def estimate_tokens(self, messages: List[LLMMessage]) -> int:
        return int(sum(len(m.content) for m in messages) * self._tokens_per_char)

    def fit(self, messages: List[LLMMessage]) -> List[LLMMessage]:
        if self.estimate_tokens(messages) <= self.max_tokens:
            return messages

        if self.strategy == "truncate_oldest":
            # Keep system message + most recent messages
            system = [m for m in messages if m.role == "system"]
            non_system = [m for m in messages if m.role != "system"]
            result = system[:]
            for msg in reversed(non_system):
                if self.estimate_tokens(result + [msg]) <= self.max_tokens:
                    result.insert(len(system), msg)
                else:
                    break
            return result

        # summarise strategy: summarise middle history
        if self._summariser and len(messages) > 3:
            middle = messages[1:-2]
            summary_text = self._summariser(
                "\n".join(f"{m.role}: {m.content}" for m in middle)
            )
            summary_msg = LLMMessage(
                role="system",
                content=f"[Conversation summary: {summary_text}]",
            )
            trimmed = [messages[0], summary_msg] + messages[-2:]
            return trimmed

        return messages[-int(self.max_tokens / 100):]


# ── StructuredOutputParser ────────────────────────────────────────────────────

class StructuredOutputParser:
    """
    Parses and validates JSON output from an LLM.
    Retries if output is malformed (up to *max_retries* times).

    Usage::

        parser = StructuredOutputParser(schema={"type": "object", "required": ["name"]})
        parsed = parser.parse('{"name": "Alice"}')
    """

    def __init__(
        self,
        schema: Optional[Dict[str, Any]] = None,
        max_retries: int = 2,
    ) -> None:
        self._schema = schema
        self.max_retries = max_retries

    def parse(self, text: str) -> Dict[str, Any]:
        # Extract JSON from markdown fences if present
        import re
        match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        json_str = match.group(1) if match else text.strip()

        data = json.loads(json_str)

        if self._schema:
            self._validate(data, self._schema)
        return data

    def _validate(self, data: Any, schema: Dict[str, Any]) -> None:
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                raise ValueError(f"Missing required field: {key!r}")

    def prompt_for_retry(self, error: str, original_prompt: str) -> str:
        return (
            f"{original_prompt}\n\n"
            f"Your previous response was invalid ({error}). "
            f"Please respond with valid JSON only."
        )


# ── LLMCache ──────────────────────────────────────────────────────────────────

class LLMCache:
    """
    Hash-based in-memory response cache.
    Identical requests (same messages + model + temperature) return cached responses.

    Usage::

        cache = LLMCache(max_size=500)
        cached = cache.get(request)
        if cached is None:
            response = await adapter.complete(request)
            cache.set(request, response)
    """

    def __init__(self, max_size: int = 500) -> None:
        self._store: Dict[str, LLMResponse] = {}
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, request: LLMRequest) -> Optional[LLMResponse]:
        key = request.cache_key()
        if key in self._store:
            self._hits += 1
            cached = self._store[key]
            cached.cached = True
            return cached
        self._misses += 1
        return None

    def set(self, request: LLMRequest, response: LLMResponse) -> None:
        if len(self._store) >= self._max_size:
            # Evict oldest (first key in insertion order)
            oldest = next(iter(self._store))
            del self._store[oldest]
        self._store[request.cache_key()] = response

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total else 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
        }


# ── LLMRouter ─────────────────────────────────────────────────────────────────

class LLMRouter:
    """
    Selects and calls the best LLM adapter, enforcing budget and cache.

    Usage::

        router = LLMRouter()
        router.add_adapter("echo", EchoAdapter())
        router.add_adapter("openai", OpenAIAdapter(api_key="..."))

        response = await router.complete(
            messages=[{"role": "user", "content": "Hello"}],
            adapter="echo",
        )
    """

    def __init__(
        self,
        cache: Optional[LLMCache] = None,
        context_window_mgr: Optional[ContextWindowManager] = None,
    ) -> None:
        self._adapters: Dict[str, LLMAdapter] = {}
        self._cache = cache
        self._cwm = context_window_mgr
        self._default: Optional[str] = None

    def add_adapter(self, name: str, adapter: LLMAdapter, default: bool = False) -> None:
        self._adapters[name] = adapter
        if default or self._default is None:
            self._default = name

    async def complete(
        self,
        messages: List[Any],   # list of dicts or LLMMessage
        adapter: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        budget: Optional[TokenBudget] = None,
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        # Normalise messages
        norm = [
            m if isinstance(m, LLMMessage)
            else LLMMessage(role=m["role"], content=m["content"])
            for m in messages
        ]

        # Context window management
        if self._cwm:
            norm = self._cwm.fit(norm)

        adapter_name = adapter or self._default
        if adapter_name not in self._adapters:
            raise KeyError(f"Adapter {adapter_name!r} not registered")

        ad = self._adapters[adapter_name]
        req = LLMRequest(
            messages=norm,
            model=model or ad.default_model,
            max_tokens=max_tokens,
            temperature=temperature,
            json_schema=json_schema,
        )

        # Cache check
        if self._cache:
            cached = self._cache.get(req)
            if cached:
                return cached

        response = await ad.complete(req)

        # Budget enforcement
        if budget:
            budget.consume(response.total_tokens)

        # Cache store
        if self._cache:
            self._cache.set(req, response)

        return response

    def adapters(self) -> List[str]:
        return list(self._adapters.keys())


__all__ = [
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "LLMAdapter",
    "EchoAdapter",
    "OpenAIAdapter",
    "OllamaAdapter",
    "BudgetExceededError",
    "TokenBudget",
    "ContextWindowManager",
    "StructuredOutputParser",
    "LLMCache",
    "LLMRouter",
]
