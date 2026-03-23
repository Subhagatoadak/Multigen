"""
Autonomous M&A Due Diligence — Step-by-Step Runner
====================================================

Run this script to execute the full due diligence graph against a
fictional semiconductor company acquisition and see every step live.

Usage:
    # With real OpenAI API (full LLM responses):
    OPENAI_API_KEY=sk-... python -m examples.ma_due_diligence.run

    # Demo mode (structured placeholders, no API key needed):
    python -m examples.ma_due_diligence.run

Prerequisites:
    - Multigen orchestrator running: uvicorn orchestrator.main:app --port 8000
    - Temporal worker running:       python -m workers.temporal_worker
    - MongoDB + Kafka + Temporal up:  docker-compose up -d

What to expect step by step:
    Step 1  DSL is built and posted to /workflows/run → returns instance_id
    Step 2  ingest node runs (DataIngestionAgent)  ~5s
    Step 3  5 expert agents run IN PARALLEL       ~15-40s
    Step 4  Any low-confidence outputs auto-trigger CritiqueAgent reflection
    Step 5  risk_synthesis aggregates all expert outputs
    Step 6  Workflow INTERRUPTS — simulates CFO human review pause
    Step 7  Fan-out: 3 parallel market analysts (bear/base/bull)
    Step 8  Workflow resumes after simulated review approval
    Step 9  valuation runs; if confidence < 0.85 → cycles back to financial
    Step 10 compliance runs (circuit breaker protected)
    Step 11 exec_summary produces board-ready recommendation
    Step 12 Final state read from MongoDB CQRS backend
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, Dict

from multigen.client import MultigenClient
from multigen.models import FanOutNodeDef, FanOutRequest
from examples.ma_due_diligence.workflow import build_ma_graph, build_payload

# Ensure repo root is on path when run with -m
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

# ── Configuration ──────────────────────────────────────────────────────────────

ORCHESTRATOR_URL = os.getenv("MULTIGEN_BASE_URL", "http://localhost:8000")

# Deal parameters
COMPANY_NAME     = "NovaSemi Technologies"
ACQUIRER_NAME    = "GlobalTech Semiconductor Corp"
SECTOR           = "Semiconductor / Fabless IC Design"
REVENUE_USD_M    = 340.0
EBITDA_USD_M     = 78.0
DEAL_RATIONALE   = (
    "Strategic acquisition to gain advanced RISC-V IP portfolio, "
    "expand into automotive-grade chip market, and acquire 180-person "
    "engineering team with deep ML accelerator expertise."
)

DIVIDER = "─" * 72


def _print_step(n: int, title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  STEP {n}: {title}")
    print(DIVIDER)


def _print_result(label: str, data: Any) -> None:
    print(f"\n  ✓ {label}:")
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                print(f"      {k}: {json.dumps(v, default=str)[:120]}")
            else:
                print(f"      {k}: {v}")
    else:
        print(f"      {data}")


def _print_expected(what: str) -> None:
    print(f"\n  📋 Expected: {what}")


async def run_demo(client: MultigenClient) -> None:

    # ── Step 1: Build and launch the workflow ─────────────────────────────────
    _print_step(1, "Build M&A due diligence graph and launch workflow")
    _print_expected(
        "Server accepts the graph DSL and returns an instance_id UUID. "
        "Temporal creates a durable workflow execution visible at http://localhost:8088"
    )

    graph_def = build_ma_graph(COMPANY_NAME, ACQUIRER_NAME, SECTOR)
    payload   = build_payload(
        company_name=COMPANY_NAME,
        acquirer_name=ACQUIRER_NAME,
        sector=SECTOR,
        target_revenue_usd_m=REVENUE_USD_M,
        target_ebitda_usd_m=EBITDA_USD_M,
        deal_rationale=DEAL_RATIONALE,
        raw_data={
            "founded": 2014,
            "employees": 183,
            "hq": "Austin, TX",
            "key_products": ["NovaCore-V RISC-V SoC", "NovaDrive automotive MCU"],
            "ip_patents": 47,
            "last_funding": "Series C $85M (2022)",
            "customers": ["Tier-1 Auto OEM", "Industrial IoT Platform", "Defense Contractor"],
        },
    )

    resp = await client.run_graph(graph_def=graph_def, payload=payload)
    wf_id = resp.instance_id

    _print_result("Workflow launched", {"instance_id": wf_id, "status": "running"})
    print(f"\n  🔗 Temporal UI: http://localhost:8088/namespaces/default/workflows/{wf_id}")
    print(f"  🔗 State API:   {ORCHESTRATOR_URL}/workflows/{wf_id}/state")

    # ── Step 2: Poll until ingest node completes ──────────────────────────────
    _print_step(2, "Wait for DataIngestionAgent (ingest node)")
    _print_expected(
        "DataIngestionAgent normalises the raw company profile. "
        "Returns: company_name, sector, employee_count, revenue_usd_m list, "
        "key_products, data_quality_issues, confidence ~0.85"
    )

    ingest_output = await _wait_for_node(client, wf_id, "ingest", timeout=150)
    _print_result("ingest output", ingest_output)

    # ── Step 3: Parallel expert analysis begins ───────────────────────────────
    _print_step(3, "5 expert agents run IN PARALLEL (financial, legal, technical, market, culture)")
    _print_expected(
        "All five analysis nodes are enqueued simultaneously after ingest completes. "
        "They execute as concurrent Temporal activities. "
        "Each returns a domain-specific analysis dict + confidence score.\n"
        "  financial  → revenue_cagr_3yr, ebitda_margin, dcf_valuation, red_flags\n"
        "  legal      → ip_ownership_clean, litigation_exposure, legal_risk_rating\n"
        "  technical  → tech_stack_score, technical_debt_severity, integration_complexity\n"
        "  market     → tam_usd_b, market_share_pct, competitive_moat, strategic_fit\n"
        "  culture    → cultural_alignment_score, estimated_attrition_pct, integration_difficulty"
    )

    expert_nodes = ["financial", "legal", "technical", "market_primary", "culture"]
    expert_results: Dict[str, Any] = {}

    # Poll all in parallel
    # 5 agents run sequentially in the BFS queue; allow 120s per agent × 5 = 600s
    tasks = [_wait_for_node(client, wf_id, n, timeout=700) for n in expert_nodes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for node, result in zip(expert_nodes, results):
        if isinstance(result, Exception):
            print(f"\n  ⚠️  {node}: {result}")
        else:
            expert_results[node] = result
            conf = result.get("confidence", "?")
            print(f"\n  ✓ {node:<16} confidence={conf}")
            # Show one key metric per domain
            metrics = {
                "financial":     "dcf_valuation_usd_m",
                "legal":         "legal_risk_rating",
                "technical":     "technical_debt_severity",
                "market_primary":"competitive_moat",
                "culture":       "cultural_alignment_score",
            }
            key = metrics.get(node)
            if key and key in result:
                print(f"              {key}={result[key]}")

    # ── Step 4: Reflection loops (automatic, low-confidence nodes) ────────────
    _print_step(4, "Automatic reflection loops for low-confidence outputs")
    _print_expected(
        "Any node whose confidence < threshold auto-injects a CritiqueAgent node "
        "at the FRONT of the queue (priority lane). The critic reviews the output, "
        "returns improved_output + higher confidence. "
        "E.g. if financial.confidence=0.71 (< threshold 0.80): "
        "  → financial__reflect_1 runs CritiqueAgent "
        "  → improved financial output written to context "
        "  → risk_synthesis sees the improved version"
    )

    # Check if any reflection nodes ran
    metrics_now = await client.get_metrics(wf_id)
    print(f"\n  Reflections triggered so far: {metrics_now.reflections_triggered}")
    print(f"  Nodes executed so far:        {metrics_now.nodes_executed}")

    # ── Step 5: Risk synthesis ────────────────────────────────────────────────
    _print_step(5, "RiskSynthesizerAgent aggregates all expert outputs")
    _print_expected(
        "Receives all 5 expert outputs as context refs. "
        "Returns: overall_risk_rating, deal_breakers (list), "
        "top_5_risks with mitigations, recommendation_to_proceed (bool), "
        "conditions_to_proceed, confidence ~0.84"
    )

    risk_output = await _wait_for_node(client, wf_id, "risk_synthesis", timeout=300)
    _print_result("risk_synthesis output", risk_output)

    # ── Step 6: INTERRUPT for human review ───────────────────────────────────
    _print_step(6, "INTERRUPT workflow — simulating CFO human review gate")
    _print_expected(
        "Workflow pauses at the next node boundary (before valuation). "
        "All in-flight activities complete. Workflow enters PAUSED state in Temporal. "
        "get_health will show interrupted=True, pending_count=1 (valuation queued). "
        "In production: CFO reviews risk_synthesis output and approves/rejects. "
        "Here we pause for 3 seconds then resume."
    )

    await client.interrupt(wf_id)
    health = await client.get_health(wf_id)
    _print_result("health after interrupt", {
        "interrupted": health.interrupted,
        "pending_count": health.pending_count,
    })

    # ── Step 7: Fan-out — parallel market analysis (bear/base/bull) ──────────
    _print_step(7, "Fan-out: 3 parallel market analysts (bear / base / bull case)")
    _print_expected(
        "While workflow is paused, inject a parallel market analysis fan-out. "
        "3 MarketAnalysisAgent variants run simultaneously with different assumptions. "
        "Consensus strategy: highest_confidence picks the best result. "
        "Result stored in context under group_id='market_scenarios' — "
        "visible to downstream nodes as {{steps.market_scenarios.output.*}}"
    )

    fan_req = FanOutRequest(
        group_id="market_scenarios",
        consensus="highest_confidence",
        nodes=[
            FanOutNodeDef(id="market_bear",  agent="MarketAnalysisAgent",
                          params={"company_name": COMPANY_NAME, "analysis_variant": "bear_case"}),
            FanOutNodeDef(id="market_base",  agent="MarketAnalysisAgent",
                          params={"company_name": COMPANY_NAME, "analysis_variant": "base_case"}),
            FanOutNodeDef(id="market_bull",  agent="MarketAnalysisAgent",
                          params={"company_name": COMPANY_NAME, "analysis_variant": "bull_case"}),
        ],
    )
    fan_result = await client.fan_out(wf_id, fan_req)
    _print_result("fan_out signal sent", fan_result)

    # Simulate CFO review time
    print("\n  ⏳ Simulating CFO review pause (3 seconds)...")
    await asyncio.sleep(3)
    print("  ✅ CFO approved. Resuming workflow.")

    # ── Step 8: Resume ────────────────────────────────────────────────────────
    _print_step(8, "Resume workflow — valuation phase begins")
    _print_expected(
        "Workflow unpauses. Fan-out group executes (market_bear/base/bull in parallel). "
        "Then valuation node starts with full financial + risk + market context. "
        "If ValuationAgent.confidence < 0.85: graph cycles back to financial node "
        "for deeper analysis (back-edge in the graph). Max 6 total cycles."
    )

    await client.resume(wf_id)
    _print_result("workflow resumed", {"status": "running"})

    # ── Step 9: Valuation (with potential cycle) ──────────────────────────────
    _print_step(9, "ValuationAgent — multi-method valuation with cycle guard")
    _print_expected(
        "4-method valuation: DCF, CCA, precedent transactions, LBO max bid. "
        "Returns: recommended_bid_range_usd_m [low, high], ev_to_ebitda_multiple, "
        "recommended_premium_pct, confidence.\n"
        "If confidence < 0.85: back-edge fires → financial re-runs → valuation re-runs. "
        "Cycle guard limits to max_cycles=6 to prevent infinite loops."
    )

    valuation_output = await _wait_for_node(client, wf_id, "valuation", timeout=400)
    _print_result("valuation output", valuation_output)

    # ── Step 10: Compliance check ─────────────────────────────────────────────
    _print_step(10, "ComplianceAgent — regulatory clearance assessment")
    _print_expected(
        "Assesses: HSR filing, EU merger control (Phase 1 / Phase 2), CFIUS review, "
        "sector-specific approvals (DoD for defense customers). "
        "Circuit breaker: if ComplianceAgent fails 3 times → FallbackComplianceAgent runs. "
        "Fallback returns conservative estimates (18 months, high risk) to unblock pipeline."
    )

    compliance_output = await _wait_for_node(client, wf_id, "compliance", timeout=300)
    _print_result("compliance output", compliance_output)

    # Show circuit breaker status
    health_final = await client.get_health(wf_id)
    print(f"\n  Circuit breaker trips: {health_final.cb_trips_total}")
    if health_final.dead_letters:
        print(f"  Dead letters: {health_final.dead_letters}")

    # ── Step 11: Executive summary ────────────────────────────────────────────
    _print_step(11, "ExecutiveSummaryAgent — board-ready recommendation")
    _print_expected(
        "Synthesises everything into a one-page board memo. "
        "Returns: headline_recommendation (PROCEED / PROCEED_WITH_CONDITIONS / DO_NOT_PROCEED), "
        "deal_rationale, headline_numbers {bid_range, ev/ebitda, expected_irr, payback_years}, "
        "top_3_value_creation_levers, top_3_deal_risks, conditions_for_proceeding, next_steps."
    )

    summary_output = await _wait_for_node(client, wf_id, "exec_summary", timeout=400)
    _print_result("EXECUTIVE SUMMARY", summary_output)

    # ── Step 12: Final state from MongoDB ─────────────────────────────────────
    _print_step(12, "Read full distributed state from MongoDB CQRS backend")
    _print_expected(
        "All node outputs persisted to MongoDB graph_state collection after execution. "
        "Readable without touching Temporal — supports dashboards, audit trails, "
        "downstream analytics, and re-runs."
    )

    state = await client.get_state(wf_id)
    final_metrics = await client.get_metrics(wf_id)

    print(f"\n  Nodes persisted in MongoDB: {state.count}")
    print(f"  Node IDs: {[n.node_id for n in state.nodes]}")

    print(f"\n{'═' * 72}")
    print("  FINAL EXECUTION SUMMARY")
    print(f"{'═' * 72}")
    print(f"  Workflow ID:            {wf_id}")
    print(f"  Company:                {COMPANY_NAME}")
    print(f"  Acquirer:               {ACQUIRER_NAME}")
    print(f"  Nodes executed:         {final_metrics.nodes_executed}")
    print(f"  Reflections triggered:  {final_metrics.reflections_triggered}")
    print(f"  Fan-outs executed:      {final_metrics.fan_outs_executed}")
    print(f"  Circuit breaker trips:  {final_metrics.circuit_breaker_trips}")
    print(f"  Error count:            {final_metrics.error_count}")
    print(f"  Dead letters:           {final_metrics.dead_letter_count}")

    rec = summary_output.get("headline_recommendation", "UNKNOWN")
    bid = summary_output.get("headline_numbers", {}).get("bid_range_usd_m", "?")
    print("\n  ╔══════════════════════════════════════════════════╗")
    print(f"  ║  RECOMMENDATION: {rec:<32}║")
    print(f"  ║  BID RANGE:      ${str(bid):<32}M ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print()


async def _wait_for_node(
    client: MultigenClient,
    workflow_id: str,
    node_id: str,
    timeout: int = 120,
    poll_interval: float = 2.0,
) -> Dict[str, Any]:
    """Poll MongoDB state until node_id appears, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            node = await client.get_node_state(workflow_id, node_id)
            return node.output
        except Exception:
            pass
        await asyncio.sleep(poll_interval)
    raise TimeoutError(f"Node '{node_id}' did not complete within {timeout}s")


