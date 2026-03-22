# Installation

## Requirements

| Dependency | Minimum Version | Notes |
|------------|----------------|-------|
| Python | 3.10+ | 3.11+ recommended |
| pip | 21+ | |
| git | 2.30+ | For source installation |

---

## Standard Install

Install `agentic-codex` — the core component library powering Multigen — from PyPI:

```bash
pip install agentic-codex
```

This installs the framework core: `AgentBuilder`, `Context`, all coordination patterns (`MapReduceCoordinator`, `AssemblyCoordinator`, `SwarmCoordinator`, etc.), `GraphRunner`, memory systems, tool adapters, and the observability stack.

---

## Optional Extras

Install only what you need:

=== "OpenAI"

    Adds the `EnvOpenAIAdapter` and streaming support via the official `openai` SDK.

    ```bash
    pip install "agentic-codex[openai]"
    ```

    Then set your API key:

    ```bash
    export OPENAI_API_KEY="sk-..."
    ```

=== "Temporal"

    Adds the Temporal workflow engine integration for durable, fault-tolerant agent orchestration.

    ```bash
    pip install "agentic-codex[temporal]"
    # Also requires a running Temporal server:
    # docker run --rm -p 7233:7233 temporalio/auto-setup:1.24
    ```

=== "Kafka"

    Adds the `KafkaMessageBus` for high-throughput, persistent inter-agent messaging.

    ```bash
    pip install "agentic-codex[kafka]"
    ```

=== "Vector DBs"

    Adds FAISS and optional external vector DB connectors for `SemanticMemory` and `VectorDBAdapter`.

    ```bash
    pip install "agentic-codex[faiss]"
    # or for GPU-accelerated FAISS:
    pip install "agentic-codex[faiss-gpu]"
    ```

=== "All"

    Install all optional dependencies:

    ```bash
    pip install "agentic-codex[openai,temporal,kafka,faiss]"
    ```

---

## Development Install (From Source)

Clone the repository and install in editable mode with all development dependencies:

```bash
git clone https://github.com/Subhagatoadak/Multigen.git
cd Multigen/Agent-Components

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# Install in editable mode with dev extras
pip install -e ".[openai,temporal,kafka,faiss]"
pip install -r requirements.txt

# Verify installation
python -c "import agentic_codex; print(agentic_codex.__version__)"
```

### Running the Test Suite

```bash
cd Multigen
pytest tests/ -v --tb=short
```

---

## Environment Setup

### Minimal (no external APIs)

No environment variables are required. All patterns work with `FunctionAdapter` (pure Python mock functions). This is the recommended way to develop and test your pipeline logic before connecting to real LLMs.

```python
from agentic_codex import AgentBuilder, Context, FunctionAdapter
from agentic_codex.core.schemas import AgentStep, Message

def my_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content="hello")],
        state_updates={},
        stop=True,
    )

agent = AgentBuilder("my-agent", "assistant").with_step(my_step).build()
ctx = Context(goal="greet")
result = agent.run(ctx)
print(result.out_messages[-1].content)  # "hello"
```

### With OpenAI

```bash
# Required
export OPENAI_API_KEY="sk-..."

# Optional: override default model
export OPENAI_MODEL="gpt-4o"
```

```python
from agentic_codex import AgentBuilder, EnvOpenAIAdapter
from agentic_codex.core.schemas import AgentStep, Message

def llm_step(ctx):
    llm = ctx.llm  # injected by the builder
    response = llm.complete([Message(role="user", content=ctx.goal)])
    return AgentStep(out_messages=[response], state_updates={}, stop=True)

agent = (
    AgentBuilder("gpt-agent", "assistant")
    .with_llm(EnvOpenAIAdapter(model="gpt-4o-mini"))
    .with_step(llm_step)
    .build()
)
```

### With the Agentic Simulator

The simulator provides a web UI for visualising and testing your agent workflows.

```bash
# Start the full stack (simulator + backend)
cd Multigen
docker compose up

# Or start just the backend
cd agentic-simulator/backend
uvicorn main:app --host 0.0.0.0 --port 8009 --reload
```

The simulator UI is available at `http://localhost:3000`.

---

## Verify Installation

Run this snippet to verify all core components are working:

```python
# verify_install.py
from agentic_codex import (
    AgentBuilder, Context,
    EpisodicMemory, MemoryCapability,
    MessageBus,
    RunStore,
)
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.patterns import (
    MapReduceCoordinator,
    SwarmCoordinator,
    AssemblyCoordinator, Stage,
    GraphRunner, GraphNodeSpec,
    GuardrailSandwich,
    MinistryOfExperts,
)

def echo_step(ctx):
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"echo: {ctx.goal}")],
        state_updates={},
        stop=True,
    )

agent = AgentBuilder("test", "tester").with_step(echo_step).build()
ctx = Context(goal="verify")
result = agent.run(ctx)
assert result.out_messages[-1].content == "echo: verify"

print("✓ Core agent: OK")
print("✓ All pattern imports: OK")
print("✓ Memory systems: OK")
print("✓ Observability: OK")
print("\nagentic_codex installation verified successfully.")
```

```bash
python verify_install.py
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'agentic_codex'`

The package is installed as an editable install from the `Agent-Components/` subdirectory. Make sure you're in the correct virtual environment:

```bash
which python  # should point to your venv
pip show agentic-codex  # should show the package info
```

If using the monorepo development install, ensure you ran `pip install -e .` from `Agent-Components/`:

```bash
cd Multigen/Agent-Components
pip install -e .
```

### `ImportError: cannot import name 'X'`

Check you have the latest version:

```bash
pip install --upgrade agentic-codex
```

### OpenAI connection errors

Verify your API key is set and the `openai` extra is installed:

```bash
echo $OPENAI_API_KEY   # should print your key
pip show openai        # should show version >= 1.0
```

For proxy or corporate network setups, set the base URL:

```bash
export OPENAI_BASE_URL="https://your-proxy.example.com/v1"
```

### Temporal connection refused

Make sure the Temporal server is running before starting any workflow:

```bash
docker run --rm -d -p 7233:7233 temporalio/auto-setup:1.24
```

---

## Next Steps

- [Quickstart](quickstart.md) — run your first chain, parallel, and graph in 5 minutes
- [Core Concepts](concepts.md) — understand the framework's mental model
- [Tutorials: Agents](../tutorials/agents.md) — deep dive on agent composition
