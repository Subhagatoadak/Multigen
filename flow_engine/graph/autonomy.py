"""
Dynamic Agent Autonomy — Self-healing with Human Approval.

Lifecycle
─────────
1. GraphWorkflow encounters a node whose agent is NOT in the registry.
2. TaskDecomposer generates an AgentSpec (purpose, system_prompt, I/O schemas,
   rationale, estimated confidence range, known limitations).
3. Workflow PAUSES → pushes spec to _pending_approvals queue.
4. Human reviews via  GET  /workflows/{id}/pending-approvals
5. Human decides  via  POST /workflows/{id}/approve-agent
                        POST /workflows/{id}/reject-agent
6. On approval  → create_agent_from_blueprint registers a live BlueprintAgent.
7. Node executes with the newly minted agent.
8. Agent name stored in _dynamic_agents for post-run cleanup.
9. Workflow completion → cleanup_dynamic_agents() deregisters all dynamic agents
   so the registry stays clean between runs.

Epistemic contract
──────────────────
Every AgentSpec includes:
  - capability_description : what it can and cannot do
  - uncertainty_floor      : minimum expected uncertainty (0.0–1.0)
  - required_inputs        : keys it needs in params
  - output_schema          : keys it promises to return
  - known_limitations      : honest list of edge cases it can't handle

This spec is shown verbatim to the approving human so they can make an
informed decision — the core of Multigen's epistemic transparency pledge.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Agent spec schema ──────────────────────────────────────────────────────────

def make_agent_spec(
    agent_name: str,
    task_context: str,
    node_params: Dict[str, Any],
    prior_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate an AgentSpec for human review.

    In production this calls an LLM to write a precise spec.
    Falls back to a structured template when no API key is available.
    """
    _api_key = os.getenv("OPENAI_API_KEY", "")
    if _api_key:
        return _make_spec_via_llm(agent_name, task_context, node_params, prior_context)
    return _make_spec_template(agent_name, task_context, node_params)


def _make_spec_via_llm(
    agent_name: str,
    task_context: str,
    node_params: Dict[str, Any],
    prior_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Call GPT-4o to generate a precise, honest agent spec."""
    try:
        import asyncio
        from openai import OpenAI  # sync client — called from activity context

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        prompt = f"""You are designing a specialist AI agent for a multi-agent workflow.

Agent name requested: {agent_name}
Task context: {task_context}
Input parameters available: {json.dumps(node_params, default=str)[:800]}
Upstream context keys: {list(prior_context.keys())}

Return a JSON object (no markdown) with exactly these fields:
{{
  "agent_name": "{agent_name}",
  "capability_description": "one paragraph: what this agent does",
  "system_prompt": "the system prompt to give this agent (200-400 words)",
  "required_inputs": ["list", "of", "param", "keys"],
  "output_schema": {{"key": "description", ...}},
  "uncertainty_floor": 0.0,
  "known_limitations": ["honest list of edge cases it cannot handle"],
  "estimated_confidence_range": [0.0, 1.0],
  "rationale": "why this agent is the right tool for this task",
  "fallback_strategy": "what to do if this agent fails"
}}

Be honest about limitations. Overconfident specs will be rejected."""

        response = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        spec = json.loads(response.choices[0].message.content)
        spec["generated_by"] = "llm"
        spec["status"] = "pending_approval"
        return spec
    except Exception as exc:
        logger.warning("LLM spec generation failed, using template: %s", exc)
        return _make_spec_template(agent_name, task_context, node_params)


def _make_spec_template(
    agent_name: str,
    task_context: str,
    node_params: Dict[str, Any],
) -> Dict[str, Any]:
    """Structured template fallback (demo mode / no API key)."""
    domain = _infer_domain(agent_name)
    return {
        "agent_name": agent_name,
        "capability_description": (
            f"A {domain} specialist agent that processes inputs related to "
            f"'{task_context}' and returns structured analysis with confidence scoring."
        ),
        "system_prompt": (
            f"You are a {domain} expert operating within a multi-agent orchestration "
            f"system. Your task: {task_context}. "
            f"Always return valid JSON with a 'confidence' field (0.0–1.0) and an "
            f"'epistemic' field containing: reasoning, uncertainty_sources, assumptions, "
            f"known_limitations, and evidence_quality. "
            f"Be conservative with confidence. Flag anything you are unsure about."
        ),
        "required_inputs": list(node_params.keys()),
        "output_schema": {
            "result": "primary output of the analysis",
            "confidence": "float 0.0-1.0",
            "epistemic": "epistemic transparency metadata dict",
        },
        "uncertainty_floor": 0.15,
        "known_limitations": [
            "operates without real-time data access",
            "cannot verify facts against live databases",
            "confidence calibration may be imperfect on novel domains",
        ],
        "estimated_confidence_range": [0.55, 0.90],
        "rationale": f"No pre-registered agent found for '{agent_name}'. "
                     f"Dynamic agent created to handle: {task_context}",
        "fallback_strategy": "Return partial result with low confidence and flag for human review.",
        "generated_by": "template",
        "status": "pending_approval",
    }


def _infer_domain(agent_name: str) -> str:
    """Guess a domain label from the agent name for the template prompt."""
    name_lower = agent_name.lower()
    mapping = {
        "financial": "financial analysis",
        "legal": "legal due diligence",
        "risk": "risk assessment",
        "market": "market research",
        "tech": "technical evaluation",
        "data": "data engineering",
        "model": "statistical modeling",
        "forecast": "forecasting",
        "report": "report writing",
        "optim": "mathematical optimization",
        "insight": "strategic analysis",
        "compliance": "regulatory compliance",
        "valuat": "valuation",
    }
    for key, domain in mapping.items():
        if key in name_lower:
            return domain
    return "general reasoning"


# ── Blueprint builder ──────────────────────────────────────────────────────────

def spec_to_blueprint(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an approved AgentSpec into a BlueprintAgent blueprint dict."""
    return {
        "system_prompt": spec["system_prompt"],
        "capability_description": spec["capability_description"],
        "output_schema": spec.get("output_schema", {}),
        "known_limitations": spec.get("known_limitations", []),
        "uncertainty_floor": spec.get("uncertainty_floor", 0.1),
    }


# ── Registry cleanup ───────────────────────────────────────────────────────────

def deregister_agent(agent_name: str) -> None:
    """
    Remove a dynamically created agent from the registry.
    Safe to call even if the agent was never registered.
    """
    try:
        from orchestrator.services.agent_registry import _registry
        removed = _registry.pop(agent_name, None)
        if removed:
            logger.info("Dynamic agent '%s' deregistered (lifecycle complete)", agent_name)
        else:
            logger.debug("deregister_agent: '%s' was not in registry", agent_name)
    except Exception as exc:
        logger.warning("deregister_agent failed for '%s': %s", agent_name, exc)


def cleanup_dynamic_agents(agent_names: List[str]) -> Dict[str, bool]:
    """
    Deregister all dynamic agents created during a workflow run.
    Returns a dict of {agent_name: success}.
    """
    results: Dict[str, bool] = {}
    for name in agent_names:
        try:
            deregister_agent(name)
            results[name] = True
        except Exception:
            results[name] = False
    if agent_names:
        logger.info(
            "Dynamic agent cleanup: %d deregistered, %d failed",
            sum(results.values()),
            sum(1 for v in results.values() if not v),
        )
    return results


# ── Agent availability check ───────────────────────────────────────────────────

def is_agent_registered(agent_name: str) -> bool:
    """Check if an agent name is in the live registry."""
    try:
        from orchestrator.services.agent_registry import _registry
        return agent_name in _registry
    except Exception:
        return False
