# API Reference — Agents

## `Agent`

The core agent dataclass. Normally created via `AgentBuilder`.

```python
from agentic_codex.core.agent import Agent
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `step` | `StepFn` | required | The step function `(Context) → AgentStep` |
| `name` | `str` | required | Unique agent identifier |
| `role` | `str` | required | Semantic role label (e.g., `"analyst"`, `"summariser"`) |
| `cfg` | `Mapping[str, Any] \| None` | `None` | Optional static configuration dict |
| `llm` | `LLMAdapter \| None` | `None` | LLM adapter (injected by `LLMCapability`) |
| `registry` | `Any \| None` | `None` | Optional agent registry reference |
| `capabilities` | `CapabilityRegistry` | `CapabilityRegistry()` | Attached capabilities |
| `kernel` | `AgentKernel \| None` | `None` | Optional custom kernel |

### Methods

#### `agent.run(ctx: Context) → AgentStep`

Execute the agent's step function against the given context.

```python
ctx = Context(goal="analyse data")
ctx.scratch["data"] = [1, 2, 3]
result = agent.run(ctx)
print(result.out_messages[-1].content)
```

**Parameters**:
- `ctx` (`Context`): The shared context object. Modified in-place via `state_updates`.

**Returns**: `AgentStep` — the step result containing messages and state updates.

**Raises**: Any exception raised by the step function propagates unmodified (handle in your coordinator or with `GuardrailSandwich`).

#### `agent.add_capability(capability: Capability, *, replace: bool = False) → None`

Attach a capability to the agent after construction.

```python
from agentic_codex import EpisodicMemory, MemoryCapability
agent.add_capability(MemoryCapability(store=EpisodicMemory()))
```

#### `agent.add_capabilities(capabilities: Iterable[Capability], ...) → None`

Attach multiple capabilities at once.

---

## `AgentBuilder`

Fluent builder for composing agents from reusable capabilities.

```python
from agentic_codex import AgentBuilder
```

### Constructor

```python
builder = AgentBuilder(name: str, role: str)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Agent name |
| `role` | `str` | Agent role |

### Builder Methods

All builder methods return `self` for chaining.

#### `.with_step(step: StepFn) → AgentBuilder`

Set the step function.

```python
def my_step(ctx: Context) -> AgentStep:
    return AgentStep(out_messages=[Message(role="assistant", content="hello")], state_updates={}, stop=True)

builder.with_step(my_step)
```

#### `.with_steps(steps: Iterable[StepSpec], *, max_parallel: int = 4) → AgentBuilder`

Compose multiple step functions into a single composite step.

```python
from agentic_codex.core.orchestration import StepSpec

builder.with_steps([
    StepSpec(name="fetch",    fn=fetch_step),
    StepSpec(name="validate", fn=validate_step),
    StepSpec(name="compute",  fn=compute_step),
])
```

#### `.with_llm(adapter: LLMAdapter, *, name: str = "llm", replace: bool = False) → AgentBuilder`

Attach an LLM adapter. The adapter is accessible via `ctx.llm` inside the step function.

```python
from agentic_codex import EnvOpenAIAdapter, FunctionAdapter

# Real LLM
builder.with_llm(EnvOpenAIAdapter(model="gpt-4o"))

# Mock LLM (no API key needed)
builder.with_llm(FunctionAdapter(fn=my_mock_fn))
```

#### `.with_memory(store: Memory, *, name: str = "memory", ...) → AgentBuilder`

Attach a memory store. Access via `ctx.components["memory"]`.

```python
from agentic_codex import EpisodicMemory

builder.with_memory(EpisodicMemory())
```

#### `.with_context(data: Mapping[str, Any], *, key: str = "context", ...) → AgentBuilder`

Pre-load static context data into the agent. Available in `ctx.scratch[key]`.

```python
builder.with_context({"knowledge_base": KB_DATA, "config": {"threshold": 0.8}})
```

#### `.with_capability(capability: Capability, *, replace: bool = False) → AgentBuilder`

Attach any custom capability.

```python
from agentic_codex.core.capabilities import MessageBusCapability
builder.with_capability(MessageBusCapability(bus=my_bus))
```

