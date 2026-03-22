"""
Multigen Python SDK  v0.3.0
============================
The most advanced open-source agentic framework.

Local (no-server) usage
-----------------------
    from multigen import (
        agent, FunctionAgent, LLMAgent, RouterAgent, AggregatorAgent,
        CircuitBreakerAgent, RetryAgent, MemoryAgent,
        Chain, Pipeline,
        Parallel, FanOut, MapReduce, Race, Batch,
        Graph,
        StateMachine,
        InMemoryBus, Message,
        Runtime,
    )

    # 1. Simple chain
    result = await Chain([
        FunctionAgent("tokenize", fn=lambda ctx: {"tokens": ctx["text"].split()}),
        LLMAgent("summarise", prompt="Summarise these tokens: {tokens}"),
    ]).run({"text": "Hello world..."})

    # 2. Parallel fan-out
    result = await Parallel([
        LLMAgent("finance",    prompt="Finance outlook: {topic}"),
        LLMAgent("technology", prompt="Tech outlook: {topic}"),
    ]).run({"topic": "AI in 2025"})

    # 3. Probabilistic state machine
    sm = StateMachine(start_state="search", terminal_states={"report"})
    sm.state("search",   LLMAgent("searcher",  prompt="Search: {query}"))
    sm.state("evaluate", LLMAgent("evaluator", prompt="Evaluate: {search.response}"))
    sm.state("report",   LLMAgent("reporter",  prompt="Report: {evaluate.response}"))
    sm.transition("search",   "evaluate", prob=1.0)
    sm.transition("evaluate", "report",   prob=0.7)
    sm.transition("evaluate", "search",   prob=0.3)
    result = await sm.run({"query": "market trends"})

    # 4. Graph (DAG)
    g = Graph(name="pipeline")
    g.node("a", FunctionAgent("a", fn=step_a))
    g.node("b", LLMAgent("b", prompt="..."))
    g.edge("a", "b")
    result = await g.run({"input": "..."})

    # 5. Runtime (unified entry-point, optional Temporal/Kafka)
    rt = Runtime(simulator_url="http://localhost:8003")   # push events to UI
    result = await rt.run_chain([agent_a, agent_b], ctx={})
    rt.use_temporal(host="localhost:7233")   # optional
    rt.use_kafka(bootstrap_servers="kafka:9092")   # optional

Remote Multigen server usage (existing API)
-------------------------------------------
    from multigen import MultigenClient, SyncMultigenClient, WorkflowBuilder

    async with MultigenClient("http://localhost:8000") as client:
        run = await client.run_workflow(
            WorkflowBuilder().sequential([...]).build(),
            payload={"topic": "AI"}
        )
"""

# ── Remote API clients (existing) ───────────────────────────────────────────
from .client import MultigenClient
from .sync_client import SyncMultigenClient
from .dsl import WorkflowBuilder, GraphBuilder

# ── Local agent primitives ───────────────────────────────────────────────────
from .agent import (
    BaseAgent,
    FunctionAgent,
    LLMAgent,
    RouterAgent,
    AggregatorAgent,
    FilterAgent,
    TransformAgent,
    CircuitBreakerAgent,
    RetryAgent,
    HumanInLoopAgent,
    MemoryAgent,
    agent,           # decorator
)

# ── Sequential execution ─────────────────────────────────────────────────────
from .chain import Chain, Pipeline, logging_middleware, tracing_middleware

# ── Parallel execution ───────────────────────────────────────────────────────
from .parallel import Parallel, FanOut, MapReduce, Race, Batch

# ── Graph executor ───────────────────────────────────────────────────────────
from .graph import Graph, GraphResult

# ── Probabilistic state machine ──────────────────────────────────────────────
from .state_machine import StateMachine, Sampler, EnsembleResult

# ── Event bus & messaging ────────────────────────────────────────────────────
from .bus import InMemoryBus, Message, get_default_bus

# ── Unified runtime ──────────────────────────────────────────────────────────
from .runtime import Runtime, get_runtime

# ── Models / exceptions (existing) ──────────────────────────────────────────
from .models import (
    RunResponse, WorkflowState, NodeState, WorkflowHealth, WorkflowMetrics,
    GraphDefinition, GraphNodeDef, GraphEdgeDef, InjectNodeRequest,
    FanOutRequest, FanOutNodeDef, Capability,
)
from .exceptions import (
    MultigenError, WorkflowNotFoundError, WorkflowStartError,
    DSLValidationError, AgentNotFoundError, GraphSignalError,
    StateReadError, MultigenHTTPError,
)

__version__ = "0.3.0"

__all__ = [
    # Clients
    "MultigenClient", "SyncMultigenClient",
    # DSL
    "WorkflowBuilder", "GraphBuilder",
    # Agents
    "BaseAgent", "FunctionAgent", "LLMAgent", "RouterAgent", "AggregatorAgent",
    "FilterAgent", "TransformAgent", "CircuitBreakerAgent", "RetryAgent",
    "HumanInLoopAgent", "MemoryAgent", "agent",
    # Execution patterns
    "Chain", "Pipeline", "logging_middleware", "tracing_middleware",
    "Parallel", "FanOut", "MapReduce", "Race", "Batch",
    "Graph", "GraphResult",
    "StateMachine", "Sampler", "EnsembleResult",
    # Messaging
    "InMemoryBus", "Message", "get_default_bus",
    # Runtime
    "Runtime", "get_runtime",
    # Models
    "RunResponse", "WorkflowState", "NodeState", "WorkflowHealth", "WorkflowMetrics",
    "GraphDefinition", "GraphNodeDef", "GraphEdgeDef", "InjectNodeRequest",
    "FanOutRequest", "FanOutNodeDef", "Capability",
    # Exceptions
    "MultigenError", "WorkflowNotFoundError", "WorkflowStartError",
    "DSLValidationError", "AgentNotFoundError", "GraphSignalError",
    "StateReadError", "MultigenHTTPError",
]
