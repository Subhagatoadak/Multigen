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

from .snapshot import InMemorySnapshotStore, Snapshot, SnapshotStore, SQLiteSnapshotStore
from .debugger import WorkflowDebugger

# ── Memory system ─────────────────────────────────────────────────────────────
from .memory import (
    Episode,
    EpisodicMemory,
    MemoryEntry,
    MemoryManager,
    SemanticMemory,
    ShortTermMemory,
    WorkingMemory,
)

# ── Caching system ────────────────────────────────────────────────────────────
from .cache import (
    AsyncCache,
    CacheEntry,
    CacheManager,
    LRUCache,
    MultiTierCache,
    TTLCache,
    cached,
    make_cache_key,
)

# ── Step composition ──────────────────────────────────────────────────────────
from .compose import (
    BranchStep,
    Compose,
    FanInStep,
    LoopStep,
    ParallelStep,
    Step,
    StepResult,
    StepSequence,
)

# ── Hierarchical structures ───────────────────────────────────────────────────
from .hierarchy import (
    AgentGroup,
    AgentHierarchy,
    AgentRole,
    AgentSpec,
    HierarchicalPipeline,
    StepKind,
    TypedStep,
)

# ── Advanced memory ───────────────────────────────────────────────────────────
from .advanced_memory import (
    AdvancedMemoryManager,
    ContextualMemory,
    ForgettingCurve,
    MemoryIndex,
    MemoryTrace,
    PersistentMemory,
    VectorMemory,
    VectorRecord,
)

# ── Polymorphism & shape-shifting ────────────────────────────────────────────
from .polymorphic import (
    AgentMixin,
    AgentShape,
    CachingMixin,
    DynamicAgent,
    LoggingMixin,
    MultiDispatch,
    PolymorphicAgent,
    RetryMixin,
    ShapeRegistry,
    TypeAdapter,
)

# ── Performance optimization ──────────────────────────────────────────────────
from .performance import (
    AgentProfiler,
    BatchExecutor,
    ConnectionPool,
    ExecutionOptimizer,
    ExecutionProfile,
    LazyValue,
    RateLimiter,
    lazy,
)

# ── Session management ────────────────────────────────────────────────────────
from .session import (
    InMemorySessionStore,
    SessionContext,
    SessionEvent,
    SessionManager,
    SessionMiddleware,
    SessionState,
    SessionStore,
)

# ── State initialization ──────────────────────────────────────────────────────
from .state_init import (
    ComputedState,
    ReactiveState,
    StateInitializer,
    StateMachineState,
    StateSchema,
    StateTransition,
    StateValidator,
    ValidationError,
)

# ── Inheritance & overloading ─────────────────────────────────────────────────
from .inheritance import (
    CachingTrait,
    InheritableAgent,
    LoggingTrait,
    MultiMethod,
    RetryTrait,
    TimingTrait,
    Trait,
    ValidatingTrait,
    build_agent,
    cooperative_init,
    implements,
    mixin,
    overload,
)

# ── Advanced messaging ────────────────────────────────────────────────────────
from .messaging import (
    AdvancedMessage,
    AdvancedMessageBus,
    DeadLetterQueue,
    MessageFilter,
    MessagePipeline,
    MessageRouter,
    PriorityMessageQueue,
    Subscription,
)

# ── Multi-model routing ───────────────────────────────────────────────────────
from .routing import (
    AdaptiveRouter,
    ContentRouter,
    CostRouter,
    FallbackRouter,
    LatencyRouter,
    ModelCapability,
    ModelHealth,
    ModelPool,
    ModelRouter,
    ModelSpec,
    QualityRouter,
    RoundRobinRouter,
    RoutingDecision,
    RoutingStrategy,
)

# ── Tool registry & sandboxing ────────────────────────────────────────────────
from .tools import (
    PermissionPolicy,
    PermissionedRegistry,
    Sandbox,
    SandboxConfig,
    SandboxedTool,
    Tool,
    ToolCall,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolValidator,
    tool as tool_decorator,
)

# ── Planning / ToT / GoT ──────────────────────────────────────────────────────
from .planning import (
    ChainOfThought,
    ExecutionPlan,
    GraphOfThoughts,
    PlanAndExecute,
    PlanStep,
    Planner,
    ReActPlanner,
    StepBackPlanner,
    ThoughtNode,
    TreeOfThoughts,
)

# ── Workflow versioning ───────────────────────────────────────────────────────
from .versioning import (
    ChangeLog,
    InMemoryVersionStore,
    SQLiteVersionStore,
    VersionStore,
    VersionedWorkflow,
    WorkflowDiff,
    WorkflowVersion,
)

