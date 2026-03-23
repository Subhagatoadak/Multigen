"""
Per-node circuit breaker for GraphWorkflow.

States:
    CLOSED     — normal; failures tracked, execution proceeds
    OPEN       — node is failing; short-circuits to fallback or skip
    HALF_OPEN  — testing recovery; one probe allowed

Recovery is iteration-count based (not time-based) so it is deterministic
inside a Temporal workflow replay.

Trip threshold: consecutive_failures >= trip_threshold → OPEN
Recovery: after recovery_executions total graph iterations in OPEN → HALF_OPEN
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _NodeBreaker:
    trip_threshold: int
    recovery_executions: int
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at_execution: int = 0  # total_executed count when tripped


class CircuitBreakerRegistry:
    """
    Holds per-node circuit breakers for an entire GraphWorkflow run.

    Designed to live entirely in workflow instance memory (no I/O) so
    Temporal can replay the workflow deterministically.

    Usage (inside GraphWorkflow):
        cb = CircuitBreakerRegistry(trip_threshold=3, recovery_executions=5)
        if not cb.can_execute("my_node", total_executed):
            # open — route to fallback
        ...
        cb.record_success("my_node")   # on clean agent_activity result
        cb.record_failure("my_node", total_executed)  # on exception / error key
    """

    def __init__(
        self,
        trip_threshold: int = 3,
        recovery_executions: int = 5,
    ) -> None:
        self._trip_threshold = trip_threshold
        self._recovery_executions = recovery_executions
        self._breakers: Dict[str, _NodeBreaker] = {}

    def _get(self, node_id: str) -> _NodeBreaker:
        if node_id not in self._breakers:
            self._breakers[node_id] = _NodeBreaker(
                trip_threshold=self._trip_threshold,
                recovery_executions=self._recovery_executions,
            )
        return self._breakers[node_id]

    def can_execute(self, node_id: str, total_executed: int) -> bool:
        """
        Return True if the node should execute.
        Transitions OPEN → HALF_OPEN automatically once enough
        total graph iterations have passed.
        """
        b = self._get(node_id)
        if b.state == CircuitState.CLOSED:
            return True
        if b.state == CircuitState.OPEN:
            elapsed = total_executed - b.opened_at_execution
            if elapsed >= b.recovery_executions:
                b.state = CircuitState.HALF_OPEN
                return True  # single probe
            return False
        # HALF_OPEN — allow probe
        return True

    def record_success(self, node_id: str) -> None:
        b = self._get(node_id)
        b.consecutive_failures = 0
        b.state = CircuitState.CLOSED

    def record_failure(self, node_id: str, total_executed: int) -> None:
        b = self._get(node_id)
        b.consecutive_failures += 1
        if b.state == CircuitState.HALF_OPEN or b.consecutive_failures >= b.trip_threshold:
            b.state = CircuitState.OPEN
            b.opened_at_execution = total_executed

    def status(self) -> Dict[str, Dict]:
        """Serialisable snapshot for health queries."""
        return {
            node_id: {
                "state": b.state.value,
                "consecutive_failures": b.consecutive_failures,
                "trip_threshold": b.trip_threshold,
                "opened_at_execution": b.opened_at_execution,
            }
            for node_id, b in self._breakers.items()
        }
