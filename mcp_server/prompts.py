"""
MCP Prompt templates — help the host model design Multigen workflows.

Prompts are returned as pre-filled conversation turns that guide Claude
through building a graph definition, choosing agents, and calling tools.
"""
from __future__ import annotations

import mcp.types as types

PROMPT_REGISTRY = [
    {
        "name": "design_graph_workflow",
        "description": (
            "Interactively design a reasoning graph for a complex multi-step task. "
            "Guides through node definition, edge wiring, reflection thresholds, and fan-out."
        ),
        "arguments": [
            types.PromptArgument(
                name="task_description",
                description="What the graph should accomplish",
                required=True,
            ),
            types.PromptArgument(
                name="quality_requirement",
                description="How rigorous the output must be (low/medium/high)",
                required=False,
            ),
        ],
    },
    {
        "name": "explain_workflow_state",
        "description": "Summarise and explain the current state and outputs of a running workflow.",
        "arguments": [
            types.PromptArgument(name="workflow_id", description="Workflow instance ID", required=True),
        ],
    },
    {
        "name": "diagnose_workflow",
        "description": "Diagnose why a workflow is slow, erroring, or stuck. Suggests corrective signals.",
        "arguments": [
            types.PromptArgument(name="workflow_id", description="Workflow instance ID", required=True),
        ],
    },
]


def render_prompt(name: str, args: dict[str, str]) -> list[types.PromptMessage]:
    if name == "design_graph_workflow":
        task = args.get("task_description", "complex reasoning task")
        quality = args.get("quality_requirement", "high")
        return [
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=(
                        f"I need to design a Multigen reasoning graph for: **{task}**\n\n"
                        f"Quality requirement: **{quality}**\n\n"
                        "Please help me:\n"
                        "1. Identify the agent nodes needed (name, purpose, agent class)\n"
                        "2. Design the edges (including any back-edges for cycles)\n"
                        "3. Set reflection_threshold for nodes where quality matters\n"
                        "4. Identify which nodes need fallback_agent for resilience\n"
                        "5. Identify opportunities for fan_out (parallel hypothesis testing)\n"
                        "6. Recommend circuit_breaker settings\n"
                        "7. Build the final graph_def JSON and call multigen_run_graph"
                    ),
                ),
            )
        ]

    if name == "explain_workflow_state":
        wf_id = args.get("workflow_id", "")
        return [
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=(
                        f"Please explain the current state of workflow `{wf_id}`.\n\n"
                        "Use multigen_get_state to read all node outputs, "
                        "multigen_get_metrics for execution stats, and "
                        "multigen_get_health for any errors or circuit breaker issues.\n"
                        "Then give a plain-English summary of:\n"
                        "- What has completed and what the outputs mean\n"
                        "- What is currently running or pending\n"
                        "- Any errors, dead letters, or CB trips\n"
                        "- Overall quality/confidence of completed nodes"
                    ),
                ),
            )
        ]

    if name == "diagnose_workflow":
        wf_id = args.get("workflow_id", "")
        return [
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=(
                        f"Workflow `{wf_id}` appears to have a problem. Please diagnose it.\n\n"
                        "Steps:\n"
                        "1. Call multigen_get_health — check interrupted, dead_letters, recent_errors\n"
                        "2. Call multigen_get_metrics — check circuit_breaker_trips, error_count\n"
                        "3. Call multigen_get_state — see which nodes completed vs are missing\n"
                        "4. Based on findings, suggest and apply corrective actions:\n"
                        "   - If interrupted: call multigen_resume\n"
                        "   - If a node is in dead_letters: call multigen_inject_node with a fixed version\n"
                        "   - If stuck in a loop: call multigen_skip_node or multigen_prune_branch\n"
                        "   - If a bad branch is running: call multigen_reroute to redirect\n"
                        "   - If a node needs urgent attention: call multigen_jump_to"
                    ),
                ),
            )
        ]

    return [
        types.PromptMessage(
            role="user",
            content=types.TextContent(type="text", text=f"Unknown prompt: {name}"),
        )
    ]
