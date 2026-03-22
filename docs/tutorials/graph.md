# Tutorial — Graph (DAG) Execution

## Overview

`GraphRunner` executes a directed acyclic graph (DAG) of coordinator nodes. Unlike a linear chain, a graph:

1. Runs nodes whose dependencies are satisfied **in parallel**
2. Passes accumulated context state between all nodes
3. Performs topological sort automatically — you just specify `deps`
4. Provides full execution traces for debugging and replay

```
     A ──────┐
             ▼
     B ─────► C ──► E
             ▲
     D ──────┘
```

In this graph, A, B, and D run in parallel. C starts only after B and D complete. E starts after C.

---

## Basic DAG Example

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.patterns import GraphRunner, GraphNodeSpec, AssemblyCoordinator, Stage


# Helper to build a simple single-agent coordinator
def make_node(name: str, description: str):
    def step(ctx: Context) -> AgentStep:
        # Collect results from prior nodes
        prior_keys = [k for k in ctx.scratch if k.endswith("_done")]
        return AgentStep(
            out_messages=[Message(role="assistant",
                                 content=f"{name}: {description} (prior: {len(prior_keys)})")],
            state_updates={f"{name}_done": True, f"{name}_result": description},
            stop=True,
        )
    agent = AgentBuilder(name, "worker").with_step(step).build()
    return AssemblyCoordinator(stages=[Stage(agent=agent, name=name)])


# Build a data pipeline DAG
#
# fetch_a ──┐
#           ▼
# fetch_b ──► merge ──► validate ──► report
#           ▲
# fetch_c ──┘
#

graph = GraphRunner(nodes=[
    GraphNodeSpec(id="fetch_a",  coordinator=make_node("fetch_a",  "Fetched data from source A"), deps=[]),
    GraphNodeSpec(id="fetch_b",  coordinator=make_node("fetch_b",  "Fetched data from source B"), deps=[]),
    GraphNodeSpec(id="fetch_c",  coordinator=make_node("fetch_c",  "Fetched data from source C"), deps=[]),
    GraphNodeSpec(id="merge",    coordinator=make_node("merge",    "Merged A, B, C into unified dataset"), deps=["fetch_a", "fetch_b", "fetch_c"]),
    GraphNodeSpec(id="validate", coordinator=make_node("validate", "Validated merged dataset"), deps=["merge"]),
    GraphNodeSpec(id="report",   coordinator=make_node("report",   "Generated final report"), deps=["validate"]),
])

result = graph.run(goal="Run the ETL pipeline", inputs={})

print("Graph execution messages:")
for msg in result.messages:
    print(f"  {msg.content[:80]}")
```

---

## Understanding GraphNodeSpec

```python
from agentic_codex.patterns import GraphNodeSpec

node = GraphNodeSpec(
    id="my_node",             # unique string ID within this graph
    coordinator=my_coordinator,  # any coordinator: AssemblyCoordinator, MapReduceCoordinator, etc.
    deps=["dep_a", "dep_b"],  # list of node IDs that must complete before this node starts
)
```

### Dependency Rules

- `deps=[]` — the node has no dependencies and can start immediately
- `deps=["A", "B"]` — the node waits for both A and B to complete
- Circular dependencies will raise a `ValueError` at construction time
- The graph will raise an error if a dep references a non-existent node ID

---

## Nested Coordinators as Nodes

Each `GraphNodeSpec.coordinator` can be any coordinator — including another `GraphRunner`, `MapReduceCoordinator`, or `SwarmCoordinator`:

```python
from agentic_codex.patterns import MapReduceCoordinator, SwarmCoordinator

# Node 1: A MapReduce that processes multiple data sources in parallel
ingestion_node = GraphNodeSpec(
    id="ingest",
    coordinator=MapReduceCoordinator(
        mappers=[source_a_agent, source_b_agent, source_c_agent],
        reducer=merger_agent,
    ),
    deps=[],
)

