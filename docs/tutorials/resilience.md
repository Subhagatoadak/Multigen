# Tutorial — Resilience Patterns

## Overview

Production multi-agent systems fail in creative ways: external APIs go down, LLMs return malformed JSON, rate limits are hit, and agents produce low-confidence outputs that shouldn't be acted on. Multigen provides a suite of resilience primitives that make your pipelines robust without making them brittle.

| Pattern | Problem solved |
|---------|---------------|
| `GuardrailSandwich` | Validate input/output, circuit-break external services |
| Retry with backoff | Transient failures (rate limits, network blips) |
| Confidence gating | Only proceed when confidence is high enough |
| `TwoPersonRuleCoordinator` | High-stakes decisions require dual approval |
| Memory-backed fallback | Degrade to cached results when services are down |
| Human-in-the-loop gate | Mandatory human review for critical decisions |

---

## 1. GuardrailSandwich (Circuit Breaker)

`GuardrailSandwich` wraps any agent with:
- **Pre-filter**: validates inputs before calling the primary agent
- **Post-filter**: validates outputs after the primary agent returns
- **Short-circuit**: if the pre-filter sets `stop=True`, the primary agent is skipped entirely

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.patterns import GuardrailSandwich

# Pre-filter: check required fields exist
def pre_validate(ctx: Context) -> AgentStep:
    required = ["api_key", "query", "user_id"]
    missing  = [r for r in required if r not in ctx.scratch]
    if missing:
        return AgentStep(
            out_messages=[Message(role="system",
                                 content=f"PRE_VALIDATE FAILED: missing {missing}")],
            state_updates={"validation_error": f"Missing: {missing}"},
            stop=True,  # ← short-circuit: skip primary agent
        )
    return AgentStep(
        out_messages=[Message(role="system", content="PRE_VALIDATE OK")],
        state_updates={"pre_validated": True},
        stop=False,
    )

# Primary agent: calls an external API
def external_api_step(ctx: Context) -> AgentStep:
    # Simulate external API call
    query  = ctx.scratch.get("query", "")
    result = {"data": f"API response for: {query}", "status": "ok"}
    return AgentStep(
        out_messages=[Message(role="assistant", content=str(result))],
        state_updates={"api_result": result},
        stop=False,
    )

# Post-filter: validate the API response
def post_validate(ctx: Context) -> AgentStep:
    result = ctx.scratch.get("api_result", {})
    if result.get("status") != "ok":
        return AgentStep(
            out_messages=[Message(role="system",
                                 content=f"POST_VALIDATE FAILED: bad status {result.get('status')}") ],
            state_updates={"post_validation_error": True},
            stop=False,
        )
    return AgentStep(
        out_messages=[Message(role="system", content="POST_VALIDATE OK")],
        state_updates={"post_validated": True},
        stop=False,
    )

pre_guard   = AgentBuilder("pre",   "validator").with_step(pre_validate).build()
primary     = AgentBuilder("api",   "api-agent").with_step(external_api_step).build()
post_guard  = AgentBuilder("post",  "validator").with_step(post_validate).build()

guarded = GuardrailSandwich(
    prefilter=pre_guard,
    primary=primary,
    postfilter=post_guard,
)

# Test: valid inputs
ctx = Context(goal="Query external API")
ctx.scratch = {"api_key": "sk-test", "query": "get analytics", "user_id": "u-001"}
result = guarded.run(goal=ctx.goal, inputs=ctx.scratch)
print("Valid inputs:", [msg.content for msg in result.messages])

# Test: missing required field (pre-filter short-circuits)
ctx2 = Context(goal="Query external API")
ctx2.scratch = {"query": "get analytics"}  # missing api_key and user_id
result2 = guarded.run(goal=ctx2.goal, inputs=ctx2.scratch)
print("Missing inputs:", [msg.content for msg in result2.messages])
```

### Circuit Breaker State Machine

Implement a proper circuit breaker with CLOSED/OPEN/HALF_OPEN states:

```python
import time
from enum import Enum

class CircuitState(Enum):
    CLOSED    = "CLOSED"     # normal operation
    OPEN      = "OPEN"       # blocking calls after too many failures
    HALF_OPEN = "HALF_OPEN"  # testing if service recovered

