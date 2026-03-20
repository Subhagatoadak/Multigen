"""
Multigen Python SDK
===================

Async client:
    from multigen import MultigenClient

Sync client (scripts / notebooks):
    from multigen import SyncMultigenClient

DSL builders:
    from multigen.dsl import WorkflowBuilder, GraphBuilder, branch, when, otherwise

Typed models:
    from multigen.models import GraphNodeDef, GraphEdgeDef, FanOutRequest, ...

Exceptions:
    from multigen.exceptions import WorkflowNotFoundError, DSLValidationError, ...
"""
from .client import MultigenClient
from .sync_client import SyncMultigenClient
from .models import (
    RunResponse,
    WorkflowState,
    NodeState,
    WorkflowHealth,
    WorkflowMetrics,
    GraphDefinition,
    GraphNodeDef,
    GraphEdgeDef,
    InjectNodeRequest,
    FanOutRequest,
    FanOutNodeDef,
    Capability,
)
from .exceptions import (
    MultigenError,
    WorkflowNotFoundError,
    WorkflowStartError,
    DSLValidationError,
    AgentNotFoundError,
    GraphSignalError,
    StateReadError,
    MultigenHTTPError,
)

__version__ = "0.2.0"

__all__ = [
    # Clients
    "MultigenClient",
    "SyncMultigenClient",
    # Models
    "RunResponse",
    "WorkflowState",
    "NodeState",
    "WorkflowHealth",
    "WorkflowMetrics",
    "GraphDefinition",
    "GraphNodeDef",
    "GraphEdgeDef",
    "InjectNodeRequest",
    "FanOutRequest",
    "FanOutNodeDef",
    "Capability",
    # Exceptions
    "MultigenError",
    "WorkflowNotFoundError",
    "WorkflowStartError",
    "DSLValidationError",
    "AgentNotFoundError",
    "GraphSignalError",
    "StateReadError",
    "MultigenHTTPError",
]
