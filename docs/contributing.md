# Contributing to Multigen

Thank you for considering contributing to Multigen! This document covers how to set up your development environment, run tests, and submit pull requests.

---

## Development Setup

### Fork and Clone

```bash
# 1. Fork the repository on GitHub
# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/Multigen.git
cd Multigen

# 3. Add the upstream remote
git remote add upstream https://github.com/Subhagatoadak/Multigen.git
```

### Python Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# Install all dependencies
pip install -e Agent-Components/
pip install -r requirements.txt
pip install -r requirement_dev.txt

# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

### Verify Setup

```bash
pytest tests/ -v --tb=short
# Expected: all tests pass
```

---

## Project Structure

```
Multigen/
├── Agent-Components/          # Core agentic_codex library
│   ├── agentic_codex/
│   │   ├── core/              # Agent, Context, Builder, Memory, Tools
│   │   ├── patterns/          # All coordination patterns
│   │   └── core/orchestration/ # GraphRunner, schedulers, planners
│   └── tests/
├── agents/                    # Framework-level agents
│   ├── base_agent.py
│   └── pattern_agents/        # Pattern agent wrappers
├── flow_engine/               # Temporal workflow integration
├── orchestrator/              # FastAPI orchestrator service
├── agentic-simulator/         # Visual dashboard
├── notebooks/                 # Jupyter notebooks
│   └── use_cases/             # Production use case examples
├── docs/                      # MkDocs documentation source
├── tests/                     # Framework-level tests
└── mkdocs.yml                 # Documentation configuration
```

---

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific module
pytest tests/test_mapreduce.py -v

# With coverage report
pytest tests/ --cov=agents --cov-report=html
open htmlcov/index.html

# Fast (no slow integration tests)
pytest tests/ -m "not slow" -v
```

### Test Conventions

- Unit tests go in `tests/` with filenames matching `test_*.py`
- Every new feature must have at least one unit test
- Tests must pass without any API keys (use `FunctionAdapter`)
- Use `pytest.mark.slow` for tests that take > 5 seconds

```python
# Good: no API key required
def test_mapreduce_basic():
    def mapper_fn(ctx):
        return AgentStep(out_messages=[Message(role="assistant", content="mapped")], state_updates={}, stop=False)

    def reducer_fn(ctx):
        outputs = ctx.scratch.get("mapper_outputs", [])
        return AgentStep(out_messages=[Message(role="assistant", content=f"reduced {len(outputs)}")], state_updates={}, stop=True)

    mapper  = AgentBuilder("m", "mapper").with_step(mapper_fn).build()
    reducer = AgentBuilder("r", "reducer").with_step(reducer_fn).build()

    pipeline = MapReduceCoordinator(mappers=[mapper], reducer=reducer)
    result   = pipeline.run(goal="test", inputs={"shards": ["a", "b", "c"]})
    assert len(result.messages) >= 1
```

---

## Adding a New Coordination Pattern

1. Create the pattern class in `Agent-Components/agentic_codex/patterns/`:

```python
# Agent-Components/agentic_codex/patterns/my_pattern.py

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message
from typing import List, Sequence

class MyPatternCoordinator(CoordinatorBase):
    """
    One-line description.

    Args:
        agents: The agents participating in this pattern.
        guards: Optional safety guard objects.

    Example:
        coordinator = MyPatternCoordinator(agents=[a, b, c])
        result = coordinator.run(goal="...", inputs={})
    """

    def __init__(self, agents: Sequence[Agent], *, guards: Sequence = ()):
        super().__init__(guards=guards)
        if not agents:
            raise ValueError("At least one agent is required")
        self.agents = list(agents)

    def _run(self, context: Context, events: list) -> List[Message]:
        history = []
        for agent in self.agents:
            with self.tracer.span(agent.name, role="participant"):
                result = agent.run(context)
                history.extend(result.out_messages)
                context.scratch.update(result.state_updates)
        events.extend(self.tracer.events)
        return history
```

2. Export from the patterns `__init__.py`:

```python
# Agent-Components/agentic_codex/patterns/__init__.py
from .my_pattern import MyPatternCoordinator
```

3. Export from the top-level `__init__.py`.

4. Add tests in `Agent-Components/tests/test_my_pattern.py`.

5. Add documentation in `docs/api/parallel.md` or a new page.

---

## Adding a Notebook

1. Create the notebook in `notebooks/` or `notebooks/use_cases/`
2. All examples must run without API keys — use `FunctionAdapter`
3. Include a markdown cell at the top explaining: purpose, what you'll learn, architecture diagram
4. Alternate markdown explanation cells with code cells
5. Add a summary/next-steps section at the end
6. Reference the notebook from the relevant documentation page

---

## Documentation

```bash
# Install MkDocs
pip install mkdocs-material

# Serve locally
mkdocs serve

# Build static site
mkdocs build

# Deploy to GitHub Pages
mkdocs gh-deploy --force
```

---

## Pull Request Process

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/my-new-pattern
   ```

2. **Make your changes** — code + tests + docs

3. **Run the full test suite**:
   ```bash
   pytest tests/ -v
   ruff check .
   ```

4. **Commit** with a descriptive message:
   ```bash
   git commit -m "feat(patterns): add MyPatternCoordinator for XYZ use case"
   ```

5. **Push and open a PR**:
   ```bash
   git push origin feature/my-new-pattern
   ```
   Then open a pull request on GitHub.

### PR Checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Linting passes (`ruff check .`)
- [ ] New features have tests
- [ ] New patterns/agents are documented
- [ ] Notebooks work end-to-end without API keys
- [ ] CHANGELOG.md updated (if applicable)

---

## Code Style

- **Python**: follow [PEP 8](https://pep8.org/). Enforced by `ruff`.
- **Docstrings**: Google style for all public classes and methods.
- **Type hints**: required for all public function signatures.
- **Line length**: 120 characters (configured in `ruff.toml`).

---

## Reporting Issues

- Use GitHub Issues for bugs and feature requests.
- For bugs, include: Python version, `agentic-codex` version, minimal reproducible example.
- For features, describe the use case and why existing patterns don't cover it.

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License. See [LICENSE](https://github.com/Subhagatoadak/Multigen/blob/main/LICENSE).
