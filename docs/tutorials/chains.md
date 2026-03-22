# Tutorial — Chains & Sequential Pipelines

## Overview

A **chain** (implemented as `AssemblyCoordinator`) sequences multiple agents as `Stage` objects. Each stage receives the accumulated context from all prior stages, enabling agents to build on each other's outputs.

```
Stage1(Agent A) → Stage2(Agent B) → Stage3(Agent C) → Result
    │                   │                   │
  writes to           reads A's           reads A+B's
  ctx.scratch         writes               writes
```

---

## Basic Chain

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.patterns import AssemblyCoordinator, Stage

# Stage 1: Extract keywords from text
def extract_keywords(ctx: Context) -> AgentStep:
    """
    REAL LLM VERSION: send text to GPT-4o with prompt:
    "Extract the 5 most important keywords from this text."
    """
    text = ctx.scratch.get("text", "")
    words = [w for w in text.split() if len(w) > 4][:5]
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"keywords: {words}")],
        state_updates={"keywords": words},
        stop=False,
    )

# Stage 2: Score keywords by relevance
def score_keywords(ctx: Context) -> AgentStep:
    keywords = ctx.scratch.get("keywords", [])
    scored = {kw: round(0.5 + len(kw) * 0.03, 3) for kw in keywords}
    return AgentStep(
        out_messages=[Message(role="assistant", content=str(scored))],
        state_updates={"scored_keywords": scored},
        stop=False,
    )

# Stage 3: Format as structured output
def format_output(ctx: Context) -> AgentStep:
    scored = ctx.scratch.get("scored_keywords", {})
    top = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    output = {"top_keywords": top, "count": len(top)}
    return AgentStep(
        out_messages=[Message(role="assistant", content=str(output))],
        state_updates={"final_output": output},
        stop=True,
    )

extractor = AgentBuilder("extractor", "extractor").with_step(extract_keywords).build()
scorer    = AgentBuilder("scorer",    "scorer").with_step(score_keywords).build()
formatter = AgentBuilder("formatter", "formatter").with_step(format_output).build()

pipeline = AssemblyCoordinator(stages=[
    Stage(agent=extractor, name="extract"),
    Stage(agent=scorer,    name="score"),
    Stage(agent=formatter, name="format"),
])

result = pipeline.run(
    goal="Extract and score keywords from the article",
    inputs={"text": "Machine learning models transform natural language processing with attention mechanisms"}
)

print("All messages from pipeline:")
for msg in result.messages:
    print(f"  [{msg.role}] {msg.content[:80]}")

print("\nFinal state:")
import json
# Access accumulated state via the coordinator result
```

---

## Stage Configuration

Each `Stage` accepts several optional parameters:

```python
from agentic_codex.patterns import AssemblyCoordinator, Stage

pipeline = AssemblyCoordinator(stages=[
    Stage(
        agent=my_agent,
        name="my_stage",                    # used in tracing
        # retry=RetryPolicy(max_attempts=3, backoff=1.0),  # future: retry on failure
    ),
])
```

---

## Conditional Routing in Chains

Implement conditional logic inside a stage's step function — read from context, then set flags that downstream stages check:

```python
def router_step(ctx: Context) -> AgentStep:
    """Route based on classification result."""
    classification = ctx.scratch.get("classification", "")
    route = "technical" if "code" in classification.lower() else "general"
    return AgentStep(
        out_messages=[Message(role="system", content=f"route: {route}")],
        state_updates={"route": route},
        stop=False,
    )

def technical_step(ctx: Context) -> AgentStep:
    """Only runs meaningful work for technical route."""
    if ctx.scratch.get("route") != "technical":
        # Pass-through for non-technical route
        return AgentStep(
            out_messages=[Message(role="system", content="skip: not technical route")],
            state_updates={},
            stop=False,
        )
    return AgentStep(
        out_messages=[Message(role="assistant", content="technical analysis: code review complete")],
        state_updates={"technical_analysis": "done"},
        stop=False,
    )

def general_step(ctx: Context) -> AgentStep:
    """Only runs for general route."""
    if ctx.scratch.get("route") != "general":
        return AgentStep(
            out_messages=[Message(role="system", content="skip: not general route")],
            state_updates={},
            stop=False,
        )
    return AgentStep(
        out_messages=[Message(role="assistant", content="general analysis: content review complete")],
        state_updates={"general_analysis": "done"},
        stop=True,
    )

router    = AgentBuilder("router",    "router").with_step(router_step).build()
technical = AgentBuilder("technical", "analyst").with_step(technical_step).build()
general   = AgentBuilder("general",   "analyst").with_step(general_step).build()
# Note: for true branching use GuildRouter or GraphRunner

conditional_chain = AssemblyCoordinator(stages=[
    Stage(agent=router,    name="route"),
    Stage(agent=technical, name="technical_analysis"),
    Stage(agent=general,   name="general_analysis"),
])
```

---

## Error Handling in Chains

Wrap stages that call unreliable external services in `GuardrailSandwich`:

```python
from agentic_codex.patterns import GuardrailSandwich

