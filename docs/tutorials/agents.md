# Tutorial — Agent Primitives

This tutorial covers every way to build agents in Multigen: from simple function wrappers to fully-equipped LLM agents with memory, tools, and skill registries.

---

## Prerequisites

```bash
pip install agentic-codex
```

All examples in this tutorial run without any API keys.

---

## 1. The Anatomy of an Agent

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message

# A step function is the core of any agent
def my_step(ctx: Context) -> AgentStep:
    # 1. Read from context
    input_data = ctx.scratch.get("input", "")

    # 2. Process (call LLM, run tool, compute something)
    output = f"Processed: {input_data.upper()}"

    # 3. Write to context and return messages
    return AgentStep(
        out_messages=[Message(role="assistant", content=output)],
        state_updates={"processed_output": output},
        stop=True,  # True means this agent is done for this run
    )

# Build the agent
agent = (
    AgentBuilder("my-processor", "processor")
    .with_step(my_step)
    .build()
)

# Run it
ctx = Context(goal="Process the greeting")
ctx.scratch["input"] = "hello world"
result = agent.run(ctx)

print(result.out_messages[-1].content)     # Processed: HELLO WORLD
print(ctx.scratch["processed_output"])      # Processed: HELLO WORLD
```

---

## 2. FunctionAdapter Agents (Mock LLM)

`FunctionAdapter` wraps a plain Python function as an `LLMAdapter`. It lets you build and test full pipeline logic without any API keys — the function receives the messages and returns a mock response.

```python
from agentic_codex import AgentBuilder, Context, FunctionAdapter
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.core.interfaces import SamplingParams

def mock_llm(messages, params: SamplingParams = None):
    """
    A mock LLM that returns a canned response based on the last user message.

    REPLACE with EnvOpenAIAdapter for production.
    """
    last_user_msg = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if "summarise" in last_user_msg.lower():
        return Message(role="assistant", content="Summary: This is a structured summary of the provided text.")
    if "classify" in last_user_msg.lower():
        return Message(role="assistant", content='{"category": "technical", "confidence": 0.87}')
    return Message(role="assistant", content=f"Response to: {last_user_msg[:50]}")


def llm_step(ctx: Context) -> AgentStep:
    """Step that calls the LLM adapter injected via capability."""
    llm    = ctx.llm   # injected by LLMCapability
    goal   = ctx.goal

    prompt = f"Please {goal}. Context: {ctx.scratch.get('context', '')}"
    response = llm.complete([Message(role="user", content=prompt)])

    return AgentStep(
        out_messages=[response],
        state_updates={"llm_response": response.content},
        stop=True,
    )


function_agent = (
    AgentBuilder("function-agent", "assistant")
    .with_llm(FunctionAdapter(fn=mock_llm))
    .with_step(llm_step)
    .build()
)

ctx = Context(goal="summarise the quarterly report")
ctx.scratch["context"] = "Revenue up 15%, costs down 8%, EBITDA improved."
result = function_agent.run(ctx)
print(result.out_messages[-1].content)
# Summary: This is a structured summary of the provided text.
```

### Switching to a Real LLM

When you're ready to test with a real LLM, change one line:

```python
from agentic_codex import EnvOpenAIAdapter

# Before (mock):
.with_llm(FunctionAdapter(fn=mock_llm))

# After (real LLM):
.with_llm(EnvOpenAIAdapter(model="gpt-4o-mini"))
```

The step function code is identical. This is the key design principle: **coordination logic is LLM-agnostic**.

---

## 3. Agents with Memory

Add `EpisodicMemory` to give your agent a running log of observations across multiple step calls. This is essential for iterative refinement workflows.

```python
from agentic_codex import AgentBuilder, Context, EpisodicMemory, MemoryCapability
from agentic_codex.core.schemas import AgentStep, Message