# ── Scheduling & triggers ─────────────────────────────────────────────────────
from .scheduler import (
    CronSchedule,
    IntervalSchedule,
    JobResult,
    OnceSchedule,
    Schedule,
    ScheduledJob,
    Scheduler,
    Trigger,
)

# ── Continuous learning ───────────────────────────────────────────────────────
from .learning import (
    AdaptivePrompt,
    ContinuousLearner,
    ExperienceReplay,
    FeedbackEntry,
    FeedbackStore,
    FewShotSelector,
    OnlineLearner,
    RewardSignal,
)

# ── Evaluation & measurement framework ───────────────────────────────────────
from .eval import (
    Benchmark,
    BenchmarkResult,
    ContainsMatch,
    CostEstimate,
    CustomMetric,
    EvalCase,
    EvalDataset,
    EvalReport,
    EvalRegistry,
    EvalResult,
    EvalSuite,
    Evaluator,
    ExactMatch,
    F1Score,
    JSONFieldMatch,
    LLMJudge,
    Latency,
    Metric,
    RegexMatch,
    TokenCount,
    get_eval_registry,
)

# ── Persistence (durable SQLite-backed stores) ────────────────────────────────
from .persistence import (
    Checkpoint,
    CheckpointedRuntime,
    DurableQueue,
    PersistentEpisodicMemory,
    SQLiteCheckpointStore,
    SQLiteMemoryStore,
    SQLiteSessionStore,
    open_persistent_stores,
)

# ── Workflow A/B testing, canary, rollback ────────────────────────────────────
from .workflow_ab import (
    ABTest,
    ABTestResult,
    CanaryRollout,
    CompatibilityChecker,
    CompatibilityReport,
    MigrationFn,
    RollbackManager,
    RouteDecision,
    TrafficSplit,
    WorkflowRouter,
)

# ── Safety: injection detection, output sanitization, PII redaction ───────────
from .safety import (
    InjectionDetector,
    InjectionPattern,
    InjectionResult,
    OutputSanitizer,
    PIIDetector,
    PIIPattern,
    PIIRedactor,
    PIISpan,
    SafetyGuard,
    SafetyReport,
)

# ── Resilience: deadline management, retry scheduling ─────────────────────────
from .resilience import (
    Deadline,
    DeadlineError,
    DeadlineGuard,
    DeadlineManager,
    RetryPolicy,
    RetryResult,
    WorkflowRetry,
    WorkflowRetryResult,
)

# ── Advanced planning: MCTS, hierarchical decomp, AutoGPT, hier. summary ──────
from .planning_advanced import (
    AutoGPTQueue,
    AutoGPTResult,
    HierarchicalDecomposer,
    HierarchicalSummariser,
    MCTSNode,
    MCTSPlanner,
    SummaryNode,
    TaskNode,
    TaskQueueItem,
)

# ── Streaming: token streams, parallel aggregation, partial results ───────────
from .streaming import (
    PartialResult,
    PartialResultBus,
    ParallelStreamer,
    StreamAggregator,
    StreamBuffer,
    StreamToken,
    StreamingAgent,
)

# ── Optimization: prompt bandit, few-shot library, agent specialisation ───────
from .optimization import (
    AgentSpecialisation,
    EpisodicFeedbackLoop,
    FewShotLibrary,
    FewShotLibraryEntry,
    OptimizationManager,
    PromptBandit,
    PromptVariant,
)

