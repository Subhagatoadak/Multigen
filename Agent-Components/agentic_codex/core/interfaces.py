"""Protocol definitions for the core extension points."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Dict, Optional, Protocol, runtime_checkable, TYPE_CHECKING

from .schemas import AgentStep, Message, RunResult

if TYPE_CHECKING:  # pragma: no cover
    from .agent import Context


SamplingParams = Dict[str, Any]


@runtime_checkable
class LLMAdapter(Protocol):
    """Adapter interface for LLM providers."""

    model: str

    def generate(
        self,
        prompt: str,
        *,
        sampling: Optional[SamplingParams] = None,
        stream: bool = False,
        response_format: Optional[Mapping[str, Any]] = None,
        cost_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> str | Iterable[str]:
        ...

    def chat(
        self,
        messages: Sequence[Message],
        *,
        sampling: Optional[SamplingParams] = None,
        stream: bool = False,
        response_format: Optional[Mapping[str, Any]] = None,
        cost_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Message | Iterable[Message]:
        ...


@runtime_checkable
class Skill(Protocol):
    """Protocol for executable tools."""

    name: str

    def run(self, **kwargs: Any) -> Any:
        ...


@runtime_checkable
class Memory(Protocol):
    """Protocol for basic memory stores."""

    def put(self, key: str, value: Any) -> None:
        ...

    def get(self, key: str) -> Any:
        ...

    def search(self, query: str, *, k: int = 5) -> Sequence[Any]:
        ...


@runtime_checkable
class GraphMemory(Protocol):
    """Protocol for graph storage."""

    def add_edge(self, src: str, dest: str, meta: Optional[Mapping[str, Any]] = None) -> None:
        ...

    def query(self, node: str) -> Sequence[Mapping[str, Any]]:
        ...


@runtime_checkable
class RunStore(Protocol):
    """Durable storage for run metadata."""

    def append(self, event: Mapping[str, Any]) -> None:
        ...

    def query(self, **filters: Any) -> Sequence[Mapping[str, Any]]:
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Binary or large object storage."""

    def put(self, artifact_id: str, data: bytes, *, mime: str, meta: Optional[Mapping[str, Any]] = None) -> str:
        ...

    def get(self, artifact_id: str) -> bytes:
        ...


@runtime_checkable
class Coordinator(Protocol):
    """Coordinator orchestrates agents and tools."""

    def run(self, goal: str, inputs: Mapping[str, Any]) -> RunResult:
        ...


@runtime_checkable
class Evaluator(Protocol):
    """Evaluation interface returning named metrics."""

    def score(self, run: RunResult) -> Dict[str, float]:
        ...


class GuardResult(Dict[str, Any]):
    """Result object returned by guard checks."""

    @property
    def ok(self) -> bool:  # pragma: no cover - trivial
        return bool(self.get("ok", False))


@runtime_checkable
class Guard(Protocol):
    """Guardrail protocol used by coordinators."""

    name: str

    def check(self, state: Mapping[str, Any]) -> GuardResult:
        ...


@runtime_checkable
class ReinforcementLearner(Protocol):
    """Trainer interface for reinforcement learning hooks."""

    def update(self, context: "Context", step: AgentStep, environment: Any | None = None) -> None:
        ...

    # Optional lifecycle hooks; implementers may define these as needed.
    # Using duck typing so capability checks via getattr rather than relying on inheritance.
