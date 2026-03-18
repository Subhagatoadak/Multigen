"""
LangChainAgent — wraps a LangChain LCEL chain using ChatOpenAI.

Required packages: langchain-core langchain-openai
These are listed in requirements.txt under optional integrations.
"""
import os
from typing import Any, Dict

from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False


@register_agent("LangChainAgent")
class LangChainAgent(BaseAgent):
    """
    Uses a LangChain LCEL chain (prompt | LLM | parser) to process text.

    Params:
        text (str): The input text to process.
        system_prompt (str, optional): Override the system prompt. Defaults to
            "You are a helpful assistant."
    """

    def __init__(self) -> None:
        super().__init__()
        if not _LANGCHAIN_AVAILABLE:
            raise ImportError(
                "LangChainAgent requires 'langchain-core' and 'langchain-openai'. "
                "Install them with: pip install langchain-core langchain-openai"
            )

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required for LangChainAgent")

        llm = ChatOpenAI(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            api_key=api_key,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        )
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            ("human", "{input_text}"),
        ])
        self._chain = self._prompt | llm | StrOutputParser()

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        input_text = params.get("text", "")
        system_prompt = params.get("system_prompt", "You are a helpful assistant.")
        output = await self._chain.ainvoke({
            "input_text": input_text,
            "system_prompt": system_prompt,
        })
        return {"generated": output}