__version__ = "0.6.0"

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
    # Messaging (basic)
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
    # Snapshot & debugging
    "InMemorySnapshotStore", "Snapshot", "SnapshotStore", "SQLiteSnapshotStore",
    "WorkflowDebugger",
    # Memory system
    "Episode", "EpisodicMemory", "MemoryEntry", "MemoryManager",
    "SemanticMemory", "ShortTermMemory", "WorkingMemory",
    # Caching
    "AsyncCache", "CacheEntry", "CacheManager", "LRUCache",
    "MultiTierCache", "TTLCache", "cached", "make_cache_key",
    # Step composition
    "BranchStep", "Compose", "FanInStep", "LoopStep",
    "ParallelStep", "Step", "StepResult", "StepSequence",
    # Hierarchical structures
    "AgentGroup", "AgentHierarchy", "AgentRole", "AgentSpec",
    "HierarchicalPipeline", "StepKind", "TypedStep",
    # Advanced messaging
    "AdvancedMessage", "AdvancedMessageBus", "DeadLetterQueue",
    "MessageFilter", "MessagePipeline", "MessageRouter",
    "PriorityMessageQueue", "Subscription",
    # Advanced memory
    "AdvancedMemoryManager", "ContextualMemory", "ForgettingCurve",
    "MemoryIndex", "MemoryTrace", "PersistentMemory", "VectorMemory", "VectorRecord",
    # Polymorphism & shape-shifting
    "AgentMixin", "AgentShape", "CachingMixin", "DynamicAgent",
    "LoggingMixin", "MultiDispatch", "PolymorphicAgent",
    "RetryMixin", "ShapeRegistry", "TypeAdapter",
    # Performance optimization
    "AgentProfiler", "BatchExecutor", "ConnectionPool", "ExecutionOptimizer",
    "ExecutionProfile", "LazyValue", "RateLimiter", "lazy",
    # Session management
    "InMemorySessionStore", "SessionContext", "SessionEvent",
    "SessionManager", "SessionMiddleware", "SessionState", "SessionStore",
    # State initialization
    "ComputedState", "ReactiveState", "StateInitializer", "StateMachineState",
    "StateSchema", "StateTransition", "StateValidator", "ValidationError",
    # Inheritance & overloading
    "CachingTrait", "InheritableAgent", "LoggingTrait", "MultiMethod",
    "RetryTrait", "TimingTrait", "Trait", "ValidatingTrait",
    "build_agent", "cooperative_init", "implements", "mixin", "overload",
    # Persistence (durable SQLite-backed stores)
    "Checkpoint", "CheckpointedRuntime", "DurableQueue",
    "PersistentEpisodicMemory", "SQLiteCheckpointStore",
    "SQLiteMemoryStore", "SQLiteSessionStore", "open_persistent_stores",
    # Multi-model routing
    "AdaptiveRouter", "ContentRouter", "CostRouter", "FallbackRouter",
    "LatencyRouter", "ModelCapability", "ModelHealth", "ModelPool",
    "ModelRouter", "ModelSpec", "QualityRouter", "RoundRobinRouter",
    "RoutingDecision", "RoutingStrategy",
    # Tool registry & sandboxing
    "PermissionPolicy", "PermissionedRegistry", "Sandbox", "SandboxConfig",
    "SandboxedTool", "Tool", "ToolCall", "ToolRegistry", "ToolResult",
    "ToolSpec", "ToolValidator", "tool_decorator",
    # Planning / ToT / GoT
    "ChainOfThought", "ExecutionPlan", "GraphOfThoughts", "PlanAndExecute",
    "PlanStep", "Planner", "ReActPlanner", "StepBackPlanner",
    "ThoughtNode", "TreeOfThoughts",
    # Workflow versioning
    "ChangeLog", "InMemoryVersionStore", "SQLiteVersionStore", "VersionStore",
    "VersionedWorkflow", "WorkflowDiff", "WorkflowVersion",
    # Scheduling & triggers
    "CronSchedule", "IntervalSchedule", "JobResult", "OnceSchedule",
    "Schedule", "ScheduledJob", "Scheduler", "Trigger",
    # Continuous learning
    "AdaptivePrompt", "ContinuousLearner", "ExperienceReplay",
    "FeedbackEntry", "FeedbackStore", "FewShotSelector",
    "OnlineLearner", "RewardSignal",
    # Evaluation & measurement
    "Benchmark", "BenchmarkResult",
    "ContainsMatch", "CostEstimate", "CustomMetric",
    "EvalCase", "EvalDataset", "EvalReport", "EvalRegistry",
    "EvalResult", "EvalSuite", "Evaluator",
    "ExactMatch", "F1Score", "JSONFieldMatch", "LLMJudge",
    "Latency", "Metric", "RegexMatch", "TokenCount",
    "get_eval_registry",
    # Workflow A/B testing, canary, rollback
    "ABTest", "ABTestResult", "CanaryRollout", "CompatibilityChecker",
    "CompatibilityReport", "MigrationFn", "RollbackManager",
    "RouteDecision", "TrafficSplit", "WorkflowRouter",
    # Safety
    "InjectionDetector", "InjectionPattern", "InjectionResult",
    "OutputSanitizer", "PIIDetector", "PIIPattern", "PIIRedactor",
    "PIISpan", "SafetyGuard", "SafetyReport",
    # Resilience
    "Deadline", "DeadlineError", "DeadlineGuard", "DeadlineManager",
    "RetryPolicy", "RetryResult", "WorkflowRetry", "WorkflowRetryResult",
    # Advanced planning
    "AutoGPTQueue", "AutoGPTResult", "HierarchicalDecomposer",
    "HierarchicalSummariser", "MCTSNode", "MCTSPlanner",
    "SummaryNode", "TaskNode", "TaskQueueItem",
    # Streaming
    "PartialResult", "PartialResultBus", "ParallelStreamer",
    "StreamAggregator", "StreamBuffer", "StreamToken", "StreamingAgent",
    # Optimization
    "AgentSpecialisation", "EpisodicFeedbackLoop", "FewShotLibrary",
    "FewShotLibraryEntry", "OptimizationManager", "PromptBandit", "PromptVariant",
]
