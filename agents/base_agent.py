# ============================================
# File: agents/base_agent.py
# ============================================
import logging
from typing import Any, Dict


class BaseAgent:
    """
    Abstract base class for all agents in the Multigen framework.

    Provides a default `run` that echoes inputs, and a `__call__` wrapper
    for logging and error handling. Subclasses can override `run`.
    """

    def __init__(self) -> None:
        # Each agent gets its own logger
        self.logger = logging.getLogger(f"agent.{self.__class__.__name__}")

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Default agent logic: echoes back the input parameters.

        Subclasses may override this method to implement actual behavior.
        """
        self.logger.warning(
            "BaseAgent.run: no implementation provided, echoing input params"
        )
        return {"echo": params}

    async def __call__(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Wrapper that invokes `run` with logging and centralized error handling.
        """
        self.logger.info("Starting agent with params: %s", params)
        try:
            result = await self.run(params)
            self.logger.info("Agent completed successfully: %s", result)
            return result
        except Exception:
            self.logger.exception(
                "Agent '%s' failed", self.__class__.__name__
            )
            # Re-raise to let the workflow handle retries or error paths
            raise