class CircuitBreaker:
    """Simple circuit breaker for protecting external service calls."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ):
        self.failure_threshold  = failure_threshold
        self.recovery_timeout   = recovery_timeout
        self.success_threshold  = success_threshold

        self.state              = CircuitState.CLOSED
        self.failure_count      = 0
        self.success_count      = 0
        self.last_failure_time  = 0.0

    def can_call(self) -> bool:
        """Returns True if a call should be attempted."""
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                print(f"  [CB] {CircuitState.HALF_OPEN.value} — testing recovery")
                return True
            return False
        else:  # HALF_OPEN
            return True

    def record_success(self):
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.success_count = 0
                print(f"  [CB] {CircuitState.CLOSED.value} — service recovered")

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            print(f"  [CB] {CircuitState.OPEN.value} — too many failures ({self.failure_count})")


# Usage
cb = CircuitBreaker(failure_threshold=3, recovery_timeout=5.0)

def protected_call(query: str) -> dict:
    if not cb.can_call():
        return {"error": "circuit_open", "fallback": "cached_data"}
    try:
        # Simulate external call (may fail)
        if "error" in query:
            raise ConnectionError("External service unavailable")
        cb.record_success()
        return {"data": f"result for {query}"}
    except Exception as exc:
        cb.record_failure()
        return {"error": str(exc)}

# Demonstrate circuit breaking
for i, query in enumerate(["ok", "ok", "error", "error", "error", "ok", "ok"]):
    result = protected_call(query)
    print(f"  Call {i+1}: query={query:5s}  state={cb.state.value:9s}  result={result}")
```

---

## 2. Retry with Exponential Backoff

```python
import time
import functools
from typing import Callable, Type

def with_retry(
    fn: Callable,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
):
    """Execute fn with exponential backoff retry."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        last_exception = None
        for attempt in range(max_attempts):
            try:
                return fn(*args, **kwargs)
            except retryable_exceptions as exc:
                last_exception = exc
                if attempt < max_attempts - 1:
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    print(f"  Retry {attempt+1}/{max_attempts}: sleeping {delay:.1f}s after: {exc}")
                    time.sleep(delay)
        raise last_exception
    return wrapper


# Apply to an agent step
import random

@with_retry(max_attempts=3, base_delay=0.1, retryable_exceptions=(ConnectionError,))
def flaky_api_call(query: str) -> dict:
    if random.random() < 0.6:   # 60% chance of failure
        raise ConnectionError("Service temporarily unavailable")
    return {"data": f"result for {query}"}

# Test
try:
    result = flaky_api_call("test query")
    print(f"Success: {result}")
except ConnectionError as e:
    print(f"All retries exhausted: {e}")
```

---

## 3. Confidence Gating

Only proceed to the next stage when the confidence score exceeds a threshold:

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message

CONFIDENCE_GATE = 0.75

def confidence_gate_step(ctx: Context) -> AgentStep:
    """
    A pass-through gate that blocks pipeline execution if confidence is too low.
    Place this stage between analysis and action stages.
    """
    confidence = ctx.scratch.get("confidence", 0.0)
    passed     = confidence >= CONFIDENCE_GATE

    action = "PROCEED" if passed else "BLOCK_LOW_CONFIDENCE"
    reason = (
        f"confidence={confidence:.0%} >= gate={CONFIDENCE_GATE:.0%} → {action}"
    )

    return AgentStep(
        out_messages=[Message(role="system", content=reason)],
        state_updates={
            "gate_passed": passed,
            "gate_reason": reason,
            "blocked": not passed,
        },
        stop=not passed,   # stop=True blocks downstream stages
    )

gate_agent = AgentBuilder("confidence_gate", "gate").with_step(confidence_gate_step).build()

# Test: low confidence
ctx_low = Context(goal="test")
ctx_low.scratch["confidence"] = 0.45
result = gate_agent.run(ctx_low)
print(f"Low confidence: {result.out_messages[-1].content}")  # → BLOCK

# Test: high confidence
ctx_high = Context(goal="test")
ctx_high.scratch["confidence"] = 0.82
result = gate_agent.run(ctx_high)
print(f"High confidence: {result.out_messages[-1].content}")  # → PROCEED
```

---

## 4. TwoPersonRuleCoordinator

Require two independent agents to both approve before proceeding. Essential for high-stakes decisions (large transactions, medical treatments, destructive infrastructure changes):

```python
from agentic_codex.patterns import TwoPersonRuleCoordinator

def approver_a_step(ctx: Context) -> AgentStep:
    """First approver: checks technical validity."""
    risk   = ctx.scratch.get("risk_score", 0.5)
    approves = risk < 0.6
    return AgentStep(
        out_messages=[Message(role="assistant",
                             content=f"ApproverA: {'APPROVE' if approves else 'REJECT'} (risk={risk})")],
        state_updates={"approver_a_decision": "approve" if approves else "reject"},
        stop=False,
    )

def approver_b_step(ctx: Context) -> AgentStep:
    """Second approver: checks business validity."""
    business_score = ctx.scratch.get("business_score", 0.5)
    approves = business_score >= 0.7
    return AgentStep(
        out_messages=[Message(role="assistant",
                             content=f"ApproverB: {'APPROVE' if approves else 'REJECT'} (biz={business_score})")],
        state_updates={"approver_b_decision": "approve" if approves else "reject"},
        stop=False,
    )

approver_a = AgentBuilder("approver_a", "approver").with_step(approver_a_step).build()
approver_b = AgentBuilder("approver_b", "approver").with_step(approver_b_step).build()

two_person_rule = TwoPersonRuleCoordinator(
    first=approver_a,
    second=approver_b,
)

result = two_person_rule.run(
    goal="Approve $500k loan disbursement",
    inputs={"risk_score": 0.35, "business_score": 0.82}
)

print("Two-person rule messages:")
for msg in result.messages:
    print(f"  {msg.content}")
```