def pre_validate(ctx: Context) -> AgentStep:
    """Validate inputs before calling external service."""
    has_required = "api_key" in ctx.scratch and "query" in ctx.scratch
    return AgentStep(
        out_messages=[Message(role="system", content=f"pre_validate: {'OK' if has_required else 'MISSING_INPUTS'}")],
        state_updates={"pre_validated": has_required},
        stop=not has_required,  # short-circuit if inputs missing
    )

def post_validate(ctx: Context) -> AgentStep:
    """Verify the external service returned a valid response."""
    response = ctx.scratch.get("api_response", {})
    valid = "error" not in response
    return AgentStep(
        out_messages=[Message(role="system", content=f"post_validate: {'OK' if valid else 'ERROR'}")],
        state_updates={"post_validated": valid},
        stop=False,
    )

pre_guard  = AgentBuilder("pre",  "validator").with_step(pre_validate).build()
post_guard = AgentBuilder("post", "validator").with_step(post_validate).build()

protected_api_agent = GuardrailSandwich(
    prefilter=pre_guard,
    primary=my_external_api_agent,  # the agent making the risky call
    postfilter=post_guard,
)

# Use in a chain
pipeline = AssemblyCoordinator(stages=[
    Stage(agent=AgentBuilder("input", "input").with_step(input_step).build(), name="input"),
    # GuardrailSandwich is a coordinator, wrap it in a Stage-compatible agent:
    # Stage(agent=wrapped_agent, name="protected_call"),  # see note below
])
```

!!! note "Wrapping coordinators in stages"
    `AssemblyCoordinator` stages require `Agent` objects, not coordinators. To use a coordinator (like `GuardrailSandwich`) as a stage, wrap it with a thin `BaseAgent` that calls `.run()` internally.

---

## Chaining Multiple Coordinators

For complex workflows, chain different coordinator types together:

```python
from agentic_codex.patterns import MapReduceCoordinator, AssemblyCoordinator, Stage

# Stage 1: A MapReduce for parallel data enrichment
enrichment_pipeline = MapReduceCoordinator(
    mappers=[enricher_a, enricher_b, enricher_c],
    reducer=merger,
)

# Then an assembly chain for sequential post-processing
# (Run enrichment first, pass outputs to chain)

def run_compound_pipeline(inputs):
    # Run MapReduce first
    enrich_result = enrichment_pipeline.run(goal="Enrich data", inputs=inputs)
    enriched_data = {}
    for msg in enrich_result.messages:
        # Extract state updates from messages
        pass

    # Run sequential chain on enriched data
    post_pipeline = AssemblyCoordinator(stages=[
        Stage(agent=validator, name="validate"),
        Stage(agent=reporter,  name="report"),
    ])
    # Pass enriched data as inputs
    return post_pipeline.run(goal="Post-process", inputs={**inputs, "enriched": True})
```

---

## Middleware Pattern

Add cross-cutting concerns (logging, metrics, audit) to every stage by wrapping the step function:

```python
import time
import functools
from agentic_codex.core.observability.logger import StructuredLogger

logger = StructuredLogger(name="pipeline")

def with_logging(step_fn, stage_name: str):
    """Decorator that wraps a step function with structured logging."""
    @functools.wraps(step_fn)
    def wrapped(ctx: Context) -> AgentStep:
        t0 = time.time()
        logger.info("stage_started", stage=stage_name, goal=ctx.goal)
        result = step_fn(ctx)
        latency_ms = (time.time() - t0) * 1000
        logger.info("stage_completed",
                    stage=stage_name,
                    latency_ms=round(latency_ms, 2),
                    state_updates=list(result.state_updates.keys()),
                    message_count=len(result.out_messages))
        return result
    return wrapped

# Apply middleware
logged_extractor = AgentBuilder("extractor", "extractor") \
    .with_step(with_logging(extract_keywords, "extract")) \
    .build()
```

---

## Performance Considerations

The `AssemblyCoordinator` runs stages **sequentially** by design. Each stage waits for the previous one to complete. If stages are independent, use `MapReduceCoordinator` or `GraphRunner` instead.

**Guideline**:

| Pattern | When to use |
|---------|-------------|
| `AssemblyCoordinator` | Stages depend on each other's output |
| `MapReduceCoordinator` | Same operation across many data items |
| `GraphRunner` | Complex dependency graph, some parallel paths |
| `SwarmCoordinator` | Same problem, multiple perspectives, pick best |

---

## Related Notebooks

- `notebooks/01_quickstart.ipynb` — basic chain
- `notebooks/02_graph_workflow.ipynb` — chains as graph nodes
- `notebooks/use_cases/uc_02_research_pipeline.ipynb` — iterative chain with quality critique
