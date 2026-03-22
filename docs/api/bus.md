# API Reference — Message Bus

## `MessageBus`

In-memory publish/subscribe message bus for agent-to-agent communication.

```python
from agentic_codex import MessageBus
```

### Constructor

```python
bus = MessageBus()
```

No parameters required. The bus is initialised with an empty subscription registry.

### `subscribe(topic: str, handler: Callable[[MessageRecord], None]) → None`

Register a callback for a topic. Multiple handlers can subscribe to the same topic.

```python
def my_handler(record: MessageRecord):
    print(f"Received: {record.topic} → {record.payload}")

bus.subscribe("pipeline.completed", my_handler)
```

**Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | `str` | Exact topic string to subscribe to |
| `handler` | `Callable[[MessageRecord], None]` | Callback invoked synchronously when a message is published to this topic |

### `publish(topic: str, payload: dict) → None`

Publish a message to a topic. All registered handlers are called synchronously.

```python
bus.publish("pipeline.completed", {
    "pipeline": "credit_risk",
    "run_id": "run-001",
    "duration_ms": 1240,
    "decision": "APPROVE",
})
```

**Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | `str` | Topic to publish to |
| `payload` | `dict` | Message data |

---

## `MessageRecord`

A published message, passed to subscriber callbacks.

```python
from agentic_codex import MessageRecord
```

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `topic` | `str` | The topic this message was published to |
| `payload` | `dict` | The message payload |
| `message_id` | `str` | Unique message identifier (UUID) |
| `timestamp` | `float` | Unix timestamp of publication |
| `publisher_id` | `str \| None` | Optional identifier of the publishing agent |

---

## `CommEnvelope`

Structured message format for directed agent-to-agent communication.

```python
from agentic_codex import CommEnvelope
```

### Constructor

```python
envelope = CommEnvelope(
    sender_id: str,
    recipient_id: str,
    topic: str,
    payload: dict,
    reply_to: Optional[str] = None,
    correlation_id: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sender_id` | `str` | required | Sending agent identifier |
| `recipient_id` | `str` | required | Target agent identifier |
| `topic` | `str` | required | Message topic |
| `payload` | `dict` | required | Message data |
| `reply_to` | `str \| None` | `None` | Topic the recipient should publish its response to |
| `correlation_id` | `str \| None` | `None` | For correlating request/response pairs |

---

## `CommunicationHub`

Manages directed agent-to-agent messaging with routing.

```python
from agentic_codex import CommunicationHub
```

### Constructor

```python
hub = CommunicationHub()
```

### Methods

#### `register_agent(agent_id: str) → None`

Register an agent with the hub.

```python
hub.register_agent("analyser-001")
hub.register_agent("reporter-001")
```

#### `send(envelope: CommEnvelope) → None`

Send a directed message to a registered agent.

```python
hub.send(CommEnvelope(
    sender_id="analyser-001",
    recipient_id="reporter-001",
    topic="analysis.ready",
    payload={"score": 0.87, "decision": "APPROVE"},
    reply_to="report.generated",
))
```

#### `subscribe(agent_id: str, topic: str, handler: Callable) → None`

Subscribe an agent to receive messages on a topic.

```python
hub.subscribe("reporter-001", "analysis.ready", handle_analysis)
```

---

## `MessageBusCapability`

Injects a `MessageBus` into an agent via the capability system.

```python
from agentic_codex.core.capabilities import MessageBusCapability

bus       = MessageBus()
capability = MessageBusCapability(bus=bus, name="message_bus")

agent = (
    AgentBuilder("producer", "producer")
    .with_capability(capability)
    .with_step(step_fn)
    .build()
)

# Access inside step_fn:
def step_fn(ctx: Context) -> AgentStep:
    bus = ctx.components["message_bus"]
    bus.publish("event.topic", {"data": "value"})
    ...
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `bus` | `MessageBus` | required | The message bus instance |
| `name` | `str` | `"message_bus"` | Key used to access via `ctx.components[name]` |

---

## Topic Naming Conventions

Use dotted namespaces to organise topics:

```
{domain}.{entity}.{event}

pipeline.stage.started
pipeline.stage.completed
pipeline.failed

agent.{agent_name}.called
agent.{agent_name}.completed
agent.{agent_name}.failed

decision.risk.assessed
decision.approved
decision.escalated

incident.{severity}.created
incident.{id}.remediated
```