def memory_step(ctx: Context) -> AgentStep:
    """Agent that accumulates observations in episodic memory."""
    # Access the memory store
    memory_store = ctx.components.get("memory")

    # Read past episodes
    past_episodes = memory_store.episodes() if memory_store else []
    iteration = len(past_episodes) + 1

    # Compute result
    result = f"Iteration {iteration}: quality_score={0.5 + iteration * 0.1:.2f}"

    # Record this episode
    if memory_store:
        memory_store.add({
            "iteration": iteration,
            "result": result,
            "timestamp": "2026-03-22",
        })

    return AgentStep(
        out_messages=[Message(role="assistant", content=result)],
        state_updates={"iteration": iteration, "past_episodes": len(past_episodes)},
        stop=iteration >= 3,  # stop after 3 iterations
    )


memory_agent = (
    AgentBuilder("memory-agent", "iterative-analyser")
    .with_memory(EpisodicMemory())
    .with_step(memory_step)
    .build()
)

ctx = Context(goal="Iteratively improve analysis quality")
for i in range(4):
    result = memory_agent.run(ctx)
    print(result.out_messages[-1].content)
    if result.out_messages and hasattr(result, 'stop') and True:
        pass  # loop is controlled by stop flag in AgentStep
```

### Memory Types

| Type | Backend | Use Case |
|------|---------|----------|
| `EpisodicMemory` | In-memory list | Ordered event log within a session |
| `SemanticMemory` | Vector DB | Similarity-based retrieval |
| `GraphMemory` | In-memory graph | Entity relationships |
| `CachedMemory` | Wraps any Memory | LRU cache for expensive lookups |
| `PersistentMemory` | File system | Survive process restarts |
| `SQLiteMemory` | SQLite | Durable, queryable memory |

---

## 4. Agents with Tools

Tools are callable adapters that agents can invoke during their step. They're ideal for database lookups, API calls, code execution, and calculations.

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.tools import MathToolAdapter, ToolPermissions
from agentic_codex.core.schemas import AgentStep, Message

# Create a calculator tool
calc_tool = MathToolAdapter(
    allowed_functions=["sin", "cos", "sqrt", "log"],
    permissions=ToolPermissions(max_calls_per_minute=100),
)

def calculator_step(ctx: Context) -> AgentStep:
    """Agent that uses the math tool to compute results."""
    tool = ctx.tools.get("calculator")
    expression = ctx.scratch.get("expression", "sqrt(144)")

    if tool:
        # Execute the tool
        result = tool.execute({"expression": expression})
        answer = result.get("result", "error")
    else:
        answer = "tool not available"

    return AgentStep(
        out_messages=[Message(role="assistant", content=f"{expression} = {answer}")],
        state_updates={"math_result": answer},
        stop=True,
    )


calc_agent = (
    AgentBuilder("calculator", "math-agent")
    .with_step(calculator_step)
    .build()
)

# Manually attach tool to context for demo (normally done via ToolCapability)
ctx = Context(goal="Calculate square root")
ctx.scratch["expression"] = "16 + 9"
# ctx.tools["calculator"] = calc_tool  # would be used in production

result = calc_agent.run(ctx)
print(result.out_messages[-1].content)
```

### Available Tool Adapters

| Adapter | Purpose |
|---------|---------|
| `HTTPToolAdapter` | REST API calls with authentication |
| `RAGToolAdapter` | Retrieval-augmented generation queries |
| `DBToolAdapter` | SQL database queries |
| `CodeExecutionToolAdapter` | Safe Python code execution |
| `MathToolAdapter` | Mathematical expression evaluation |
| `SafeFileReaderToolAdapter` | Sandboxed file reading |

---

## 5. Multi-Step Agents

Use `.with_steps()` to compose multiple step functions inside a single agent. Steps run sequentially (or in parallel with `max_parallel`).

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.orchestration import StepSpec
from agentic_codex.core.schemas import AgentStep, Message

def fetch_step(ctx: Context) -> AgentStep:
    ctx.scratch["data"] = "fetched: raw sensor data"
    return AgentStep(
        out_messages=[Message(role="tool", content="fetch complete")],
        state_updates={"fetched": True},
        stop=False,
    )

