"""Typed exceptions for the Multigen Python SDK."""
from __future__ import annotations


class MultigenError(Exception):
    """Base class for all Multigen SDK errors."""


class WorkflowNotFoundError(MultigenError):
    """Raised when the target workflow ID does not exist in Temporal."""


class WorkflowStartError(MultigenError):
    """Raised when a workflow cannot be started."""


class DSLValidationError(MultigenError):
    """Raised when a workflow DSL fails server-side validation."""


class AgentNotFoundError(MultigenError):
    """Raised when the requested agent is not registered."""


class GraphSignalError(MultigenError):
    """Raised when a graph signal (interrupt, jump, etc.) cannot be delivered."""


class StateReadError(MultigenError):
    """Raised when the distributed state backend returns an error."""


class MultigenHTTPError(MultigenError):
    """Raised for unexpected HTTP errors from the orchestrator."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")
