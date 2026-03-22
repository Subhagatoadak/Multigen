# Tutorial — Event Bus & Messaging

## Overview

The `MessageBus` enables loosely-coupled, asynchronous communication between agents. Instead of direct function calls, agents publish events to topics and subscribe to receive them.

```
AgentA ──publish("topic.name", payload)──► MessageBus
                                                │
                                         subscribers notified
                                                │
AgentB ◄──callback(MessageRecord)──────────────┘
AgentC ◄──callback(MessageRecord)──────────────┘
```

This decouples the producer from consumers: `AgentA` doesn't know or care how many agents are listening. New consumers can be added without modifying `AgentA`.

---

## 1. Basic Pub/Sub

```python
from agentic_codex import MessageBus, MessageRecord

bus = MessageBus()

# Subscribe: register a callback for a topic
received_events = []

def on_analysis_done(record: MessageRecord):
    received_events.append(record)
    print(f"Received: topic={record.topic} payload={record.payload}")

bus.subscribe("analysis.done", on_analysis_done)

# Publish: emit an event on a topic
bus.publish("analysis.done", {
    "agent": "AnalysisAgent",
    "result": {"score": 0.87, "decision": "APPROVE"},
    "run_id": "run-20260322-001",
})

print(f"\nTotal events received: {len(received_events)}")
print(f"First event: {received_events[0].payload}")
```

### MessageRecord Fields

| Field | Type | Description |
|-------|------|-------------|
| `topic` | `str` | The topic name the message was published to |
| `payload` | `dict` | The message payload |
| `message_id` | `str` | Unique message identifier |
| `timestamp` | `float` | Unix timestamp of publication |
| `publisher_id` | `str` | ID of the publishing agent (optional) |

---

## 2. Multiple Subscribers

Multiple agents can subscribe to the same topic. All callbacks are called synchronously when a message is published:

```python
bus = MessageBus()

def logger_handler(record: MessageRecord):
    """Log every event to structured logger."""
    print(f"[LOG] {record.topic}: {record.payload}")

def alerter_handler(record: MessageRecord):
    """Send alerts for high-risk events."""
    if record.payload.get("risk_level") == "HIGH":
        print(f"[ALERT] High risk event: {record.payload.get('decision')}")

def auditor_handler(record: MessageRecord):
    """Write audit trail entry."""
    print(f"[AUDIT] Event recorded for compliance: {record.message_id}")

# Multiple subscribers on the same topic
bus.subscribe("risk.assessed", logger_handler)
bus.subscribe("risk.assessed", alerter_handler)
bus.subscribe("risk.assessed", auditor_handler)

# Single publish triggers all three
bus.publish("risk.assessed", {
    "risk_level": "HIGH",
    "decision": "MANUAL_REVIEW",
    "application_id": "APP-001",
})
```

---

## 3. Attaching the Bus to Agents

Use `MessageBusCapability` to inject the bus into an agent so it can publish during its step function:

```python
from agentic_codex import AgentBuilder, Context, FunctionAdapter
from agentic_codex import MessageBus
from agentic_codex.core.capabilities import MessageBusCapability
from agentic_codex.core.schemas import AgentStep, Message

bus = MessageBus()

# Subscribe before building agents
results_log = []
bus.subscribe("result.produced", lambda r: results_log.append(r.payload))

def producing_step(ctx: Context) -> AgentStep:
    """Agent that publishes a result event after computing."""
    result_value = 42.0  # computed result
    ctx.scratch["result"] = result_value

    # Get bus from context components (injected by MessageBusCapability)
    message_bus = ctx.components.get("message_bus")
    if message_bus:
        message_bus.publish("result.produced", {
            "result": result_value,
            "agent": "producer",
            "run_id": ctx.goal,
        })

    return AgentStep(
        out_messages=[Message(role="assistant", content=f"Published result: {result_value}")],
        state_updates={"result": result_value},
        stop=True,
    )

producer_agent = (
    AgentBuilder("producer", "producer")
    .with_capability(MessageBusCapability(bus=bus, name="message_bus"))
    .with_step(producing_step)
    .build()
)

# Run the agent
ctx = Context(goal="run-001")
producer_agent.run(ctx)

print(f"Events logged: {results_log}")
```

---

## 4. Topic Namespacing

Use dotted topic names to organise events by domain and type:

```
pipeline.stage.started
pipeline.stage.completed
pipeline.stage.failed

agent.llm.called
agent.llm.completed
agent.tool.invoked

risk.assessed
risk.escalated
risk.auto_approved

incident.created
incident.triaged
incident.remediated
incident.closed
```

