# API Reference — Chain / Pipeline

## `AssemblyCoordinator`

Executes agents sequentially as `Stage` objects. Each stage receives the accumulated context from all prior stages.

```python
from agentic_codex.patterns import AssemblyCoordinator, Stage
```

### Constructor

```python
coordinator = AssemblyCoordinator(
    stages: List[Stage],
    *,
    guards: Sequence = (),
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stages` | `List[Stage]` | required | Ordered list of pipeline stages |
| `guards` | `Sequence` | `()` | Optional guard objects applied to all stages |

### `run(goal: str, inputs: dict) → CoordinatorResult`

Execute all stages sequentially.

```python
result = coordinator.run(
    goal="Process the quarterly report",
    inputs={"document": "...", "metadata": {}}
)

# Access all messages from all stages
for msg in result.messages:
    print(msg.content)
```

**Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `goal` | `str` | Task description, set as `ctx.goal` |
| `inputs` | `dict` | Initial `ctx.scratch` values |

**Returns**: `CoordinatorResult` with `messages: List[Message]`.

---

## `Stage`

A single stage in an `AssemblyCoordinator`.

```python
from agentic_codex.patterns import Stage
```

### Constructor

```python
stage = Stage(
    agent: Agent,
    name: str,
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent` | `Agent` | The agent to run in this stage |
| `name` | `str` | Stage name used in tracing and logging |

### Example

```python
from agentic_codex.patterns import AssemblyCoordinator, Stage

pipeline = AssemblyCoordinator(stages=[
    Stage(agent=parser_agent,    name="parse"),
    Stage(agent=analyser_agent,  name="analyse"),
    Stage(agent=reporter_agent,  name="report"),
])

result = pipeline.run(goal="Analyse document", inputs={"text": "..."})
```

---

## `StreamingActorSystem`

Drive long-lived actors from an input event stream. Each event in the stream is processed by the actor, with an optional controller agent that can modify state between events.

```python
from agentic_codex.patterns import StreamingActorSystem
```

### Constructor

```python
coordinator = StreamingActorSystem(
    actor: Agent,
    controller: Optional[Agent] = None,
    *,
    guards: Sequence = (),
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `actor` | `Agent` | required | Agent called for each event |
| `controller` | `Agent \| None` | `None` | Optional controller called after each event |
| `guards` | `Sequence` | `()` | Guard objects |

### `run(goal: str, inputs: dict) → CoordinatorResult`

Process all events in `inputs["events"]`.

```python
events = [
    {"type": "sensor_reading", "value": 42.1},
    {"type": "sensor_reading", "value": 38.7},
    {"type": "alert",          "threshold_exceeded": True},
]

result = coordinator.run(
    goal="Process sensor stream",
    inputs={"events": events}
)
```

Inside the actor's step function, access the current event via `ctx.scratch["current_event"]`.

---

## `CoordinatorBase`

The abstract base class for all coordinators. Implement custom coordinators by subclassing this.

```python
from agentic_codex.core.orchestration.coordinator.base import CoordinatorBase
from agentic_codex.core.agent import Context
from agentic_codex.core.schemas import Message
from typing import List

class MyCustomCoordinator(CoordinatorBase):
    def __init__(self, agents, *, guards=()):
        super().__init__(guards=guards)
        self.agents = agents

    def _run(self, context: Context, events: list) -> List[Message]:
        """Implement your coordination logic here."""
        history = []
        for agent in self.agents:
            result = agent.run(context)
            history.extend(result.out_messages)
            # update context scratch with state updates
            context.scratch.update(result.state_updates)
        events.extend(self.tracer.events)
        return history
```

### Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `run` | `(goal: str, inputs: dict) → CoordinatorResult` | Public entry point |
| `_run` | `(context: Context, events: list) → List[Message]` | Override in subclasses |

---

## `run_steps` / `StepSpec`

Run a list of steps inside a single agent. Used by `AgentBuilder.with_steps()`.

```python
from agentic_codex.core.orchestration import run_steps, StepSpec
```

### `StepSpec`

```python
spec = StepSpec(
    name: str,            # step identifier for tracing
    fn: StepFn,           # the step function
    parallel: bool = False,  # run in parallel with adjacent parallel steps
)
```

### `run_steps(steps, ctx, max_parallel=4) → List[AgentStep]`

Execute steps sequentially (or in parallel batches if `parallel=True`).

```python
results = run_steps(
    [StepSpec("fetch", fetch_fn), StepSpec("clean", clean_fn)],
    ctx,
    max_parallel=4,
)
```