# Node 2: A Swarm that analyses the merged data from multiple angles
analysis_node = GraphNodeSpec(
    id="analyse",
    coordinator=SwarmCoordinator(
        explorers=[analyst_a, analyst_b, analyst_c],
    ),
    deps=["ingest"],
)

# Node 3: A simple assembly for reporting
report_node = GraphNodeSpec(
    id="report",
    coordinator=AssemblyCoordinator(stages=[
        Stage(agent=formatter, name="format"),
        Stage(agent=publisher, name="publish"),
    ]),
    deps=["analyse"],
)

compound_graph = GraphRunner(nodes=[ingestion_node, analysis_node, report_node])
```

---

## Conditional Edges (Dynamic Routing)

Implement conditional routing by using `GuildRouter` as a node, or by checking context state at the start of a node's step function:

```python
def conditional_analysis_step(ctx: Context) -> AgentStep:
    """Run different analysis based on the data type flag set by a prior node."""
    data_type = ctx.scratch.get("data_type", "generic")

    if data_type == "financial":
        analysis = "Financial risk assessment: DCF model applied"
    elif data_type == "technical":
        analysis = "Technical complexity score: cyclomatic complexity = 12"
    else:
        analysis = "Generic analysis: descriptive statistics computed"

    return AgentStep(
        out_messages=[Message(role="assistant", content=analysis)],
        state_updates={"analysis": analysis, "analysis_type": data_type},
        stop=True,
    )

# Classification node writes data_type to ctx.scratch
# Downstream nodes read data_type and adapt their behaviour
```

For hard branching (only one branch should execute), use `GuildRouter`:

```python
from agentic_codex.patterns import GuildRouter

router = GuildRouter(
    guilds={
        "financial": financial_coordinator,
        "technical": technical_coordinator,
        "generic":   generic_coordinator,
    },
    router_fn=lambda ctx: ctx.scratch.get("data_type", "generic"),
)
```

---

## Accessing Node Results

Node results are accumulated in `ctx.scratch` via each node's `state_updates`. The naming convention is `{node_id}_result` or `{node_id}_done`:

```python
def report_step(ctx: Context) -> AgentStep:
    # Access results from all prior nodes
    ingest_result   = ctx.scratch.get("ingest_result", {})
    analysis_result = ctx.scratch.get("analysis_result", {})
    # fetch_a_result, fetch_b_result are also available

    # Build final report from all accumulated state
    report = {
        "data_sources": ingest_result.get("source_count", 0),
        "insights": analysis_result.get("insights", []),
    }
    return AgentStep(
        out_messages=[Message(role="assistant", content=str(report))],
        state_updates={"report": report},
        stop=True,
    )