```python
# Subscribe to all events under a domain using prefix matching
def on_pipeline_event(record: MessageRecord):
    print(f"Pipeline event: {record.topic}")

# Note: basic MessageBus does exact topic matching
# For prefix matching, implement a custom subscription wrapper:

class PrefixBus:
    def __init__(self, bus: MessageBus):
        self.bus = bus
        self._prefix_handlers = {}

    def subscribe_prefix(self, prefix: str, handler):
        """Subscribe to all topics starting with prefix."""
        self._prefix_handlers[prefix] = handler

    def publish(self, topic: str, payload: dict):
        self.bus.publish(topic, payload)
        for prefix, handler in self._prefix_handlers.items():
            if topic.startswith(prefix):
                import time
                record = MessageRecord(
                    topic=topic,
                    payload=payload,
                    message_id=f"msg-{int(time.time()*1000)}",
                    timestamp=time.time(),
                )
                handler(record)
```

---

## 5. Dead Letter Queue (DLQ)

Messages that fail processing can be routed to a Dead Letter Queue for inspection and replay:

```python
from collections import deque
import traceback

class BusWithDLQ:
    """MessageBus with dead-letter queue for failed message handling."""

    def __init__(self):
        self.bus = MessageBus()
        self.dlq = deque(maxlen=1000)  # store last 1000 failed messages

    def subscribe(self, topic: str, handler, *, reraise: bool = False):
        """Subscribe with automatic DLQ routing on handler failure."""
        def safe_handler(record: MessageRecord):
            try:
                handler(record)
            except Exception as exc:
                error_record = {
                    "original_record": record,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
                self.dlq.append(error_record)
                print(f"[DLQ] Message {record.message_id} → dead letter: {exc}")
                if reraise:
                    raise

        self.bus.subscribe(topic, safe_handler)

    def publish(self, topic: str, payload: dict):
        self.bus.publish(topic, payload)

    def replay_dlq(self, topic_filter: str = None):
        """Replay failed messages from the DLQ."""
        replayed = 0
        for item in list(self.dlq):
            record = item["original_record"]
            if topic_filter is None or record.topic == topic_filter:
                print(f"[DLQ REPLAY] Replaying: {record.topic}")
                self.publish(record.topic, record.payload)
                replayed += 1
        return replayed


# Usage
dlq_bus = BusWithDLQ()

def failing_handler(record: MessageRecord):
    """Simulate a handler that sometimes fails."""
    if record.payload.get("bad_data"):
        raise ValueError("Cannot process bad data")
    print(f"Processed: {record.payload}")

dlq_bus.subscribe("data.received", failing_handler)
dlq_bus.publish("data.received", {"value": 42})           # OK
dlq_bus.publish("data.received", {"bad_data": True})      # Goes to DLQ
print(f"DLQ size: {len(dlq_bus.dlq)}")
```

---

## 6. TTL (Time-to-Live) Messages

Implement message expiry to prevent stale messages from being processed:

```python
import time

class TTLMessage:
    def __init__(self, payload: dict, ttl_seconds: float = 60):
        self.payload = payload
        self.expires_at = time.time() + ttl_seconds

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


# Subscriber that respects TTL
def ttl_aware_handler(record: MessageRecord):
    """Skip expired messages."""
    ttl_msg = record.payload.get("_ttl")
    if ttl_msg:
        # Check if the message has expired
        expires_at = record.payload.get("_expires_at", float("inf"))
        if time.time() > expires_at:
            print(f"[TTL] Message {record.message_id} expired, skipping")
            return
    print(f"Processing fresh message: {record.payload}")


def publish_with_ttl(bus: MessageBus, topic: str, payload: dict, ttl_seconds: float = 60):
    """Publish a message with TTL metadata."""
    payload_with_ttl = {
        **payload,
        "_expires_at": time.time() + ttl_seconds,
        "_ttl_seconds": ttl_seconds,
    }
    bus.publish(topic, payload_with_ttl)
```

---

## 7. CommEnvelope for Structured Agent-to-Agent Messages

`CommEnvelope` provides a structured format for agent-to-agent communication with routing metadata:

```python
from agentic_codex import CommEnvelope, CommunicationHub

# CommunicationHub manages directed agent-to-agent messaging
hub = CommunicationHub()

# Register agents
hub.register_agent("agent_a")
hub.register_agent("agent_b")

# Agent A sends a message to Agent B
envelope = CommEnvelope(
    sender_id="agent_a",
    recipient_id="agent_b",
    topic="task.request",
    payload={"task": "analyse_document", "priority": "high"},
    reply_to="task.response",
)

hub.send(envelope)
```

---

## Messaging Patterns Summary

| Pattern | When to use |
|---------|-------------|
| Simple pub/sub | Broadcasting pipeline events to multiple consumers |
| Multiple subscribers | Fan-out notifications (logger + alerter + auditor) |
| DLQ | Reliable processing with failure recovery |
| TTL | Time-sensitive data (sensor readings, market prices) |
| CommEnvelope | Directed agent-to-agent task delegation |

---

## Related Notebooks

- `notebooks/03_signals_control.ipynb` — signal-based workflow control
- `notebooks/11_parallel_bfs_streaming_a2a.ipynb` — agent-to-agent messaging
