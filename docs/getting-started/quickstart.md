# Quickstart — 5 Minutes to Your First Multi-Agent Workflow

This guide gets you from zero to running working multi-agent patterns in 5 minutes. No API keys required — all examples use `FunctionAdapter` (pure Python) so you can focus on the coordination patterns first.

---

## Step 0 — Install

```bash
pip install agentic-codex
```

---

## Step 1 — Your First Agent

An `Agent` is the atomic unit. It has a `name`, a `role`, and a `step` function that receives a `Context` and returns an `AgentStep`.

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message

def greet_step(ctx: Context) -> AgentStep:
    name = ctx.scratch.get("name", "World")
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"Hello, {name}!")],
        state_updates={"greeted": True},
        stop=True,
    )

agent = AgentBuilder("greeter", "assistant").with_step(greet_step).build()

ctx = Context(goal="Say hello")
ctx.scratch["name"] = "Multigen"

result = agent.run(ctx)
print(result.out_messages[-1].content)   # Hello, Multigen!
print(result.state_updates)              # {"greeted": True}
```

**What happened?**

1. `AgentBuilder` creates an `Agent` dataclass with your step function wired in.
2. `Context` carries the shared state — `goal` (a string description), `scratch` (a dict for arbitrary data), `memory`, `tools`, etc.
3. `AgentStep` is the return value: a list of messages, any state mutations, and a `stop` flag.

---

## Step 2 — Your First Chain (AssemblyCoordinator)

An `AssemblyCoordinator` sequences agents as `Stage` objects. Each stage receives the accumulated context from prior stages.

```python
from agentic_codex.patterns import AssemblyCoordinator, Stage

def parse_step(ctx: Context) -> AgentStep:
    raw = ctx.scratch.get("raw_text", "")
    parsed = {"words": raw.split(), "char_count": len(raw)}
    return AgentStep(
        out_messages=[Message(role="assistant", content=str(parsed))],
        state_updates={"parsed": parsed},
        stop=False,
    )

def summarise_step(ctx: Context) -> AgentStep:
    parsed = ctx.scratch.get("parsed", {})
    summary = f"{parsed.get('char_count', 0)} characters, {len(parsed.get('words', []))} words"
    return AgentStep(
        out_messages=[Message(role="assistant", content=summary)],
        state_updates={"summary": summary},
        stop=True,
    )

parser    = AgentBuilder("parser",    "parser").with_step(parse_step).build()
summariser = AgentBuilder("summariser", "summariser").with_step(summarise_step).build()

chain = AssemblyCoordinator(stages=[
    Stage(agent=parser,    name="parse"),
    Stage(agent=summariser, name="summarise"),
])

result = chain.run(
    goal="Analyse text",
    inputs={"raw_text": "Multigen is a composable multi-agent framework for Python."}
)

for msg in result.messages:
    print(msg.content)
# {'words': ['Multigen', 'is', ...], 'char_count': 55}
# 55 characters, 8 words
```

**Key concepts:**

- Stages run **sequentially** — each stage sees the output of all prior stages via `ctx.scratch`.
- Set `stop=True` in the last stage's `AgentStep` to signal completion.
- The `AssemblyCoordinator` collects all messages in `result.messages`.

---

## Step 3 — Your First Parallel (MapReduceCoordinator)

Run multiple agents concurrently and combine their outputs with a reducer.

```python
from agentic_codex.patterns import MapReduceCoordinator

def sentiment_step(ctx: Context) -> AgentStep:
    """Mock sentiment analyser — classify shard as positive/negative/neutral."""
    text = ctx.scratch.get("current_shard", "")
    # In production: call GPT-4o or a sentiment classifier
    score = 0.8 if any(w in text.lower() for w in ["great", "good", "excellent"]) else 0.3
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"sentiment_score:{score}")],
        state_updates={"last_sentiment": score},
        stop=False,
    )

def aggregate_step(ctx: Context) -> AgentStep:
    """Combine all mapper outputs into a mean sentiment."""
    outputs = ctx.scratch.get("mapper_outputs", [])
    scores = []
    for out in outputs:
        if "sentiment_score:" in out:
            scores.append(float(out.split(":")[1]))
    mean_score = sum(scores) / len(scores) if scores else 0.5
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"mean_sentiment={mean_score:.3f}")],
        state_updates={"mean_sentiment": mean_score},
        stop=True,
    )

analyser   = AgentBuilder("analyser",   "analyst").with_step(sentiment_step).build()
aggregator = AgentBuilder("aggregator", "reducer").with_step(aggregate_step).build()

pipeline = MapReduceCoordinator(mappers=[analyser, analyser, analyser], reducer=aggregator)

reviews = [
    "This product is excellent and works great!",
    "Average product, nothing special.",
    "Great value for money, highly recommend.",
]

result = pipeline.run(
    goal="Analyse review sentiment",
    inputs={"shards": reviews}
)

