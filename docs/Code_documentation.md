Absolutely—our `agent_registry` makes it trivial to wire up any new agent, including LangChain or LlamaIndex wrappers, in just a few lines:

1. **Create your agent class** (it can live anywhere under `agents/…`):

   ```python
   from orchestrator.services.agent_registry import register_agent
   from agents.base_agent import BaseAgent

   @register_agent("MyCustomLangChain")
   class MyCustomLangChainAgent(BaseAgent):
       def __init__(self, llm, prompt_template):
           super().__init__()
           self.chain = SomeLangChainWrapper(llm, prompt_template)

       async def run(self, params: dict) -> dict:
           # your LangChain logic here
           output = await self.chain.arun(params["text"])
           return {"generated": output}
   ```

2. **On import**, the decorator drops `{"MyCustomLangChain": MyCustomLangChainAgent}` into the internal `_registry` map.

3. **At runtime**, your workflow’s `agent_activity` simply does:

   ```python
   agent = get_agent("MyCustomLangChain")
   result = await agent(params)
   ```

   No additional plumbing is required.

You can do exactly the same for a `LlamaIndexAgent`:

```python
@register_agent("MyLlamaIndex")
class MyLlamaIndexAgent(BaseAgent):
    def __init__(self, index_path):
        super().__init__()
        self.index = load_your_index(index_path)

    async def run(self, params: dict) -> dict:
        docs = self.index.search(params["query"])
        return {"results": docs}
```

---

### Why it’s easy:

- **Decorator-based**: No manual registry calls or JSON manifests—just annotate the class.
- **Dynamic lookup**: `get_agent(name)` handles instantiation, so the workflow code stays generic.
- **Pluggable**: You can bundle agents in separate packages or generate them at runtime via AgentFactory and still register them the same way.


**WORKFLOW AGENTS**
## 1. **Define and Register the Agent**  
In your agent’s Python module (e.g. `agents/echo_agent/echo_agent.py`), you write:

```python
from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent

@register_agent("EchoAgent")
class EchoAgent(BaseAgent):
    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"echo": params}
```

- **Decorator** `@register_agent("EchoAgent")` runs at *import time*.  
- It calls `agent_registry.register_agent("EchoAgent")`, which adds `{"EchoAgent": EchoAgent}` to a central `_registry` dict.

---

## 2. **Agent Registry Holds the Mapping**  
In `orchestrator/services/agent_registry.py` you have:

```python
_registry: Dict[str, Type[BaseAgent]] = {}

def register_agent(name: str):
    def decorator(cls):
        _registry[name] = cls
        return cls
    return decorator

def get_agent(name: str) -> BaseAgent:
    cls = _registry[name]
    return cls()   # instantiate the agent
```

- Every time you decorate a class, the registry remembers it.
- You never have to import your agent manually elsewhere—the registry does it for you.

---

## 3. **Workflow Activity Looks Up the Agent**  
In your Temporal workflow definition (`flow_engine/workflows/sequence.py`):

```python
@activity.defn
async def agent_activity(agent_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    agent = get_agent(agent_name)   # ← here’s where the registry comes in
    return await agent(params)      # calls agent.__call__, which wraps run()
```

- The workflow sends you the string `"EchoAgent"` (or whatever name you registered).  
- `get_agent("EchoAgent")` finds the `EchoAgent` class and returns a new instance.

---

## 4. **Agent Invocation and Logging**  
Your `BaseAgent` implements:

```python
async def __call__(self, params):
    self.logger.info("Starting agent with params: %s", params)
    try:
        result = await self.run(params)       # calls EchoAgent.run()
        self.logger.info("Agent completed successfully: %s", result)
        return result
    except Exception:
        self.logger.exception("Agent '%s' failed", self.__class__.__name__)
        raise
```

- The workflow’s `agent_activity` simply does `await agent(params)`, triggering all the logging and error-handling you built in.

---

## 5. **Result Feeds Back into the Workflow**  
- `agent_activity` returns the JSONable dict back to Temporal.  
- The workflow assembles these into its overall `results` list.  
- On completion, your `handle_message` (or caller) grabs that list and publishes it downstream (via Kafka, HTTP, etc.).

---

### Summary Diagram

```text
Developer code
   └─@register_agent("MyAgent")─> AgentRegistry._registry["MyAgent"] = MyAgentClass

Temporal Activity Invocation
   └─agent_activity("MyAgent", params)
         ↓
   get_agent("MyAgent")   ──> instantiate MyAgentClass()
         ↓
   await agent(params)    ──> BaseAgent.__call__
         ↓
   await MyAgentClass.run(params)  ──> agent logic
         ↓
   return result back to workflow
```