#### `.with_config(cfg: Mapping[str, Any]) → AgentBuilder`

Set static configuration available via `agent.cfg`.

#### `.build() → Agent`

Finalise and return the `Agent` instance.

```python
agent = builder.build()
```

---

## `Context`

Shared mutable state passed to every agent step.

```python
from agentic_codex import Context
```

### Constructor

```python
ctx = Context(goal: str)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `goal` | `str` | required | Human-readable description of the task |

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `goal` | `str` | Task description |
| `scratch` | `dict` | Arbitrary key-value pipeline state (primary data channel) |
| `memory` | `dict` | Long-term accumulated observations |
| `inbox` | `List[Message]` | Messages received from other agents |
| `tools` | `dict` | Tool adapters registered for this run |
| `stores` | `dict` | Data stores (vector DBs, SQLite, etc.) |
| `policies` | `dict` | Rate limits and safety policies |
| `components` | `dict` | Custom capabilities (message bus, circuit breaker, etc.) |
| `llm` | `LLMAdapter \| None` | LLM adapter injected by `LLMCapability` |

### Methods

#### `ctx.add_component(name: str, value: Any) → None`
#### `ctx.get_component(name: str) → Any`
#### `ctx.add_tool(name: str, tool: Any) → None`
#### `ctx.get_tool(name: str) → Any`
#### `ctx.push_message(message: Message) → None`

---

## `AgentStep`

Return value from every step function.

```python
from agentic_codex.core.schemas import AgentStep
```

### Constructor

```python
step = AgentStep(
    out_messages: List[Message],
    state_updates: Dict[str, Any],
    stop: bool = False,
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `out_messages` | `List[Message]` | Messages emitted in this step |
| `state_updates` | `Dict[str, Any]` | Key-value pairs merged into `ctx.scratch` |
| `stop` | `bool` | If `True`, signal to coordinator that agent is done |

---

## `Message`

A single message in the agent's message history.

```python
from agentic_codex.core.schemas import Message
```

| Field | Type | Description |
|-------|------|-------------|
| `role` | `str` | `"user"`, `"assistant"`, `"system"`, `"tool"` |
| `content` | `str` | Message text |
| `name` | `str \| None` | Optional message name |
| `tool_call_id` | `str \| None` | For tool response messages |

---

## LLM Adapters

### `FunctionAdapter`

```python
from agentic_codex import FunctionAdapter

adapter = FunctionAdapter(
    fn: Callable[[List[Message], Optional[SamplingParams]], Message],
    model: str = "function",
)
```

Wraps a plain Python function as an LLM adapter. The function receives the message list and returns a `Message`.

### `EnvOpenAIAdapter`

```python
from agentic_codex import EnvOpenAIAdapter

adapter = EnvOpenAIAdapter(
    model: str,                          # e.g., "gpt-4o", "gpt-4o-mini"
    api_key: Optional[str] = None,       # defaults to $OPENAI_API_KEY
    max_retries: int = 3,
    backoff: float = 0.5,
)
```

Proxies calls to OpenAI's API. Requires `pip install "agentic-codex[openai]"`.

---

## Memory Systems

### `EpisodicMemory`

```python
from agentic_codex import EpisodicMemory

memory = EpisodicMemory()
memory.add({"event": "step_completed", "quality": 0.87})
episodes = memory.episodes()   # list of all added dicts
```

### `SemanticMemory`

```python
from agentic_codex import SemanticMemory

memory = SemanticMemory(embedding_adapter=my_embedder)
memory.add({"text": "Machine learning improves predictions", "metadata": {}})
results = memory.search("neural networks", top_k=5)
```

### `SQLiteMemory`

```python
from agentic_codex import SQLiteMemory

memory = SQLiteMemory(db_path="./pipeline_memory.db")
memory.add({"key": "analysis_v1", "value": {"score": 0.87}})
value = memory.get("analysis_v1")
```

### `CachedMemory`

```python
from agentic_codex import CachedMemory, EpisodicMemory

cached = CachedMemory(store=EpisodicMemory(), max_size=1000)
```