print(result.messages[-1].content)   # mean_sentiment=0.633
```

**Key concepts:**

- `shards` in `inputs` are distributed across mappers (round-robin by index).
- Each mapper receives its shard in `ctx.scratch["current_shard"]`.
- After all mappers complete, `ctx.scratch["mapper_outputs"]` contains all their message content strings.
- The reducer receives the same context with all mapper outputs available.

---

## Step 4 — Your First Graph (GraphRunner)

A `GraphRunner` executes a directed acyclic graph (DAG) of `GraphNodeSpec` nodes.

```python
from agentic_codex.patterns import GraphRunner, GraphNodeSpec
from agentic_codex.patterns import AssemblyCoordinator, Stage

def extract_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"extracted: {ctx.goal[:20]}")],
        state_updates={"extracted": True},
        stop=False,
    )

def enrich_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content="enriched with metadata")],
        state_updates={"enriched": True},
        stop=False,
    )

def report_step(ctx: Context) -> AgentStep:
    extracted = ctx.scratch.get("extracted", False)
    enriched  = ctx.scratch.get("enriched", False)
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"Report: extracted={extracted} enriched={enriched}")],
        state_updates={"report_done": True},
        stop=True,
    )

extractor = AgentBuilder("extractor", "extractor").with_step(extract_step).build()
enricher  = AgentBuilder("enricher",  "enricher").with_step(enrich_step).build()
reporter  = AgentBuilder("reporter",  "reporter").with_step(report_step).build()

# Build a simple chain-style assembly for graph nodes
extract_stage = AssemblyCoordinator(stages=[Stage(agent=extractor, name="extract")])
enrich_stage  = AssemblyCoordinator(stages=[Stage(agent=enricher,  name="enrich")])
report_stage  = AssemblyCoordinator(stages=[Stage(agent=reporter,  name="report")])

graph = GraphRunner(nodes=[
    GraphNodeSpec(id="extract", coordinator=extract_stage, deps=[]),
    GraphNodeSpec(id="enrich",  coordinator=enrich_stage,  deps=["extract"]),
    GraphNodeSpec(id="report",  coordinator=report_stage,  deps=["extract", "enrich"]),
])

result = graph.run(goal="Process document", inputs={"document": "Sample text for analysis"})
for msg in result.messages:
    print(msg.content)
```

**Key concepts:**

- `deps` defines which nodes must complete before this node starts.
- Nodes with no unmet dependencies run in parallel.
- The graph performs topological sort automatically.

---

## Step 5 — Your First State Machine

A state machine iterates until a termination condition is met — ideal for refinement loops and confidence-gated workflows.

```python
from enum import Enum

class ReviewState(Enum):
    DRAFT   = "draft"
    REVIEW  = "review"
    DONE    = "done"

def draft_step(ctx: Context) -> AgentStep:
    iteration = ctx.scratch.get("iteration", 0) + 1
    # Simulate quality improving with each pass
    quality = min(0.95, 0.50 + iteration * 0.15)
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"draft_v{iteration} quality={quality:.2f}")],
        state_updates={"quality": quality, "iteration": iteration},
        stop=False,
    )

drafter = AgentBuilder("drafter", "writer").with_step(draft_step).build()

# Run state machine loop
state           = ReviewState.DRAFT
quality_threshold = 0.80
max_iterations    = 5
ctx = Context(goal="Write a report")
history = []

while state != ReviewState.DONE:
    result = drafter.run(ctx)
    quality   = ctx.scratch.get("quality", 0)
    iteration = ctx.scratch.get("iteration", 0)
    history.append({"state": state.value, "quality": quality})
    print(f"  [{state.value.upper()}] iter={iteration}  quality={quality:.2f}")

    if quality >= quality_threshold or iteration >= max_iterations:
        state = ReviewState.DONE
    else:
        state = ReviewState.REVIEW  # trigger another draft

print(f"\nFinal quality: {ctx.scratch.get('quality', 0):.2f} (threshold: {quality_threshold})")
```

Expected output:
```
  [DRAFT] iter=1  quality=0.65
  [REVIEW] iter=2  quality=0.80
Final quality: 0.80 (threshold: 0.80)
```

---

## What's Next?

You now know the five core patterns. Explore the tutorials for deep dives:

| Tutorial | What you learn |
|----------|---------------|
| [Agents](../tutorials/agents.md) | Every agent type: Function, LLM, Memory, Tool |
| [Chains](../tutorials/chains.md) | Assembly, pipeline middleware, error handling |
| [Parallel](../tutorials/parallel.md) | MapReduce, Swarm, Race, FanOut, Batch |
| [Graph](../tutorials/graph.md) | DAG construction, conditional edges, subgraphs |
| [State Machines](../tutorials/state_machine.md) | MCMC sampling, ensemble, temperature |
| [Resilience](../tutorials/resilience.md) | Circuit breakers, retry, HITL gates |

Or jump directly to a use case:

- [Credit Risk Assessment](../use_cases/credit_risk.md) — parallel analysis, MCMC ensemble
- [Research Pipeline](../use_cases/research.md) — iterative refinement, quality critique
- [AIOps](../use_cases/devops.md) — race pattern, confidence gating, auto-remediation