```

---

## Complete Example: Research DAG

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.patterns import GraphRunner, GraphNodeSpec, AssemblyCoordinator, Stage, MapReduceCoordinator

import json

# ── Agent definitions ─────────────────────────────────────────────────────────

def topic_extractor_fn(ctx: Context) -> AgentStep:
    topic = ctx.scratch.get("research_topic", ctx.goal)
    sub_topics = ["methodology", "findings", "limitations", "implications"]
    return AgentStep(
        out_messages=[Message(role="assistant", content=json.dumps({"sub_topics": sub_topics}))],
        state_updates={"sub_topics": sub_topics},
        stop=True,
    )

def source_search_fn(ctx: Context) -> AgentStep:
    sub_topics = ctx.scratch.get("sub_topics", [])
    sources = [{"id": f"src_{i}", "title": f"Paper on {st}"} for i, st in enumerate(sub_topics)]
    return AgentStep(
        out_messages=[Message(role="assistant", content=json.dumps({"sources": sources}))],
        state_updates={"sources": sources},
        stop=True,
    )

def summarise_fn(ctx: Context) -> AgentStep:
    shard = ctx.scratch.get("current_shard", {})
    title = shard.get("title", "") if isinstance(shard, dict) else str(shard)
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"Summary of: {title}")],
        state_updates={},
        stop=False,
    )

def merge_summaries_fn(ctx: Context) -> AgentStep:
    outputs = ctx.scratch.get("mapper_outputs", [])
    merged  = " | ".join(outputs)
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"Merged: {merged[:80]}")],
        state_updates={"merged_summary": merged},
        stop=True,
    )

def report_fn(ctx: Context) -> AgentStep:
    summary   = ctx.scratch.get("merged_summary", "")
    sources   = ctx.scratch.get("sources", [])
    sub_topics = ctx.scratch.get("sub_topics", [])
    report    = {
        "topic": ctx.scratch.get("research_topic", ""),
        "sub_topics": sub_topics,
        "source_count": len(sources),
        "summary": summary[:100],
    }
    return AgentStep(
        out_messages=[Message(role="assistant", content=json.dumps(report))],
        state_updates={"final_report": report},
        stop=True,
    )

# ── Build agents ──────────────────────────────────────────────────────────────
topic_agent   = AgentBuilder("topic",    "extractor").with_step(topic_extractor_fn).build()
search_agent  = AgentBuilder("search",   "searcher").with_step(source_search_fn).build()
summariser    = AgentBuilder("summariser", "summariser").with_step(summarise_fn).build()
merger        = AgentBuilder("merger",   "reducer").with_step(merge_summaries_fn).build()
reporter      = AgentBuilder("reporter", "reporter").with_step(report_fn).build()

# ── Build coordinators for each node ──────────────────────────────────────────
topic_coord   = AssemblyCoordinator(stages=[Stage(agent=topic_agent,  name="topic")])
search_coord  = AssemblyCoordinator(stages=[Stage(agent=search_agent, name="search")])
summary_coord = MapReduceCoordinator(mappers=[summariser, summariser], reducer=merger)
report_coord  = AssemblyCoordinator(stages=[Stage(agent=reporter, name="report")])

# ── Build and run the DAG ─────────────────────────────────────────────────────
research_graph = GraphRunner(nodes=[
    GraphNodeSpec(id="extract_topics",  coordinator=topic_coord,   deps=[]),
    GraphNodeSpec(id="search_sources",  coordinator=search_coord,  deps=["extract_topics"]),
    GraphNodeSpec(id="summarise",       coordinator=summary_coord, deps=["search_sources"]),
    GraphNodeSpec(id="generate_report", coordinator=report_coord,  deps=["summarise"]),
])

graph_result = research_graph.run(
    goal="Research AI impact on productivity",
    inputs={"research_topic": "AI impact on software development productivity"}
)

print("Research DAG execution complete.")
print(f"Total messages: {len(graph_result.messages)}")
for msg in graph_result.messages:
    print(f"  [{msg.role}] {msg.content[:70]}")
```

---

## Topological Sort

`GraphRunner` automatically topologically sorts nodes before execution. You never need to order `nodes` in the constructor — just specify correct `deps`.

```
Input:  [E(deps=[D]), D(deps=[B,C]), B(deps=[A]), C(deps=[A]), A(deps=[])]
Output: A → [B, C] → D → E  (B and C run in parallel)
```

If you accidentally create a cycle, a `ValueError` is raised at `GraphRunner` construction:

```python
# This will raise ValueError: "Circular dependency detected"
GraphRunner(nodes=[
    GraphNodeSpec(id="A", coordinator=..., deps=["B"]),
    GraphNodeSpec(id="B", coordinator=..., deps=["A"]),   # cycle!
])
```

---

## Related Notebooks

- `notebooks/02_graph_workflow.ipynb` — DAG-based workflows
- `notebooks/11_parallel_bfs_streaming_a2a.ipynb` — parallel BFS graph traversal
- `notebooks/use_cases/uc_02_research_pipeline.ipynb` — research DAG
- `notebooks/use_cases/uc_04_clinical_decision_support.ipynb` — clinical DAG with circuit breakers
