"""
Project scaffolding for `multigen init <project_name>`.

Creates a self-contained starter project with:
  agents/         — example agent with full epistemic envelope
  workflows/      — YAML workflow DSL
  tests/          — pytest skeleton
  .env.example    — env vars template
  requirements.txt
  README.md
"""
from __future__ import annotations

from pathlib import Path

import click


# ── Template sources ──────────────────────────────────────────────────────────

def _agent_py(project_name: str) -> str:
    return f'''\
"""
Example agent for {project_name}.

Every agent in Multigen:
  1. Inherits from BaseAgent
  2. Is decorated with @register_agent("<Name>")
  3. Implements async run(params) -> dict
  4. Returns an epistemic envelope alongside its output
"""
from orchestrator.services.agent_registry import register_agent
from orchestrator.agents.base import BaseAgent


@register_agent("MyAgent")
class MyAgent(BaseAgent):
    """Starter agent — replace this with your own logic."""

    async def run(self, params: dict) -> dict:
        # ── Your agent logic here ────────────────────────────────────
        message = params.get("message", "Hello from MyAgent")
        result  = f"Echo: {{message}}"

        # ── Return with epistemic envelope ───────────────────────────
        return {{
            "output": {{
                "result": result,
                "epistemic": {{
                    "confidence": 0.95,
                    "reasoning": "Direct echo — no uncertainty",
                    "uncertainty_sources": [],
                    "assumptions": [],
                    "known_limitations": ["Only echoes input"],
                    "known_unknowns": [],
                    "evidence_quality": "high",
                    "data_completeness": 1.0,
                    "flags": [],
                }},
            }}
        }}
'''


def _workflow_yaml_basic(project_name: str) -> str:
    return f"""\
# {project_name} — basic sequential workflow
# Run with:  multigen run workflows/my_workflow.yaml --payload '{{"message":"hi"}}'

steps:
  - name: greet
    agent: MyAgent
    params:
      message: "{{{{payload.message}}}}"
    retry: 3
    timeout: 30
"""


def _workflow_yaml_graph(project_name: str) -> str:
    return f"""\
# {project_name} — graph workflow with parallel branches
# Run with:  multigen run workflows/my_workflow.yaml --payload '{{"topic":"AI"}}'

nodes:
  - id: research
    agent: MyAgent
    params:
      message: "Research {{{{topic}}}}"
    timeout: 60

  - id: analysis
    agent: MyAgent
    params:
      message: "Analyse {{{{steps.research.output.result}}}}"
    depends_on: [research]
    reflection_threshold: 0.75
    max_reflections: 2
    timeout: 60

  - id: report
    agent: MyAgent
    params:
      message: "Summarise for {{{{topic}}}}"
    timeout: 30

edges:
  - source: research
    target: analysis
  - source: analysis
    target: report

entry: research
max_cycles: 10
circuit_breaker:
  trip_threshold: 3
  recovery_executions: 5
"""


def _test_py(project_name: str) -> str:
    return f'''\
"""
Unit tests for {project_name} agents.

Run with:  pytest tests/
"""
import asyncio
import pytest
from agents.my_agent import MyAgent


@pytest.fixture
def agent():
    return MyAgent()


def test_my_agent_echo(agent):
    result = asyncio.run(agent.run({{"message": "hello"}}))
    assert "output" in result
    assert "Echo: hello" in result["output"]["result"]


def test_my_agent_confidence(agent):
    result = asyncio.run(agent.run({{"message": "test"}}))
    ep = result["output"]["epistemic"]
    assert 0.0 <= ep["confidence"] <= 1.0
    assert isinstance(ep["known_unknowns"], list)


def test_my_agent_missing_param(agent):
    """Agent should handle missing params gracefully."""
    result = asyncio.run(agent.run({{}}))
    assert "output" in result
'''


def _env_example() -> str:
    return """\
# Multigen environment variables
# Copy to .env and fill in values

MULTIGEN_BASE_URL=http://localhost:8000
TEMPORAL_SERVER_URL=localhost:7233
TEMPORAL_TASK_QUEUE=flow-task-queue
TEMPORAL_TASK_QUEUES=flow-task-queue

MONGODB_URI=mongodb://localhost:27017
GRAPH_STATE_DB_NAME=multigen

OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o

# For local execution mode (no Temporal required)
MULTIGEN_LOCAL_MODE=false
"""


def _requirements_txt() -> str:
    return """\
# Multigen project dependencies
multigen @ git+https://github.com/Subhagatoadak/Multigen.git

# Workflow DSL parsing
pyyaml>=6.0

# Testing
pytest>=8.0
pytest-asyncio>=0.23

# Optional: hot reload
watchdog>=4.0
"""


def _readme(project_name: str, template: str) -> str:
    return f"""\
# {project_name}

A Multigen agent workflow project.

## Getting started

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY

# Run a workflow (local mode — no Temporal required)
multigen run workflows/my_workflow.yaml --payload '{{"message": "hello"}}' --local

# Run against a live Multigen stack
multigen run workflows/my_workflow.yaml --payload '{{"message": "hello"}}'

# Watch events in real time
multigen run workflows/my_workflow.yaml --watch
```

## Project structure

```
{project_name}/
├── agents/
│   └── my_agent.py         # Add your agents here
├── workflows/
│   └── my_workflow.yaml    # Workflow DSL
├── tests/
│   └── test_my_agent.py    # Agent unit tests
├── .env.example
├── requirements.txt
└── README.md
```

## Running tests

```bash
pytest tests/ -v
```

## Template: `{template}`

Generated by `multigen init {project_name} --template {template}`.
"""


# ── Scaffolding entry point ───────────────────────────────────────────────────

def scaffold_project(project_name: str, template: str) -> None:
    root = Path(project_name)

    if root.exists():
        click.echo(
            click.style(f"Directory '{project_name}' already exists.", fg="red"),
            err=True,
        )
        raise SystemExit(1)

    dirs = [
        root / "agents",
        root / "workflows",
        root / "tests",
    ]
    for d in dirs:
        d.mkdir(parents=True)

    # agents/__init__.py
    (root / "agents" / "__init__.py").write_text("")

    # agents/my_agent.py
    (root / "agents" / "my_agent.py").write_text(_agent_py(project_name))

    # workflows/my_workflow.yaml
    wf = _workflow_yaml_graph(project_name) if template == "graph" else _workflow_yaml_basic(project_name)
    (root / "workflows" / "my_workflow.yaml").write_text(wf)

    # tests/
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_my_agent.py").write_text(_test_py(project_name))

    # Root files
    (root / ".env.example").write_text(_env_example())
    (root / "requirements.txt").write_text(_requirements_txt())
    (root / "README.md").write_text(_readme(project_name, template))

    click.echo(click.style(f"\n✓ Created project '{project_name}'\n", fg="green", bold=True))
    click.echo("  Directory layout:")
    for d in dirs:
        click.echo(f"    {d}/")
    click.echo()
    click.echo("  Next steps:")
    click.echo(f"    cd {project_name}")
    click.echo("    cp .env.example .env  # add OPENAI_API_KEY")
    click.echo("    pip install -r requirements.txt")
    click.echo("    multigen run workflows/my_workflow.yaml --local")
    click.echo()
