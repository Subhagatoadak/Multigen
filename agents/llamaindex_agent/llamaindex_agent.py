"""
LlamaIndexAgent — wraps a LlamaIndex VectorStoreIndex for document retrieval.

Required packages: llama-index-core llama-index-llms-openai
These are listed in requirements.txt under optional integrations.

Set the LLAMAINDEX_DOCS_PATH environment variable to your documents directory
(defaults to ./data/docs).
"""
import os
from typing import Any, Dict

from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent

try:
    from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Settings
    from llama_index.llms.openai import OpenAI as LlamaOpenAI
    _LLAMAINDEX_AVAILABLE = True
except ImportError:
    _LLAMAINDEX_AVAILABLE = False


@register_agent("LlamaIndexAgent")
class LlamaIndexAgent(BaseAgent):
    """
    Uses LlamaIndex to build a vector index over a local document corpus and
    answer natural-language queries via similarity search + LLM synthesis.

    Params:
        query (str): The question to answer against the document corpus.
        top_k (int, optional): Number of source nodes to retrieve. Defaults to 3.
    """

    def __init__(self) -> None:
        super().__init__()
        if not _LLAMAINDEX_AVAILABLE:
            raise ImportError(
                "LlamaIndexAgent requires 'llama-index-core' and 'llama-index-llms-openai'. "
                "Install them with: pip install llama-index-core llama-index-llms-openai"
            )

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required for LlamaIndexAgent")

        docs_path = os.getenv("LLAMAINDEX_DOCS_PATH", "./data/docs")
        if not os.path.isdir(docs_path):
            raise FileNotFoundError(
                f"LlamaIndexAgent: document directory not found at '{docs_path}'. "
                f"Set LLAMAINDEX_DOCS_PATH to a valid path."
            )

        Settings.llm = LlamaOpenAI(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            api_key=api_key,
        )

        documents = SimpleDirectoryReader(docs_path).load_data()
        index = VectorStoreIndex.from_documents(documents)
        self._query_engine = index.as_query_engine(
            similarity_top_k=int(os.getenv("LLAMAINDEX_TOP_K", "3")),
        )

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "")
        response = self._query_engine.query(query)
        sources = [
            {"text": node.get_content(), "score": node.score}
            for node in (response.source_nodes or [])
        ]
        return {"response": str(response), "sources": sources}