async def main() -> None:
    print(f"\n{'═' * 72}")
    print("  MULTIGEN — Autonomous M&A Due Diligence")
    print(f"  Target:   {COMPANY_NAME}")
    print(f"  Acquirer: {ACQUIRER_NAME}")
    print(f"  Sector:   {SECTOR}")
    print(f"  Revenue:  ${REVENUE_USD_M}M   EBITDA: ${EBITDA_USD_M}M")
    print(f"{'═' * 72}")

    demo_mode = not os.getenv("OPENAI_API_KEY")
    if demo_mode:
        print("\n  ⚠️  Running in DEMO MODE (no OPENAI_API_KEY set).")
        print("     Agents return structured placeholders, not real LLM analysis.")
        print("     Set OPENAI_API_KEY=sk-... for full LLM-powered due diligence.\n")

    async with MultigenClient(base_url=ORCHESTRATOR_URL) as client:
        if not await client.ping():
            print(f"\n  ✗ Cannot reach orchestrator at {ORCHESTRATOR_URL}")
            print("    Start it with: uvicorn orchestrator.main:app --port 8000")
            return
        print(f"\n  ✓ Orchestrator reachable at {ORCHESTRATOR_URL}")
        await run_demo(client)


if __name__ == "__main__":
    asyncio.run(main())