---

## 5. Human-in-the-Loop Gate

Block pipeline execution until a human provides a decision:

```python
import json
from datetime import datetime, timezone

def human_review_gate(
    ctx: Context,
    review_summary: dict,
    auto_approve_demo: bool = True,
) -> dict:
    """
    Mandatory human review gate.

    PRODUCTION: This function would:
    1. Push a task to a workflow management system (Jira, ServiceNow, custom)
    2. Send notifications (Slack, email, pager)
    3. Block execution and WAIT for async human input
    4. Record approver ID, timestamp, and any modifications
    5. Resume pipeline only after approval

    In this demo, we simulate auto-approval.
    """
    print("\n" + "="*60)
    print("  HUMAN REVIEW REQUIRED")
    print("="*60)
    print(f"  Summary: {json.dumps(review_summary, indent=4)}")
    print()

    if auto_approve_demo:
        decision  = "APPROVED"
        reviewer  = "human.reviewer@company.com"
        notes     = "Reviewed and approved with no modifications."
    else:
        # Production: block here and wait for external signal
        decision = "PENDING"
        reviewer = "UNASSIGNED"
        notes    = ""

    gate_result = {
        "decision": decision,
        "reviewer": reviewer,
        "notes": notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "approved": decision == "APPROVED",
    }

    ctx.scratch["human_review"] = gate_result
    ctx.scratch["human_approved"] = gate_result["approved"]

    print(f"  Decision: {decision} by {reviewer}")
    return gate_result


# Use in a pipeline
def action_step(ctx: Context) -> AgentStep:
    """Only executes if human has approved."""
    if not ctx.scratch.get("human_approved", False):
        return AgentStep(
            out_messages=[Message(role="system", content="BLOCKED: Awaiting human review")],
            state_updates={},
            stop=True,
        )
    return AgentStep(
        out_messages=[Message(role="assistant", content="Action executed with human approval")],
        state_updates={"action_executed": True},
        stop=True,
    )
```

---

## 6. Graceful Degradation with Fallbacks

When a service is unavailable, degrade gracefully rather than failing:

```python
def graceful_agent_step(ctx: Context) -> AgentStep:
    """
    Try primary service first; fall back to cached/approximate data if unavailable.
    Reports degraded confidence when using fallback.
    """
    cb = ctx.components.get("circuit_breaker")

    if cb and not cb.can_call():
        # Service is down — use cached data
        cached = ctx.scratch.get("cached_result", {"data": "stale_data", "age_sec": 300})
        return AgentStep(
            out_messages=[Message(role="assistant",
                                 content=f"FALLBACK: {cached['data']} (age={cached['age_sec']}s)")],
            state_updates={
                "result": cached["data"],
                "confidence": 0.40,  # reduced confidence for stale data
                "used_fallback": True,
            },
            stop=False,
        )

    # Primary path
    result_data = "fresh data from service"
    if cb:
        cb.record_success()

    return AgentStep(
        out_messages=[Message(role="assistant", content=result_data)],
        state_updates={
            "result": result_data,
            "confidence": 0.92,
            "used_fallback": False,
        },
        stop=False,
    )
```

---

## Resilience Checklist

Before going to production, verify:

- [ ] All external service calls are wrapped in `GuardrailSandwich`
- [ ] Circuit breakers are configured with appropriate thresholds
- [ ] Retry policies use exponential backoff (not fixed delays)
- [ ] All agent outputs include `confidence` scores
- [ ] High-stakes decisions have confidence gates or `TwoPersonRuleCoordinator`
- [ ] Mandatory human review gates are in place for medical/legal/financial decisions
- [ ] All errors are caught and routed to DLQ or audit log
- [ ] Fallback data sources are tested and confidence scores are accurate

---

## Related Notebooks

- `notebooks/04_circuit_breakers.ipynb` — circuit breaker deep dive
- `notebooks/10_autonomy_transparency.ipynb` — autonomy levels and human oversight
- `notebooks/use_cases/uc_04_clinical_decision_support.ipynb` — mandatory human review gate
- `notebooks/use_cases/uc_05_legal_document_analysis.ipynb` — lawyer review gate
