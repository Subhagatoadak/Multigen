from typing import Any, Dict
from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent

from langchain import LLMChain  # hypothetical import
from langchain.prompts import PromptTemplate

@register_agent("LangChainAgent")
class LangChainAgent(BaseAgent):
    """
    Wraps a LangChain LLMChain to generate text based on a prompt template.
    """
    def __init__(self) -> None:
        super().__init__()
        template = PromptTemplate(
            input_variables=["input_text"],
            template="You are a helpful assistant. Process: {input_text}"
        )
        self.chain = LLMChain(llm=None, prompt=template)  # llm injected via config

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        input_text = params.get("text", "")
        output = await self.chain.arun(input_text)
        return {"generated": output}