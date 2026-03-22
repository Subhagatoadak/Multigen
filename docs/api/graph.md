# API Reference — Graph

## `GraphRunner`

Executes a directed acyclic graph (DAG) of coordinator nodes.

```python
from agentic_codex.patterns import GraphRunner, GraphNodeSpec
# also importable from:
from agentic_codex.core.orchestration import GraphRunner, GraphNodeSpec
```

### Constructor

```python
graph = GraphRunner(
    nodes: List[GraphNodeSpec],
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `nodes` | `List[GraphNodeSpec]` | All nodes in the graph (order doesn't matter — topological sort applied automatically) |

**Raises**:
- `ValueError: "Circular dependency detected"` — if `deps` form a cycle
- `ValueError: "Unknown dependency node"` — if a `dep` references a non-existent `id`

### `run(goal: str, inputs: dict) → CoordinatorResult`

Execute the graph.

**Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `goal` | `str` | Task description. Set as `ctx.goal` for all nodes. |
| `inputs` | `dict` | Initial `ctx.scratch` values. All nodes share the same context. |

**Returns**: `CoordinatorResult`

| Attribute | Type | Description |
|-----------|------|-------------|
| `messages` | `List[Message]` | All messages from all nodes, in execution order |

**Execution model**:
1. Build the topological order from `deps` constraints
2. Run all nodes with no unmet dependencies concurrently
3. As nodes complete, update `ctx.scratch` with their `state_updates`
4. Re-evaluate which nodes can now start
5. Continue until all nodes have run

---

## `GraphNodeSpec`

Specification for one node in a `GraphRunner` graph.

```python
from agentic_codex.patterns import GraphNodeSpec
```

### Constructor

```python
node = GraphNodeSpec(
    id: str,
    coordinator: CoordinatorBase,
    deps: List[str] = [],
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `id` | `str` | required | Unique node identifier within this `GraphRunner` instance |
| `coordinator` | `CoordinatorBase` | required | The coordinator to run for this node |
| `deps` | `List[str]` | `[]` | IDs of nodes that must complete before this node can start |

### Supported Coordinator Types for `coordinator`

Any `CoordinatorBase` subclass works as a node coordinator:

| Type | Class |
|------|-------|
| Sequential chain | `AssemblyCoordinator` |
| Parallel MapReduce | `MapReduceCoordinator` |
| Swarm | `SwarmCoordinator` |
| Nested graph | `GraphRunner` |
| Pattern coordinator | `GuardrailSandwich`, `DebateCoordinator`, etc. |

---

## Graph Topology Reference

### Linear (no parallelism)

```python
nodes = [
    GraphNodeSpec(id="A", coordinator=c_a, deps=[]),
    GraphNodeSpec(id="B", coordinator=c_b, deps=["A"]),
    GraphNodeSpec(id="C", coordinator=c_c, deps=["B"]),
]
# Execution: A → B → C
```

### Fan-Out (parallel branches)

```python
nodes = [
    GraphNodeSpec(id="ingest",    coordinator=c_in,  deps=[]),
    GraphNodeSpec(id="branch_a",  coordinator=c_a,   deps=["ingest"]),
    GraphNodeSpec(id="branch_b",  coordinator=c_b,   deps=["ingest"]),
    GraphNodeSpec(id="branch_c",  coordinator=c_c,   deps=["ingest"]),
    GraphNodeSpec(id="merge",     coordinator=c_m,   deps=["branch_a", "branch_b", "branch_c"]),
]
# Execution: ingest → [branch_a ∥ branch_b ∥ branch_c] → merge
```

### Diamond

```python
nodes = [
    GraphNodeSpec(id="A", coordinator=c_a, deps=[]),
    GraphNodeSpec(id="B", coordinator=c_b, deps=["A"]),
    GraphNodeSpec(id="C", coordinator=c_c, deps=["A"]),
    GraphNodeSpec(id="D", coordinator=c_d, deps=["B", "C"]),
]
# Execution: A → [B ∥ C] → D
```

### Nested Graphs

```python
inner_graph = GraphRunner(nodes=[
    GraphNodeSpec(id="x", coordinator=c_x, deps=[]),
    GraphNodeSpec(id="y", coordinator=c_y, deps=["x"]),
])

outer_graph = GraphRunner(nodes=[
    GraphNodeSpec(id="prepare",    coordinator=c_prep,   deps=[]),
    GraphNodeSpec(id="inner",      coordinator=inner_graph, deps=["prepare"]),  # nested!
    GraphNodeSpec(id="finalise",   coordinator=c_final,  deps=["inner"]),
])
```

---

## Accessing Results Between Nodes

All nodes share `ctx.scratch`. Convention: write results as `{node_id}_result` or `{node_id}_done`.

```python
# Node "analysis" writes:
return AgentStep(
    out_messages=[...],
    state_updates={"analysis_result": {...}, "analysis_done": True},
    stop=True,
)

# Node "report" (which depends on "analysis") reads:
def report_step(ctx: Context) -> AgentStep:
    analysis = ctx.scratch.get("analysis_result", {})
    # use analysis data...
```

---

## Visualising the Graph

Export the graph structure as a Mermaid diagram:

```python
def to_mermaid(nodes: List[GraphNodeSpec]) -> str:
    """Generate a Mermaid flowchart from graph nodes."""
    lines = ["graph TD"]
    for node in nodes:
        lines.append(f"    {node.id}[{node.id}]")
        for dep in node.deps:
            lines.append(f"    {dep} --> {node.id}")
    return "\n".join(lines)

print(to_mermaid(graph.nodes))
```
