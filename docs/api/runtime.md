# API Reference — Runtime

## `SyncMultigenClient`

Synchronous Python client for interacting with the Multigen orchestrator. Wraps the async client for use in notebooks and scripts.

```python
from multigen import SyncMultigenClient
```

### Constructor

```python
client = SyncMultigenClient(
    base_url: str = "http://localhost:8009",
    timeout: float = 60.0,
    api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_url` | `str` | `"http://localhost:8009"` | Orchestrator API base URL |
| `timeout` | `float` | `60.0` | Request timeout in seconds |
| `api_key` | `str \| None` | `None` | Optional API key for authentication |

### Connection Methods

#### `ping() → bool`

Check if the orchestrator is reachable.

```python
assert client.ping(), "Orchestrator not reachable"
```

#### `version() → str`

Return the orchestrator version string.

```python
print(f"Orchestrator version: {client.version()}")
```

#### `list_agents() → List[dict]`

Return all registered agents.

```python
agents = client.list_agents()
for agent in agents:
    print(f"  {agent['name']} ({agent['type']})")
```

---

### Workflow Methods

#### `run_workflow(workflow_def: dict, payload: dict) → WorkflowResponse`

Submit a workflow DSL definition for execution.

```python
from multigen.dsl import WorkflowBuilder

workflow = (
    WorkflowBuilder()
    .step("step_name")
        .agent("EchoAgent")
        .params({"message": "hello"})
        .timeout(30)
        .done()
    .build()
)

response = client.run_workflow(workflow_def=workflow, payload={"key": "value"})
print(f"Instance ID: {response.instance_id}")
```

**Returns** `WorkflowResponse`:

| Field | Type | Description |
|-------|------|-------------|
| `instance_id` | `str` | Unique workflow instance identifier |
| `status` | `str` | Initial status (`"RUNNING"`) |

#### `run_graph(graph_def: dict, payload: dict) → WorkflowResponse`

Submit a graph DSL definition.

```python
from multigen.dsl import GraphBuilder

graph = (
    GraphBuilder()
    .node("parse").agent("EchoAgent").params({}).timeout(30).done()
    .node("analyse").agent("EchoAgent").params({}).timeout(60).done()
    .edge("parse", "analyse")
    .entry("parse")
    .max_cycles(10)
    .build()
)

response = client.run_graph(graph_def=graph, payload={})
```

#### `get_state(instance_id: str) → dict`

Poll workflow state.

```python
state = client.get_state(instance_id)
print(f"Status: {state['status']}")
print(f"Step outputs: {state.get('step_outputs', {})}")
print(f"Metrics: {state.get('metrics', {})}")
```

**State dict keys**:

| Key | Type | Description |
|-----|------|-------------|
| `instance_id` | `str` | Workflow instance ID |
| `status` | `str` | `"RUNNING"`, `"COMPLETED"`, `"FAILED"`, `"CANCELLED"` |
| `step_outputs` | `dict` | Per-step output data |
| `metrics` | `dict` | Runtime metrics (latency, token counts, etc.) |
| `error` | `str \| None` | Error message if `status == "FAILED"` |

#### `cancel(instance_id: str) → None`

Cancel a running workflow.

```python
client.cancel(instance_id)
```

---

### Fan-Out Methods

#### `fan_out(instance_id: str, request: FanOutRequest) → dict`

Inject a fan-out into a running workflow.

```python
from multigen import FanOutRequest, FanOutNodeDef

fan_out_req = FanOutRequest(
    group_id="scenario_analysis",
    consensus="highest_confidence",
    nodes=[
        FanOutNodeDef(id="bear", agent="EchoAgent", params={"scenario": "bear", "confidence": 0.55}),
        FanOutNodeDef(id="base", agent="EchoAgent", params={"scenario": "base", "confidence": 0.78}),
        FanOutNodeDef(id="bull", agent="EchoAgent", params={"scenario": "bull", "confidence": 0.91}),
    ],
)

result = client.fan_out(instance_id, fan_out_req)
```

**`FanOutRequest` Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `group_id` | `str` | Unique identifier for this fan-out group |
| `consensus` | `str` | Strategy: `"highest_confidence"`, `"aggregate"`, `"majority_vote"`, `"first_success"` |
| `nodes` | `List[FanOutNodeDef]` | Nodes to run in parallel |

**`FanOutNodeDef` Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | `str` | Node identifier within the fan-out group |
| `agent` | `str` | Registered agent name |
| `params` | `dict` | Agent parameters |

---

### Event Methods

#### `push_event(instance_id: str, event_type: str, data: dict) → None`

Push a custom event to the simulator's event stream for real-time visualisation.

```python
client.push_event(
    instance_id=instance_id,
    event_type="agent.completed",
    data={"agent": "AnalysisAgent", "duration_ms": 342, "confidence": 0.87}
)
```

---

## `WorkflowBuilder`

Fluent DSL builder for linear workflow definitions.

```python
from multigen.dsl import WorkflowBuilder

workflow = (
    WorkflowBuilder()
    .step("step_name")               # start a step definition
        .agent("AgentName")          # registered agent to run
        .params({"key": "value"})    # parameters passed to agent
        .timeout(30)                 # timeout in seconds
        .depends_on("prior_step")    # optional dependency
        .done()                      # end step definition
    .build()                         # return the workflow dict
)
```

---

## `GraphBuilder`

Fluent DSL builder for graph workflow definitions.

```python
from multigen.dsl import GraphBuilder

graph = (
    GraphBuilder()
    .node("node_name")               # start a node definition
        .agent("AgentName")
        .params({})
        .timeout(60)
        .done()
    .edge("source", "target")        # define a directed edge
    .entry("first_node")             # set the entry node
    .max_cycles(10)                  # maximum execution cycles
    .build()
)
```
