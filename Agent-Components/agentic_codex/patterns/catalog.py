"""Pattern catalog with personas and usage guidance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence


@dataclass(frozen=True)
class PatternDescriptor:
    """Describe an orchestration pattern and when to use it."""

    name: str
    idea: str
    best_for: str
    tradeoffs: str
    personas: Sequence[str]


PatternCatalog = Mapping[str, Sequence[PatternDescriptor]]


CATALOG: Dict[str, List[PatternDescriptor]] = {
    "hierarchical": [
        PatternDescriptor(
            name="Ministry of Experts",
            idea="A lead planner decomposes the goal and allocates sub-problems to an expert council before a cabinet consolidates the result.",
            best_for="Complex, multi-disciplinary tasks where oversight and governance are crucial.",
            tradeoffs="High quality and accountability, but can bottleneck on the minister and cabinet review cycles.",
            personas=[
                "Minister (planner)",
                "Domain Experts (council members)",
                "Cabinet Chair (final reviewer)",
            ],
        ),
        PatternDescriptor(
            name="Manager-Worker",
            idea="A manager agent creates milestones and assigns them to worker agents that deliver concrete artifacts.",
            best_for="Deliverables with verifiable outputs such as reports, code, or analyses with acceptance criteria.",
            tradeoffs="Clear accountability with milestone tracking, but requires good task decomposition.",
            personas=[
                "Manager (supervisor)",
                "Workers (specialist executors)",
                "Reviewer (milestone QA)",
            ],
        ),
        PatternDescriptor(
            name="Judge-Referee Gate",
            idea="An independent judge validates outputs from other agents using rubrics, constraints, or schema checks.",
            best_for="Safety-critical or regulated deliverables that must pass strict validation.",
            tradeoffs="Improves safety but adds extra latency and cost for each verification cycle.",
            personas=[
                "Primary Agent (producer)",
                "Judge (QA owner)",
                "Referee (schema/checklist enforcer)",
            ],
        ),
    ],
    "collective_intelligence": [
        PatternDescriptor(
            name="Agent Swarm",
            idea="Many lightweight agents explore the search space in parallel and converge via heuristics or quorum rules.",
            best_for="Open-ended ideation, divergent research, and loosely structured problem solving.",
            tradeoffs="Broad coverage versus increased compute; requires deduplication and consensus heuristics.",
            personas=[
                "Explorers (parallel scouts)",
                "Swarm Coordinator (synthesizer)",
                "Voter (quorum/arbitration)",
            ],
        ),
        PatternDescriptor(
            name="Blackboard / Stigmergy",
            idea="Agents iteratively read and write to a shared board until the knowledge converges.",
            best_for="Incremental synthesis, multi-source research, and iterative fact gathering.",
            tradeoffs="Simple coordination but requires convergence detection and conflict resolution.",
            personas=[
                "Scribe (board maintainer)",
                "Researchers (fact contributors)",
                "Arbiter (conflict resolver)",
            ],
        ),
        PatternDescriptor(
            name="Debate / Adversarial Pair",
            idea="Opposing agents argue for and against a position while a judge selects or synthesizes the best argument.",
            best_for="Reasoning, red-teaming, policy reviews, and safety evaluations.",
            tradeoffs="Produces higher rigor but doubles token usage and needs a strong judging rubric.",
            personas=[
                "Proponent",
                "Opponent",
                "Judge",
            ],
        ),
    ],
    "market": [
        PatternDescriptor(
            name="Contract Net",
            idea="Tasks are auctioned by a coordinator and agents bid with their capability, cost, or ETA.",
            best_for="Heterogeneous tool ecosystems where availability and cost vary between agents.",
            tradeoffs="Flexible marketplace but needs bidding policies and tie-breaking rules.",
            personas=[
                "Auctioneer (task broker)",
                "Vendors (bidders)",
                "Adjudicator (award agent)",
            ],
        ),
        PatternDescriptor(
            name="Incentive-Aligned Game",
            idea="Agents earn rewards for grounded evidence and incur penalties for hallucinations or breaches.",
            best_for="Long-running, evolving ecosystems where agent quality drifts over time.",
            tradeoffs="Encourages truthful behaviour but requires scoring infrastructure and reputation tracking.",
            personas=[
                "Game Master (scorer)",
                "Participants (contenders)",
                "Auditor (reputation keeper)",
            ],
        ),
    ],
    "graph_mesh": [
        PatternDescriptor(
            name="Agent Mesh",
            idea="Peer agents communicate directly based on a capability graph without a single controller.",
            best_for="Low-latency micro-collaboration and resilient systems that avoid single points of failure.",
            tradeoffs="Scales horizontally but requires robust routing and conflict resolution.",
            personas=[
                "Peers (mesh nodes)",
                "Router (capability graph)",
                "Observer (health monitor)",
            ],
        ),
        PatternDescriptor(
            name="Mixture-of-Experts Committee",
            idea="A router sends prompts to relevant experts, aggregates their votes, and produces a final response.",
            best_for="Narrow but diverse skill coverage where budget caps necessitate targeted expert usage.",
            tradeoffs="Efficient expert use but needs good routing heuristics and ensemble aggregation.",
            personas=[
                "Router",
                "Experts",
                "Committee Chair",
            ],
        ),
    ],
    "pipeline": [
        PatternDescriptor(
            name="Assembly Line",
            idea="Ordered stages handle research, drafting, editing, and QA in sequence.",
            best_for="Deterministic, repeatable business processes with clear stage boundaries.",
            tradeoffs="Predictable but rigid; downstream stages depend on upstream quality.",
            personas=[
                "Researcher",
                "Writer",
                "Editor",
                "QA Lead",
            ],
        ),
        PatternDescriptor(
            name="Map-Reduce",
            idea="Corpus shards are processed in parallel by mappers and then summarized by reducers.",
            best_for="Large literature reviews, ETL pipelines, and dataset synthesis with LLM checks.",
            tradeoffs="High throughput yet requires careful aggregation to avoid hallucinations.",
            personas=[
                "Shard Planner",
                "Mapper Agents",
                "Reducer",
            ],
        ),
        PatternDescriptor(
            name="Branching Flow",
            idea="A DAG coordinator executes conditional, parallel, and batched steps with traceability.",
            best_for="Complex workflows that need optional gates, parallel experiments, or multi-path fallbacks.",
            tradeoffs="Flexible but requires explicit dependency graphs and attention to shared state in parallel paths.",
            personas=[
                "Graph Planner",
                "Parallel Specialists",
                "Trace Observer",
            ],
        ),
        PatternDescriptor(
            name="Streaming Actor",
            idea="Long-lived actors consume and emit events in near real-time based on CEP rules.",
            best_for="Monitoring, incident response, trading, or any streaming inference loop.",
            tradeoffs="Responsive but requires durable state and backpressure handling.",
            personas=[
                "Event Sentinel",
                "Actor",
                "Controller",
            ],
        ),
    ],
    "federation": [
        PatternDescriptor(
            name="Hub-and-Spoke",
            idea="Tenant hubs own local policies and memories while a federation broker coordinates cross-hub collaboration.",
            best_for="Enterprises spanning multiple business units or regions with local compliance needs.",
            tradeoffs="Balances autonomy with federation but requires metadata standardisation.",
            personas=[
                "Federation Broker",
                "Local Hub Leads",
                "Compliance Officer",
            ],
        ),
        PatternDescriptor(
            name="Guilds / Service Pools",
            idea="Capability-aligned guilds expose services and a router dispatches requests based on load and SLOs.",
            best_for="Operational scaling, capacity planning, and modular workforce management.",
            tradeoffs="Improves specialisation but needs dynamic routing and capacity signals.",
            personas=[
                "Guild Master",
                "Guild Members",
                "Router / Scheduler",
            ],
        ),
    ],
    "safety": [
        PatternDescriptor(
            name="Guardrail Sandwich",
            idea="Apply pre-filters like PII scrubbing, execute the agent, then post-validate schemas or toxicity.",
            best_for="Workflows requiring layered safety interventions around each action.",
            tradeoffs="High assurance with layered checks but introduces extra latency.",
            personas=[
                "Pre-Filter Operator",
                "Primary Agent",
                "Post-Validator",
            ],
        ),
        PatternDescriptor(
            name="Two-Person Rule",
            idea="Sensitive actions require two independent approvals (agent-agent or agent-human).",
            best_for="High-stakes decisions such as financial trades, code deploys, or policy updates.",
            tradeoffs="Improves safety yet doubles interaction cost.",
            personas=[
                "Initiator",
                "Co-Signer",
                "Auditor",
            ],
        ),
        PatternDescriptor(
            name="Policy-as-Code Lattice",
            idea="Declarative policies determine routing, model choice, and tool permissions.",
            best_for="Organisations needing strict governance for compliance and auditability.",
            tradeoffs="Highly controllable but demands well-maintained policy rule sets.",
            personas=[
                "Policy Author",
                "Policy Engine",
                "Observer / Auditor",
            ],
        ),
    ],
}


def list_categories() -> Sequence[str]:
    """Return the available pattern categories."""

    return tuple(CATALOG.keys())


def list_patterns(category: str | None = None) -> PatternCatalog | Sequence[PatternDescriptor]:
    """Return patterns for a category or the whole catalog."""

    if category is None:
        return CATALOG
    if category not in CATALOG:
        raise KeyError(f"Unknown pattern category {category!r}")
    return tuple(CATALOG[category])


__all__ = ["PatternDescriptor", "PatternCatalog", "CATALOG", "list_categories", "list_patterns"]
