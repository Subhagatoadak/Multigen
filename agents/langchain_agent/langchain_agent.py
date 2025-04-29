from typing import Any, Dict
from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent

# Hypothetical LlamaIndex imports
from llama_index import GPTVectorStoreIndex, SimpleDirectoryReader

@register_agent("LlamaIndexAgent")
class LlamaIndexAgent(BaseAgent):
    """
    Uses LlamaIndex to perform vector-based retrieval over a document corpus.
    """
    def __init__(self) -> None:
        super().__init__()
        # Build or load index
        documents = SimpleDirectoryReader("./data/docs").load_data()
        self.index = GPTVectorStoreIndex.from_documents(documents)

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "")
        results = self.index.similarity_search(query)
        # Return top-k results
        top_k = params.get("top_k", 5)
        snippets = [str(doc) for doc in results[:top_k]]
        return {"snippets": snippets}