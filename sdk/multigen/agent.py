"""
multigen.agent
==============
Self-contained agent primitives — no Kafka, Temporal, or MongoDB required.

Agent types
-----------
BaseAgent       — abstract interface every agent must implement
FunctionAgent   — wrap any Python coroutine as an agent
LLMAgent        — call OpenAI / Gemini / Mistral / Ollama with prompt templates
RouterAgent     — route messages to one of N downstream agents based on a rule
AggregatorAgent — collect N upstream outputs and merge them
FilterAgent     — forward only messages passing a predicate
TransformAgent  — apply a pure transformation function to message content
CircuitBreakerAgent — wrap another agent; trip after K consecutive failures
RetryAgent      — retry failed agent calls with exponential back-off
HumanInLoopAgent — pause and await human approval before forwarding
MemoryAgent     — stateful agent that accumulates context across turns

Decorator
---------
@agent(name="my_agent")
async def my_agent(ctx: Context) -> dict:
    ...

Usage
-----
    from multigen.agent import FunctionAgent, LLMAgent, RouterAgent

    extractor = FunctionAgent("extract", fn=lambda ctx: {"entities": []})
    llm       = LLMAgent("summarise", prompt="Summarise: {input}")
    router    = RouterAgent("router", rules={"positive": pos_agent, "negative": neg_agent})
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Types ───────────────────────────────────────────────────────────────────

Context = Dict[str, Any]
AgentOutput = Dict[str, Any]
AgentFn = Callable[[Context], Coroutine[Any, Any, AgentOutput]]


# ─── Base ────────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """Abstract base for all Multigen agents."""

    def __init__(self, name: str, *, description: str = "", tags: Optional[List[str]] = None) -> None:
        self.name = name
        self.id = str(uuid.uuid4())[:8]
        self.description = description
        self.tags: List[str] = tags or []
        self._call_count = 0
        self._error_count = 0
        self._total_latency_ms = 0.0

    @abstractmethod
    async def run(self, ctx: Context) -> AgentOutput:
        """Execute the agent and return its output."""
        ...

    async def __call__(self, ctx: Context) -> AgentOutput:
        start = time.perf_counter()
        self._call_count += 1
        try:
            result = await self.run(ctx)
            self._total_latency_ms += (time.perf_counter() - start) * 1000
            return result
        except Exception as exc:
            self._error_count += 1
            self._total_latency_ms += (time.perf_counter() - start) * 1000
            raise exc

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "name":           self.name,
            "calls":          self._call_count,
            "errors":         self._error_count,
            "avg_latency_ms": self._total_latency_ms / self._call_count if self._call_count else 0,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


# ─── FunctionAgent ───────────────────────────────────────────────────────────

class FunctionAgent(BaseAgent):
    """Wraps any Python callable (sync or async) as a Multigen agent.

    Examples::

        agent = FunctionAgent("tokenize", fn=lambda ctx: {"tokens": ctx["text"].split()})

        async def fetch_data(ctx):
            return {"rows": await db.query(ctx["sql"])}

        agent = FunctionAgent("fetch", fn=fetch_data)
    """

    def __init__(self, name: str, fn: Callable, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self._fn = fn

    async def run(self, ctx: Context) -> AgentOutput:
        if asyncio.iscoroutinefunction(self._fn):
            result = await self._fn(ctx)
        else:
            result = await asyncio.get_event_loop().run_in_executor(None, self._fn, ctx)
        return result if isinstance(result, dict) else {"output": result}


# ─── LLMAgent ────────────────────────────────────────────────────────────────

class LLMAgent(BaseAgent):
    """Agent that calls an LLM provider.

    Providers: ``openai`` (default), ``gemini``, ``mistral``, ``ollama``.

    Template variables are resolved from the context using ``{key}`` syntax::

        agent = LLMAgent(
            "analyst",
            prompt="Analyse the following data and return JSON insights:\\n{data}",
            provider="openai",
            model="gpt-4o",
        )

    Environment variables read at runtime:
        OPENAI_API_KEY, GEMINI_API_KEY, MISTRAL_API_KEY
    """

    def __init__(
        self,
        name: str,
        prompt: str,
        *,
        provider: str = "openai",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        system_prompt: Optional[str] = None,
        output_key: str = "response",
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self.prompt_template = prompt
        self.provider = provider.lower()
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.output_key = output_key

    def _render(self, ctx: Context) -> str:
        try:
            return self.prompt_template.format(**ctx)
        except KeyError:
            return self.prompt_template

    async def run(self, ctx: Context) -> AgentOutput:
        prompt = self._render(ctx)
        reply = await asyncio.get_event_loop().run_in_executor(
            None, self._call_sync, prompt
        )
        return {self.output_key: reply, "prompt": prompt, "provider": self.provider}

    def _call_sync(self, prompt: str) -> str:
        if self.provider == "openai":
            return self._openai(prompt)
        if self.provider == "gemini":
            return self._gemini(prompt)
        if self.provider == "mistral":
            return self._mistral(prompt)
        if self.provider == "ollama":
            return self._ollama(prompt)
        return self._openai(prompt)

    def _openai(self, prompt: str) -> str:
        import os
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        try:
            from openai import OpenAI  # type: ignore
            client = OpenAI(api_key=api_key)
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.append({"role": "user", "content": prompt})
            resp = client.chat.completions.create(
                model=self.model or "gpt-4o-mini",
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return resp.choices[0].message.content
        except ImportError:
            import json
            import urllib.request
            data = json.dumps({
                "model": self.model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=data,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]

    def _gemini(self, prompt: str) -> str:
        import json
        import os
        import urllib.request
        api_key = os.getenv("GEMINI_API_KEY", "")
        model = self.model or "gemini-1.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]

    def _mistral(self, prompt: str) -> str:
        import json
        import os
        import urllib.request
        api_key = os.getenv("MISTRAL_API_KEY", "")
        data = json.dumps({
            "model": self.model or "mistral-small-latest",
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.mistral.ai/v1/chat/completions",
            data=data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    def _ollama(self, prompt: str) -> str:
        import json
        import os
        import urllib.request
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        data = json.dumps({
            "model": self.model or "llama3",
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())["response"]


# ─── RouterAgent ─────────────────────────────────────────────────────────────

class RouterAgent(BaseAgent):
    """Routes context to one of N sub-agents based on a classifier function.

    Example::

        router = RouterAgent(
            "classifier",
            classifier=lambda ctx: "positive" if ctx["score"] > 0 else "negative",
            routes={
                "positive": positive_agent,
                "negative": negative_agent,
            },
            default=fallback_agent,
        )
    """

    def __init__(
        self,
        name: str,
        classifier: Callable[[Context], str],
        routes: Dict[str, BaseAgent],
        default: Optional[BaseAgent] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self._classifier = classifier
        self._routes = routes
        self._default = default

    async def run(self, ctx: Context) -> AgentOutput:
        key = self._classifier(ctx)
        agent = self._routes.get(key, self._default)
        if agent is None:
            return {"routed_to": None, "key": key, "error": "no matching route"}
        result = await agent(ctx)
        return {"routed_to": agent.name, "key": key, **result}


# ─── AggregatorAgent ─────────────────────────────────────────────────────────

class AggregatorAgent(BaseAgent):
    """Merge outputs from multiple upstream agents.

    Default strategy: ``merge`` (dict union).  Supply a custom ``merge_fn``
    for weighted averaging, voting, etc.
    """

    def __init__(
        self,
        name: str,
        agents: List[BaseAgent],
        merge_fn: Optional[Callable[[List[AgentOutput]], AgentOutput]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self._agents = agents
        self._merge_fn = merge_fn or _default_merge

    async def run(self, ctx: Context) -> AgentOutput:
        results = await asyncio.gather(*[a(ctx) for a in self._agents], return_exceptions=True)
        outputs = []
        for agent, r in zip(self._agents, results):
            if isinstance(r, BaseException):
                logger.warning("AggregatorAgent: %s failed: %s", agent.name, r)
                outputs.append({"error": str(r), "agent": agent.name})
            else:
                outputs.append({**r, "agent": agent.name})
        merged = self._merge_fn(outputs)
        return {"outputs": outputs, "merged": merged}


def _default_merge(outputs: List[AgentOutput]) -> AgentOutput:
    merged: AgentOutput = {}
    for out in outputs:
        merged.update(out)
    return merged


# ─── FilterAgent ─────────────────────────────────────────────────────────────

class FilterAgent(BaseAgent):
    """Forward context only if predicate returns True; else return filtered=True."""

    def __init__(self, name: str, predicate: Callable[[Context], bool], downstream: BaseAgent, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self._predicate = predicate
        self._downstream = downstream

    async def run(self, ctx: Context) -> AgentOutput:
        if self._predicate(ctx):
            return await self._downstream(ctx)
        return {"filtered": True, "reason": "predicate returned False"}


# ─── TransformAgent ──────────────────────────────────────────────────────────

class TransformAgent(BaseAgent):
    """Apply a pure transformation to the context and pass to downstream."""

    def __init__(
        self, name: str, transform: Callable[[Context], Context],
        downstream: Optional[BaseAgent] = None, **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self._transform = transform
        self._downstream = downstream

    async def run(self, ctx: Context) -> AgentOutput:
        new_ctx = self._transform(ctx)
        if self._downstream:
            return await self._downstream(new_ctx)
        return new_ctx if isinstance(new_ctx, dict) else {"output": new_ctx}


# ─── CircuitBreakerAgent ─────────────────────────────────────────────────────

class CircuitBreakerAgent(BaseAgent):
    """Wrap an agent and trip open after ``failure_threshold`` consecutive errors.

    States: CLOSED (normal) → OPEN (blocking) → HALF_OPEN (testing recovery).
    """

    def __init__(self, name: str, agent: BaseAgent, failure_threshold: int = 3,
                 recovery_timeout: float = 30.0, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self._agent = agent
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failures = 0
        self._state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._opened_at: float = 0.0

    async def run(self, ctx: Context) -> AgentOutput:
        now = time.time()
        if self._state == "OPEN":
            if now - self._opened_at >= self._recovery_timeout:
                self._state = "HALF_OPEN"
            else:
                return {
                    "circuit_open": True,
                    "agent": self._agent.name,
                    "retry_after": self._recovery_timeout - (now - self._opened_at),
                }

        try:
            result = await self._agent(ctx)
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                self._failures = 0
            return result
        except Exception as exc:
            self._failures += 1
            if self._failures >= self._threshold:
                self._state = "OPEN"
                self._opened_at = time.time()
                logger.warning("CircuitBreaker %s OPEN after %d failures", self.name, self._failures)
            raise exc


# ─── RetryAgent ──────────────────────────────────────────────────────────────

class RetryAgent(BaseAgent):
    """Retry a failing agent with exponential back-off."""

    def __init__(self, name: str, agent: BaseAgent, max_retries: int = 3,
                 base_delay: float = 1.0, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self._agent = agent
        self._max_retries = max_retries
        self._base_delay = base_delay

    async def run(self, ctx: Context) -> AgentOutput:
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._agent(ctx)
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    delay = self._base_delay * (2 ** attempt)
                    logger.warning(
                        "RetryAgent %s attempt %d failed, retrying in %.1fs: %s",
                        self.name, attempt + 1, delay, exc,
                    )
                    await asyncio.sleep(delay)
        raise RuntimeError(f"RetryAgent {self.name}: all {self._max_retries} retries exhausted") from last_exc


# ─── HumanInLoopAgent ────────────────────────────────────────────────────────

class HumanInLoopAgent(BaseAgent):
    """Pause execution and await human approval.

    In automated pipelines, set ``auto_approve=True`` or provide an
    ``approval_fn`` that returns ``(approved: bool, feedback: str)``.
    """

    def __init__(
        self,
        name: str,
        downstream: BaseAgent,
        approval_fn: Optional[Callable[[Context], tuple[bool, str]]] = None,
        auto_approve: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self._downstream = downstream
        self._approval_fn = approval_fn
        self._auto_approve = auto_approve

    async def run(self, ctx: Context) -> AgentOutput:
        if self._auto_approve:
            approved, feedback = True, "auto-approved"
        elif self._approval_fn:
            approved, feedback = self._approval_fn(ctx)
        else:
            # Interactive terminal approval
            print(f"\n[HumanInLoop:{self.name}] Context preview:\n{ctx}\nApprove? [y/n] ", end="")
            ans = await asyncio.get_event_loop().run_in_executor(None, input, "")
            approved = ans.strip().lower().startswith("y")
            feedback = "approved" if approved else "rejected"

        if not approved:
            return {"approved": False, "feedback": feedback, "halted": True}
        result = await self._downstream(ctx)
        return {"approved": True, "feedback": feedback, **result}


# ─── MemoryAgent ─────────────────────────────────────────────────────────────

class MemoryAgent(BaseAgent):
    """Stateful agent that accumulates context across invocations.

    Useful for multi-turn conversations and iterative refinement loops.
    Supports windowed memory (keep last N messages).
    """

    def __init__(self, name: str, downstream: BaseAgent, window: int = 20, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self._downstream = downstream
        self._window = window
        self._history: List[Dict[str, Any]] = []

    async def run(self, ctx: Context) -> AgentOutput:
        augmented = {**ctx, "history": self._history[-self._window:]}
        result = await self._downstream(augmented)
        self._history.append({"input": ctx, "output": result})
        if len(self._history) > self._window * 2:
            self._history = self._history[-self._window:]
        return {**result, "history_length": len(self._history)}

    def clear_memory(self) -> None:
        self._history.clear()


# ─── @agent decorator ────────────────────────────────────────────────────────

def agent(
    name: Optional[str] = None,
    *,
    description: str = "",
    tags: Optional[List[str]] = None,
) -> Callable[[AgentFn], FunctionAgent]:
    """Decorator that turns a coroutine into a :class:`FunctionAgent`.

    Usage::

        @agent(name="summarise")
        async def summarise(ctx: dict) -> dict:
            return {"summary": ctx["text"][:200]}
    """
    def decorator(fn: AgentFn) -> FunctionAgent:
        agent_name = name or fn.__name__
        return FunctionAgent(agent_name, fn=fn, description=description or (fn.__doc__ or ""), tags=tags)
    return decorator
