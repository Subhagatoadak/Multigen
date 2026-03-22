# API Reference — Parallel Execution & MapReduce

## `MapReduceCoordinator`

Shard work across mapper agents and consolidate with a reducer.

```python
from agentic_codex.patterns import MapReduceCoordinator
```

### Constructor

```python
coordinator = MapReduceCoordinator(
    mappers: Sequence[Agent],
    reducer: Agent,
    *,
    guards: Sequence = (),
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mappers` | `Sequence[Agent]` | required | One or more mapper agents. At least one required. |
| `reducer` | `Agent` | required | Reducer agent called after all mappers complete |
| `guards` | `Sequence` | `()` | Optional guard objects |

**Raises**: `ValueError` if `mappers` is empty.

### `run(goal: str, inputs: dict) → CoordinatorResult`

Execute all mappers (round-robin over shards), then the reducer.

```python
result = coordinator.run(
    goal="Process all documents",
    inputs={
        "shards": ["doc1.txt", "doc2.txt", "doc3.txt"],
        # additional inputs available to all agents
        "config": {"threshold": 0.75},
    }
)
```

**Shard Distribution**: `shard[i]` is assigned to `mapper[i % len(mappers)]`.

**Context Flow**:
1. For each shard, `ctx.scratch["current_shard"] = shard[i]`
2. Mapper runs, its `state_updates` are merged into `ctx.scratch`
3. Mapper's last message content is appended to `ctx.scratch["mapper_outputs"]`
4. After all mappers, reducer runs with full context including all mapper outputs

**Accessing Mapper Outputs in Reducer**:
```python
def my_reducer(ctx: Context) -> AgentStep:
    outputs = ctx.scratch.get("mapper_outputs", [])  # List[str]
    for output_str in outputs:
        data = json.loads(output_str)  # parse if mappers output JSON
    ...
```

**Returns**: `CoordinatorResult` where `messages` contains all mapper + reducer messages.

---

## `SwarmCoordinator`

Runs N explorer agents against the same context, then aggregates.

```python
from agentic_codex.patterns import SwarmCoordinator
```

### Constructor

```python
coordinator = SwarmCoordinator(
    explorers: Sequence[Agent],
    *,
    guards: Sequence = (),
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `explorers` | `Sequence[Agent]` | required | Explorer agents (all see the same full context) |
| `guards` | `Sequence` | `()` | Optional guards |

### `run(goal: str, inputs: dict) → CoordinatorResult`

Run all explorers sequentially, accumulating their outputs.

```python
swarm = SwarmCoordinator(explorers=[analyst_a, analyst_b, analyst_c])
result = swarm.run(
    goal="Explore the classification problem from multiple angles",
    inputs={"data": classification_data}
)
```

**Difference from MapReduce**: All explorers receive the same full context (not shards). Use SwarmCoordinator when you want multiple perspectives on the same input, not parallel processing of different inputs.

---

## `GraphRunner`

Execute a DAG of coordinator nodes. Nodes with satisfied dependencies run in parallel.

```python
from agentic_codex.patterns import GraphRunner, GraphNodeSpec
```

### `GraphNodeSpec`

```python
node = GraphNodeSpec(
    id: str,
    coordinator: CoordinatorBase,
    deps: List[str] = [],
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | `str` | Unique node identifier within this graph |
| `coordinator` | `CoordinatorBase` | Any coordinator: `AssemblyCoordinator`, `MapReduceCoordinator`, `SwarmCoordinator`, nested `GraphRunner`, etc. |
| `deps` | `List[str]` | IDs of nodes that must complete before this node starts |

### `GraphRunner` Constructor

```python
graph = GraphRunner(
    nodes: List[GraphNodeSpec],
)
```

**Raises**: `ValueError` if:
- A `dep` references a non-existent node ID
- Circular dependencies are detected

### `run(goal: str, inputs: dict) → CoordinatorResult`

Execute nodes in topological order, running independent nodes in parallel.

```python
graph = GraphRunner(nodes=[
    GraphNodeSpec(id="A", coordinator=coord_a, deps=[]),
    GraphNodeSpec(id="B", coordinator=coord_b, deps=[]),
    GraphNodeSpec(id="C", coordinator=coord_c, deps=["A", "B"]),
])
result = graph.run(goal="Pipeline run", inputs={})
```

**Execution order**: Topological sort. Nodes with no unmet dependencies run concurrently.

**Context sharing**: All nodes share the same `Context` object. State written by one node is visible to all subsequent nodes via `ctx.scratch`.

---

## `MeshCoordinator`

All-to-all peer communication: each agent can interact with every other agent.

```python
from agentic_codex.patterns import MeshCoordinator
```

### Constructor

```python
coordinator = MeshCoordinator(
    agents: Sequence[Agent],
    rounds: int = 1,
    *,
    guards: Sequence = (),
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agents` | `Sequence[Agent]` | required | Mesh participant agents |
| `rounds` | `int` | `1` | Number of communication rounds |
| `guards` | `Sequence` | `()` | Optional guards |

---

## `MixtureOfExpertsCommittee`

Route inputs to the most appropriate expert agent based on a gating function.

```python
from agentic_codex.patterns import MixtureOfExpertsCommittee
```

### Constructor

```python
coordinator = MixtureOfExpertsCommittee(
    experts: Sequence[Agent],
    gating_agent: Optional[Agent] = None,
    *,
    guards: Sequence = (),
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `experts` | `Sequence[Agent]` | required | Expert agent pool |
| `gating_agent` | `Agent \| None` | `None` | Agent that selects which experts to activate |
| `guards` | `Sequence` | `()` | Optional guards |

---

## Pattern Selection Guide

| Scenario | Recommended Pattern |
|----------|---------------------|
| Process 100 documents in parallel | `MapReduceCoordinator` with 4-8 mappers |
| Get 5 different perspectives on one problem | `SwarmCoordinator` with 5 explorers |
| Fan-out to independent branches, then merge | `GraphRunner` with parallel nodes |
| First good answer wins | Custom Race with `threading` |
| Route to specialist based on input type | `GuildRouter` or `MixtureOfExpertsCommittee` |
| All agents discuss and collaborate | `MeshCoordinator` |
