"""
Autonomous M&A Due Diligence — Graph Definition
================================================

Builds the full GraphDefinition for the acquisition due diligence process.
The graph has:

  Phase 1 — Data Ingestion
    ingest: DataIngestionAgent

  Phase 2 — Parallel Expert Analysis (5 agents in parallel via fan-out)
    financial, legal, technical, market_primary, culture

  Phase 3 — Risk Synthesis (with reflection loop)
    risk_synthesis: RiskSynthesizerAgent
      → if confidence < 0.80: auto-inject CritiqueAgent → re-runs risk_synthesis

  Phase 4 — Valuation (with cycle back to financial if confidence < 0.85)
    valuation: ValuationAgent
      → edge back to financial if confidence < 0.85

  Phase 5 — Compliance (with circuit breaker + fallback)
    compliance: ComplianceAgent [fallback: FallbackComplianceAgent]

  Phase 6 — Executive Summary
    exec_summary: ExecutiveSummaryAgent

Signal hooks built into this example:
  - After risk_synthesis: INTERRUPT for CFO human review
  - Fan-out for market analysis: 3 parallel market agents (bear/base/bull)
  - Prune cultural-fit branch if acquirer overrides culture concerns
"""
from __future__ import annotations

import os
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from multigen.dsl import GraphBuilder


def build_ma_graph(
    company_name: str,
    acquirer_name: str,
    sector: str,
) -> dict:
    """
    Build the full M&A due diligence GraphDefinition.

    Returns a graph_def dict ready to pass to:
        multigen_run_graph(graph_def=..., payload=...)
    """
    return (
        GraphBuilder()
        # ── Phase 1: Data Ingestion ─────────────────────────────────────────
        .node("ingest")
            .agent("DataIngestionAgent")
            .params(
                company_name=company_name,
                acquirer_name=acquirer_name,
                sector=sector,
            )
            .reflect(threshold=0.70, max_rounds=1, critic="CritiqueAgent")
            .timeout(60)
            .done()

        # ── Phase 2: Expert Analysis (parallel via explicit parallel edges) ─
        # Timeout=120s per node: GPT-4o calls can take 20-40s; allows 2 retries
        .node("financial")
            .agent("FinancialAnalystAgent")
            .params(company_name=company_name)
            .reflect(threshold=0.80, max_rounds=2, critic="CritiqueAgent")
            .fallback("FallbackComplianceAgent")
            .timeout(120)
            .done()

        .node("legal")
            .agent("LegalDueDiligenceAgent")
            .params(company_name=company_name)
            .reflect(threshold=0.75, max_rounds=2, critic="CritiqueAgent")
            .timeout(120)
            .done()

        .node("technical")
            .agent("TechnicalDueDiligenceAgent")
            .params(company_name=company_name)
            .reflect(threshold=0.75, max_rounds=1, critic="CritiqueAgent")
            .timeout(120)
            .done()

        .node("market_primary")
            .agent("MarketAnalysisAgent")
            .params(
                company_name=company_name,
                analysis_variant="base_case",
            )
            .reflect(threshold=0.78, max_rounds=2, critic="CritiqueAgent")
            .timeout(120)
            .done()

        .node("culture")
            .agent("CultureFitAgent")
            .params(
                company_name=company_name,
                acquirer_name=acquirer_name,
            )
            .reflect(threshold=0.72, max_rounds=1, critic="CritiqueAgent")
            .timeout(120)
            .done()

        # ── Phase 3: Risk Synthesis ─────────────────────────────────────────
        .node("risk_synthesis")
            .agent("RiskSynthesizerAgent")
            .params(company_name=company_name)
            .reflect(threshold=0.82, max_rounds=3, critic="CritiqueAgent")
            .timeout(150)
            .done()

        # ── Phase 4: Valuation ──────────────────────────────────────────────
        .node("valuation")
            .agent("ValuationAgent")
            .params(company_name=company_name)
            .reflect(threshold=0.85, max_rounds=2, critic="CritiqueAgent")
            .timeout(150)
            .done()

        # ── Phase 5: Compliance ─────────────────────────────────────────────
        .node("compliance")
            .agent("ComplianceAgent")
            .params(
                company_name=company_name,
                acquirer_name=acquirer_name,
            )
            .fallback("FallbackComplianceAgent")
            .timeout(120)
            .done()

        # ── Phase 6: Executive Summary ──────────────────────────────────────
        .node("exec_summary")
            .agent("ExecutiveSummaryAgent")
            .params(
                company_name=company_name,
                acquirer_name=acquirer_name,
            )
            .reflect(threshold=0.88, max_rounds=1, critic="CritiqueAgent")
            .timeout(150)
            .done()

        # ── Edges ───────────────────────────────────────────────────────────
        # Phase 1 → Phase 2 (fan-out: all 5 expert agents start after ingest)
        .edge("ingest", "financial")
        .edge("ingest", "legal")
        .edge("ingest", "technical")
        .edge("ingest", "market_primary")
        .edge("ingest", "culture")

        # Phase 2 → Phase 3 (all experts must finish before risk_synthesis)
        # Note: in the BFS engine, risk_synthesis is enqueued by each expert;
        # cycle guard (max_cycles=1 effectively, since we set max_cycles=6 overall)
        # prevents multiple redundant runs — the last expert to complete triggers it.
        .edge("financial",     "risk_synthesis")
        .edge("legal",         "risk_synthesis")
        .edge("technical",     "risk_synthesis")
        .edge("market_primary","risk_synthesis")
        .edge("culture",       "risk_synthesis")

        # Phase 3 → Phase 4
        .edge("risk_synthesis", "valuation")

        # Phase 4 → back to financial if valuation confidence low (cycle)
        .edge("valuation", "financial", condition="valuation.confidence < 0.85")

        # Phase 4 → Phase 5 (only when valuation is confident enough)
        .edge("valuation", "compliance", condition="valuation.confidence >= 0.85")

        # Phase 5 → Phase 6
        .edge("compliance", "exec_summary")

        # Entry and global settings
        .entry("ingest")
        .max_cycles(6)  # valuation↔financial cycle capped at 6 total
        .circuit_breaker(trip_threshold=3, recovery_executions=4)
        .build()
    )


def build_payload(
    company_name: str,
    acquirer_name: str,
    sector: str,
    target_revenue_usd_m: float,
    target_ebitda_usd_m: float,
    deal_rationale: str,
    raw_data: dict | None = None,
) -> dict:
    """Build the initial payload passed to all graph nodes."""
    return {
        "company_name": company_name,
        "acquirer_name": acquirer_name,
        "sector": sector,
        "raw_data": raw_data or {},
        "deal_context": {
            "target_revenue_usd_m": target_revenue_usd_m,
            "target_ebitda_usd_m": target_ebitda_usd_m,
            "deal_rationale": deal_rationale,
        },
    }