def clean_step(ctx: Context) -> AgentStep:
    raw = ctx.scratch.get("data", "")
    cleaned = raw.replace("raw ", "clean ")
    return AgentStep(
        out_messages=[Message(role="assistant", content=cleaned)],
        state_updates={"data": cleaned, "cleaned": True},
        stop=False,
    )

def validate_step(ctx: Context) -> AgentStep:
    cleaned = ctx.scratch.get("data", "")
    valid = len(cleaned) > 0
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"validation: {'OK' if valid else 'FAIL'}")],
        state_updates={"valid": valid},
        stop=True,
    )

# Build an agent with 3 internal steps
etl_agent = (
    AgentBuilder("etl-agent", "data-engineer")
    .with_steps([
        StepSpec(name="fetch",    fn=fetch_step),
        StepSpec(name="clean",    fn=clean_step),
        StepSpec(name="validate", fn=validate_step),
    ])
    .build()
)

ctx = Context(goal="ETL pipeline run")
result = etl_agent.run(ctx)
for msg in result.out_messages:
    print(f"  [{msg.role}] {msg.content}")
```

---

## 6. Pattern Agents

Multigen ships with several pre-built "pattern agents" that wrap coordination patterns as single `BaseAgent` subclasses. These are registered in the orchestrator and can be used directly in DSL workflow definitions.

```python
from agents.pattern_agents.agents import (
    MinistryOfExpertsAgent,
    SwarmAgent,
    DebateAgent,
    GuardrailAgent,
    MapReduceAgent,
)

# Use in an orchestrator workflow DSL step:
workflow_step = {
    "name": "analyse",
    "agent": "MinistryOfExpertsAgent",
    "params": {
        "goal": "Analyse this market research data",
        "task_count": 3,
    }
}
```

### Available Pattern Agents

| Class | Pattern | params |
|-------|---------|--------|
| `MinistryOfExpertsAgent` | Hierarchical planning | `goal`, `task_count` |
| `SwarmAgent` | Collective intelligence | `goal`, `swarm_size` |
| `DebateAgent` | Adversarial reasoning | `goal` |
| `GuardrailAgent` | Safety sandwich | `goal` |

---

## 7. Agent Observability

Every agent run is automatically traced when you use the observability stack.

```python
from agentic_codex.core.observability.logger import StructuredLogger
from agentic_codex.core.observability.metrics import record_counter, record_latency

logger = StructuredLogger(name="my-agent")

def observable_step(ctx: Context) -> AgentStep:
    logger.info("step_started", goal=ctx.goal, scratch_keys=list(ctx.scratch.keys()))

    import time
    t0 = time.time()

    # ... do work ...
    result = "processed"

    latency_ms = (time.time() - t0) * 1000
    record_counter("agent.step.completed", tags={"agent": "my-agent"})
    record_latency("agent.step.latency_ms", latency_ms, tags={"agent": "my-agent"})

    logger.info("step_completed", result_length=len(result), latency_ms=latency_ms)

    return AgentStep(
        out_messages=[Message(role="assistant", content=result)],
        state_updates={},
        stop=True,
    )
```

---

## 8. Best Practices

### Do

- Keep step functions **pure** — read from `ctx.scratch`, write via `state_updates`
- Use `FunctionAdapter` for all unit tests; swap to real LLM for integration tests
- Return meaningful `stop=True` conditions — the coordinator depends on them
- Add `confidence` to `state_updates` for every agent that produces uncertain output

### Don't

- Don't mutate `ctx.scratch` directly inside step functions — use `state_updates` instead
- Don't put large blobs (images, PDFs) in `ctx.scratch` — use `ctx.stores` for large data
- Don't create agents with side effects (DB writes) without wrapping in `GuardrailSandwich`
- Don't use `stop=True` in mappers if the reducer needs to run after all mappers

---

## Related Notebooks

- `notebooks/01_quickstart.ipynb` — first agent run
- `notebooks/05_reflection_loops.ipynb` — iterative multi-step agents
- `notebooks/06_fan_out_consensus.ipynb` — parallel agent patterns
- `notebooks/use_cases/uc_01_credit_risk_assessment.ipynb` — full production example
