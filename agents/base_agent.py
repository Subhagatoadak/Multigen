from abc import ABC, abstractmethod
import logging
from typing import Any, Dict


class BaseAgent(ABC):
    """
    Abstract base class for all agents in the Multigen framework.

    Subclasses must implement the async `run` method.

    Provides a default `__call__` wrapper for logging and error handling.
    """

    def __init__(self) -> None:
        # Each agent gets its own logger
        self.logger = logging.getLogger(f"agent.{self.__class__.__name__}")

    @abstractmethod
    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Default agent logic: echoes back the input parameters.

        Subclasses should override this method to implement actual behavior.
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
        except Exception as exc:
            self.logger.exception("Agent '%s' failed", self.__class__.__name__)
            # Re-raise to let the workflow handle retries or error paths
            raise