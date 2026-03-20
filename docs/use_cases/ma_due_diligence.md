# Autonomous M&A Due Diligence
## A Production Use Case for the Multigen Multi-Agent Orchestration Framework

---

## Table of Contents

1. [Executive Overview](#1-executive-overview)
2. [Business Problem](#2-business-problem)
3. [Solution Architecture](#3-solution-architecture)
4. [System Components](#4-system-components)
5. [Graph Design](#5-graph-design)
6. [Agent Catalogue](#6-agent-catalogue)
7. [Reasoning Mechanisms](#7-reasoning-mechanisms)
8. [Data Flow](#8-data-flow)
9. [Step-by-Step Walkthrough](#9-step-by-step-walkthrough)
10. [Expected Outputs](#10-expected-outputs)
11. [Failure Modes and Recovery](#11-failure-modes-and-recovery)
12. [Observability and Monitoring](#12-observability-and-monitoring)
13. [MCP Integration](#13-mcp-integration)
14. [Deployment Architecture](#14-deployment-architecture)
15. [Performance Characteristics](#15-performance-characteristics)
16. [Extending the Use Case](#16-extending-the-use-case)

---

## 1. Executive Overview

**Use Case:** Autonomous M&A Due Diligence for the acquisition of *NovaSemi Technologies* by *GlobalTech Semiconductor Corp*

**What it demonstrates:**
- A 11-node directed graph with back-edges (cycles) for iterative refinement
- 5 expert agents executing in parallel (saving ~3× wall-clock time)
- Automatic reflection loops when any agent's confidence score falls below threshold
- A fan-out with 3 market analysis variants (bear/base/bull) merged via highest-confidence consensus
- Human-in-the-loop interruption gate after risk synthesis for CFO review
- Circuit breaker protecting the compliance agent with automatic fallback routing
- Real-time state persistence in MongoDB readable without touching Temporal internals
- Full observability via OpenTelemetry spans and Prometheus metrics
- Control via MCP tools from Claude or any MCP-capable host

**Deal parameters:**
| Parameter | Value |
|-----------|-------|
| Target company | NovaSemi Technologies |
| Acquirer | GlobalTech Semiconductor Corp |
| Sector | Semiconductor / Fabless IC Design |
| Target revenue | $340M |
| Target EBITDA | $78M |
| Deal rationale | RISC-V IP portfolio + automotive MCU market + 183-person ML accelerator team |

---

## 2. Business Problem

Traditional M&A due diligence is:

- **Slow:** 4–12 weeks involving 50+ people across legal, financial, technical, and strategy teams
- **Siloed:** Each workstream works in isolation; risk signals don't cross-pollinate until the final week
- **Inconsistent:** Quality depends heavily on individual analyst expertise and availability
- **Expensive:** Tier-1 investment bank fees range $2–15M for a mid-market transaction
- **Error-prone:** Manual aggregation of 200+ page reports introduces synthesis errors

**Target outcome with Multigen:**
| Metric | Traditional | Multigen |
|--------|------------|---------|
| Time to first risk report | 2–3 weeks | 45–90 minutes |
| Consistency | Variable | Deterministic per model version |
| Parallelism | Sequential workstreams | All 5 domains in parallel |
| Re-analysis cost | High (human time) | Near-zero (re-run graph) |
| Audit trail | Email chains + PDFs | Immutable Temporal event history + MongoDB |
| Human touchpoints | 50+ people | 1 CFO gate (configurable) |

---

## 3. Solution Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                                   │
│  Claude (MCP)  ·  Python SDK  ·  REST API  ·  run.py CLI             │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTP POST /workflows/run
┌────────────────────────────▼────────────────────────────────────────┐
│                    ORCHESTRATOR (FastAPI :8000)                        │
│                                                                       │
│  workflow.py router   →  DSL parser  →  capability validator          │
│  graph.py router      →  signal/query proxy to Temporal               │
│                                                                       │
│  Kafka publish: topic=flow-requests                                   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Apache Kafka   │  topic: flow-requests
                    └────────┬────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                  TEMPORAL WORKER (temporal_worker.py)                  │
│                                                                       │
│  Registered workflows:  ComplexSequenceWorkflow, GraphWorkflow        │
│  Registered activities: agent_activity, tool_activity,                │
│                         persist_node_state_activity,                  │
│                         create_agent_activity                         │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│              GRAPH WORKFLOW ENGINE (GraphWorkflow)                     │
│                                                                       │
│  BFS execution queue (deque)                                          │
│  CircuitBreakerRegistry  ·  ReasoningStrategies  ·  GraphTelemetry   │
│                                                                       │
│  Per-node execution:                                                  │
│  1. Interrupt check   2. Prune/skip    3. Circuit breaker             │
│  4. Tool activities   5. Agent activity 6. Confidence score           │
│  7. Reflection inject 8. MongoDB write  9. Edge evaluation            │
└──────┬────────────────────────────────────────────────────┬──────────┘
       │ activity calls                                      │ CQRS write
┌──────▼──────┐                                    ┌────────▼────────┐
│  AGENT POOL  │                                    │    MONGODB       │
│              │                                    │                  │
│ DataIngest   │                                    │ multigen.        │
│ Financial    │                                    │ graph_state      │
│ Legal        │                                    │ (node outputs)   │
│ Technical    │                                    │                  │
│ Market       │                                    │ multigen.        │
│ Culture      │                                    │ capabilities     │
│ RiskSynth    │                                    └─────────────────┘
│ Valuation    │
│ Compliance   │   Each agent calls
│ Critique     │   OpenAI GPT-4o
│ ExecSummary  │   with structured
│              │   JSON output mode
└──────────────┘
```

---

## 4. System Components

### 4.1 Multigen Orchestrator
FastAPI service on port 8000. Accepts workflow DSL, validates agents against the Capability Directory, publishes to Kafka, and proxies graph control signals/queries to Temporal.

### 4.2 Capability Directory Service
FastAPI service on port 8001. Registry of all available agent names, versions, and descriptions. Orchestrator validates every step's `agent` field against this registry before dispatching.

### 4.3 Temporal Workflow Engine
Durable execution engine. Every node execution is an Activity with configurable retry policy. The GraphWorkflow maintains BFS state, circuit breakers, and signal buffers as in-memory workflow instance state — fully recoverable on worker restart.

### 4.4 Kafka
Decouples the REST API from the Temporal worker. The orchestrator publishes to `flow-requests`; the worker consumer starts the Temporal workflow. Dead-letter queue on `flow-dead-letter`.

### 4.5 MongoDB
CQRS read model. After each node completes, `persist_node_state_activity` upserts `{workflow_id, node_id, output, updated_at}` into `multigen.graph_state`. External dashboards, audit systems, and the SDK's `get_state()` read from here without touching Temporal.

### 4.6 MCP Server
Thin adapter layer. Exposes every orchestrator capability as an MCP tool. Claude Code, Claude Desktop, Cursor, and custom agents can start, control, and inspect workflows through natural tool calls.

---

## 5. Graph Design

### 5.1 Node Topology

```
                    ┌─────────┐
                    │  ingest │  DataIngestionAgent
                    └────┬────┘  reflection_threshold=0.70
                         │
         ┌───────────────┼───────────────────────┐
         │               │           │            │            │
    ┌────▼────┐    ┌──────▼────┐  ┌──▼──────┐  ┌─▼────────┐  ┌─▼──────┐
    │financial│    │  legal    │  │technical│  │  market  │  │culture │
    │AnalystA │    │    DD     │  │   DD    │  │ Analysis │  │  Fit   │
    └────┬────┘    └──────┬────┘  └──┬──────┘  └─┬────────┘  └─┬──────┘
         │  reflect↑      │          │             │              │
         │  conf<0.80      │          │             │              │
         └────────────────┴──────────┴─────────────┴──────────────┘
                                        │
                              ┌─────────▼────────┐
                              │  risk_synthesis   │  RiskSynthesizerAgent
                              │  reflect<0.82     │  ← INTERRUPT GATE (CFO)
                              └─────────┬─────────┘
                                        │
                   ┌────────────────────┤
                   │  FAN-OUT           │
                   │  market_bear       │
                   │  market_base  ─────┤  (parallel, consensus=highest_confidence)
                   │  market_bull       │
                   └────────────────────┘
                                        │
                              ┌─────────▼─────────┐
                              │    valuation       │  ValuationAgent
                              │    reflect<0.85    │
                              └─────────┬──────────┘
                                        │
                         ┌──────────────┴──────────────┐
                    conf<0.85                     conf≥0.85
                    (back-edge)               (forward-edge)
                         │                            │
                    ┌────▼────┐              ┌────────▼────────┐
                    │financial│              │   compliance    │  ComplianceAgent
                    │  (re-run│              │  CB: trip=3     │  fallback: FallbackCompliance
                    └─────────┘              └────────┬────────┘
                                                      │
                                            ┌─────────▼──────────┐
                                            │   exec_summary      │  ExecutiveSummaryAgent
                                            │   reflect<0.88      │
                                            └────────────────────┘
```

### 5.2 Edge Conditions

| Source | Target | Condition | Type |
|--------|--------|-----------|------|
| ingest | financial | (none) | Parallel fan-out |
| ingest | legal | (none) | Parallel fan-out |
| ingest | technical | (none) | Parallel fan-out |
| ingest | market_primary | (none) | Parallel fan-out |
| ingest | culture | (none) | Parallel fan-out |
| financial | risk_synthesis | (none) | Convergence |
| legal | risk_synthesis | (none) | Convergence |
| technical | risk_synthesis | (none) | Convergence |
| market_primary | risk_synthesis | (none) | Convergence |
| culture | risk_synthesis | (none) | Convergence |
| risk_synthesis | valuation | (none) | Sequential |
| **valuation** | **financial** | **`valuation.confidence < 0.85`** | **Back-edge (cycle)** |
| valuation | compliance | `valuation.confidence >= 0.85` | Forward (guarded) |
| compliance | exec_summary | (none) | Sequential |

### 5.3 Graph Settings

```python
max_cycles = 6          # valuation↔financial cycle capped at 6 total executions
circuit_breaker:
  trip_threshold       = 3     # 3 consecutive failures → OPEN
  recovery_executions  = 4     # auto-recover after 4 total graph iterations
```

---

## 6. Agent Catalogue

| Agent | Role | LLM Prompt Focus | Output Keys | Reflection Threshold |
|-------|------|-----------------|-------------|---------------------|
| `DataIngestionAgent` | Normalise raw company data | Schema extraction, data quality | company_name, revenue_usd_m, data_quality_issues | 0.70 |
| `FinancialAnalystAgent` | P&L, DCF, capital structure | Investment bank financial analysis | revenue_cagr_3yr, ebitda_margin, dcf_valuation_usd_m, red_flags | 0.80 |
| `LegalDueDiligenceAgent` | Contracts, IP, litigation | M&A legal counsel | ip_ownership_clean, litigation_exposure_usd_m, legal_risk_rating | 0.75 |
| `TechnicalDueDiligenceAgent` | Tech stack, debt, security | CTO-level tech assessment | tech_stack_modernity_score, technical_debt_severity, integration_complexity | 0.75 |
| `MarketAnalysisAgent` | TAM, competition, moat | Strategy consultant market analysis | tam_usd_b, market_share_pct, competitive_moat, strategic_fit_score | 0.78 |
| `CultureFitAgent` | Leadership, retention, alignment | Organisational psychologist | cultural_alignment_score, estimated_attrition_pct, integration_difficulty | 0.72 |
| `RiskSynthesizerAgent` | Master risk register | Chief Risk Officer synthesis | overall_risk_rating, deal_breakers, top_5_risks, recommendation_to_proceed | 0.82 |
| `ValuationAgent` | DCF + CCA + precedent + LBO | Investment banking MD | recommended_bid_range_usd_m, ev_to_ebitda_multiple, recommended_premium_pct | 0.85 |
| `ComplianceAgent` | Antitrust, CFIUS, regulatory | Regulatory affairs partner | hsr_filing_required, eu_phase_2_risk, estimated_clearance_months | — |
| `CritiqueAgent` | Quality critic (all domains) | Senior quality reviewer | critique, identified_gaps, improved_output, confidence | — |
| `ExecutiveSummaryAgent` | Board recommendation memo | Managing Director, M&A | headline_recommendation, deal_rationale, headline_numbers, next_steps | 0.88 |
| `FallbackComplianceAgent` | Conservative compliance fallback | Static conservative estimates | All compliance fields, regulatory_risk_rating=high | — |

---

## 7. Reasoning Mechanisms

### 7.1 Confidence-Based Reflection Loops

Every agent returns a `confidence` field (0.0–1.0). The graph engine checks this after each node:

```
confidence = extract_confidence(output)   ← checks: confidence, score, quality, certainty keys

if confidence < reflection_threshold and reflection_count < max_reflections:
    → inject CritiqueAgent node at FRONT of queue (priority lane)
    → CritiqueAgent receives: {subject: prior_output, instruction: "critique and improve"}
    → CritiqueAgent returns: {critique, identified_gaps, improved_output, confidence}
    → improved output replaces prior in context
    → downstream nodes see the improved version
```

**Example chain for financial analysis:**
```
financial runs → confidence=0.71 (< 0.80 threshold)
  → financial__reflect_1 (CritiqueAgent) runs immediately
  → critique: "DCF assumptions lack working capital detail"
  → improved_output with confidence=0.87
  → risk_synthesis receives financial__reflect_1 output (improved)
```

### 7.2 Fan-Out / Parallel Hypothesis Testing

The bear/base/bull market analysis fan-out:

```
fan_out signal received:
  group_id = "market_scenarios"
  consensus = "highest_confidence"
  nodes = [market_bear, market_base, market_bull]

asyncio.gather runs all 3 in parallel as Temporal activities
  market_bear:  TAM=$12B, growth=3%, moat=weak,     confidence=0.74
  market_base:  TAM=$18B, growth=8%, moat=moderate, confidence=0.88  ← selected
  market_bull:  TAM=$28B, growth=15%, moat=strong,  confidence=0.71

select_consensus(strategy="highest_confidence") → market_base result
context["market_scenarios"] = market_base output
```

This prevents anchoring bias — no single market analyst defines the entire view.

### 7.3 Iterative Valuation Cycle

The back-edge `valuation → financial` implements a financial re-analysis cycle:

```
valuation runs → confidence=0.73 (< 0.85 threshold)
  edge: valuation → financial fires (condition: valuation.confidence < 0.85)
  financial re-runs with updated context (now has risk_synthesis data too)
  financial__reflect_1 may run again if still low confidence
  valuation re-runs → confidence=0.91 ✓
  edge: valuation → compliance fires (condition: valuation.confidence >= 0.85)
```

Cycle guard (`max_cycles=6`) ensures this terminates.

### 7.4 Human-in-the-Loop Gate

After `risk_synthesis` completes:

```python
# Sent via SDK or MCP tool:
await client.interrupt(workflow_id)

# CFO reviews:  GET /workflows/{id}/state/risk_synthesis
# CFO approves: await client.resume(workflow_id)
# CFO rejects:  await client.prune_branch(workflow_id, "valuation")  ← kills all downstream
```

The workflow pauses at the next node boundary. All in-flight Temporal activities complete normally. State is fully preserved — no re-execution of completed nodes on resume.

### 7.5 Circuit Breaker — Compliance Agent

```
ComplianceAgent failure 1 → CB records failure (consecutive=1)
ComplianceAgent failure 2 → CB records failure (consecutive=2)
ComplianceAgent failure 3 → CB TRIPS → state=OPEN
  → no more calls to ComplianceAgent
  → fallback_agent="FallbackComplianceAgent" detected on node
  → FallbackComplianceAgent injected at queue front
  → returns conservative (safe) compliance estimates
  → workflow continues unblocked

After 4 total graph iterations: CB transitions OPEN → HALF_OPEN
  → one probe allowed (ComplianceAgent gets one more try)
  → success: CB closes
  → failure: CB opens again
```

---

## 8. Data Flow

### 8.1 Payload Threading

The initial payload is passed to every node as the default params source:

```python
payload = {
    "company_name": "NovaSemi Technologies",
    "acquirer_name": "GlobalTech Semiconductor Corp",
    "sector": "Semiconductor / Fabless IC Design",
    "raw_data": { "founded": 2014, "employees": 183, ... },
    "deal_context": { "target_revenue_usd_m": 340, ... }
}
```

Nodes that declare explicit `params` override the payload. Nodes without params fall back to the full payload dict.

### 8.2 Context Reference Resolution

Downstream nodes reference upstream outputs using `{{steps.<node>.output.<key>}}` syntax, resolved before each activity dispatch:

```yaml
# risk_synthesis params (resolved automatically by _resolve_refs):
financial_analysis:  "{{steps.financial.output}}"
legal_analysis:      "{{steps.legal.output}}"
technical_analysis:  "{{steps.technical.output}}"
market_analysis:     "{{steps.market_primary.output}}"
culture_analysis:    "{{steps.culture.output}}"
```

After reflection, the context key `financial` is updated with the improved output — risk_synthesis automatically sees the post-reflection version.

### 8.3 MongoDB State Documents

Each node's completion writes:
```json
{
  "workflow_id": "a3f7c82b-...",
  "node_id": "financial",
  "output": {
    "revenue_cagr_3yr": 0.28,
    "ebitda_margin": 0.23,
    "dcf_valuation_usd_m": 485,
    "confidence": 0.87,
    "agent": "FinancialAnalystAgent"
  },
  "updated_at": "2025-03-20T14:32:11.847Z"
}
```

The collection has a compound index on `(workflow_id, node_id)` for O(1) point reads.

---

## 9. Step-by-Step Walkthrough

### Step 1 — Graph Launch (~0s)
```
POST /workflows/run
Body: { "dsl": {"steps": [{"name": "due_diligence", "graph": <graph_def>}]}, "payload": {...} }

Response: { "instance_id": "a3f7c82b-4d1e-4892-b7f6-2c8d9e0f1a2b" }
```

Temporal creates a durable `GraphWorkflow` execution. The entry node `ingest` is placed in the pending deque.

### Step 2 — Data Ingestion (~5–15s)

`DataIngestionAgent` is called as a Temporal activity with the full payload. The LLM (GPT-4o with JSON mode) normalises the raw company data.

**If confidence < 0.70:** `CritiqueAgent` is injected immediately, re-runs data normalisation with stricter instructions.

**Expected output:**
```json
{
  "company_name": "NovaSemi Technologies",
  "sector": "Semiconductor / Fabless IC Design",
  "founded_year": 2014,
  "employee_count": 183,
  "hq_jurisdiction": "Texas, USA",
  "revenue_usd_m": [210, 268, 340],
  "key_products": ["NovaCore-V RISC-V SoC", "NovaDrive automotive MCU"],
  "subsidiaries": [],
  "ip_patents": 47,
  "data_quality_issues": ["No audited financials available for 2023"],
  "confidence": 0.86
}
```

### Step 3 — Parallel Expert Analysis (~15–45s)

All five analysis edges fire simultaneously. Temporal executes them as 5 concurrent activities.

**Expected outputs:**

**financial:**
```json
{
  "revenue_cagr_3yr": 0.27,
  "ebitda_margin": 0.23,
  "gross_margin": 0.61,
  "net_debt_to_ebitda": 0.4,
  "free_cash_flow_yield": 0.12,
  "dcf_valuation_usd_m": 485,
  "key_financial_risks": ["Customer concentration (top 3 = 71% of revenue)", "No audited 2023 financials"],
  "financial_quality_score": 7,
  "red_flags": ["Missing audited financials"],
  "confidence": 0.83
}
```

**legal:**
```json
{
  "ip_ownership_clean": true,
  "material_litigation_exposure_usd_m": 2.5,
  "change_of_control_clauses_count": 4,
  "regulatory_licences_transferable": true,
  "employment_liability_usd_m": 1.2,
  "legal_risk_rating": "medium",
  "blocking_issues": [],
  "confidence": 0.88
}
```

**technical:**
```json
{
  "tech_stack_modernity_score": 8,
  "scalability_rating": 7,
  "technical_debt_severity": "low",
  "security_posture_score": 7,
  "engineering_team_quality": 9,
  "integration_complexity": "medium",
  "estimated_remediation_cost_usd_m": 3.5,
  "key_technical_risks": ["Legacy verification toolchain", "Single cloud region"],
  "confidence": 0.85
}
```

**market_primary:**
```json
{
  "tam_usd_b": 18.4,
  "sam_usd_b": 3.2,
  "market_share_pct": 4.1,
  "market_growth_cagr_5yr": 0.12,
  "competitive_moat": "moderate",
  "top_3_competitors": ["SiFive", "Andes Technology", "Codasip"],
  "strategic_fit_score": 8,
  "market_risk_factors": ["RISC-V commoditisation risk", "China export controls"],
  "confidence": 0.81
}
```

**culture:**
```json
{
  "leadership_quality_score": 8,
  "cultural_alignment_score": 6,
  "key_person_dependency_count": 3,
  "estimated_attrition_post_close_pct": 18,
  "integration_difficulty": "medium",
  "retention_program_cost_usd_m": 12.0,
  "culture_blockers": ["Startup vs enterprise culture gap"],
  "confidence": 0.79
}
```

### Step 4 — Reflection Loops (Automatic)

Culture agent returns confidence=0.79 (< threshold 0.72? No — 0.79 > 0.72, so no reflection).
Market agent returns confidence=0.81 (< threshold 0.78? No — 0.81 > 0.78, so no reflection).

If financial returned confidence=0.71 (hypothetical):
```
financial__reflect_1 (CritiqueAgent) injected at queue front
→ critique: "DCF model needs explicit terminal value and WACC assumptions"
→ improved_output.confidence = 0.87
→ financial context key updated
→ risk_synthesis sees improved financial data
```

Reflections triggered metric increments accordingly.

### Step 5 — Risk Synthesis (~10–20s)

All 5 expert outputs are in context. `RiskSynthesizerAgent` aggregates.

**Expected output:**
```json
{
  "overall_risk_rating": "medium",
  "deal_breakers": [],
  "top_5_risks": [
    {"risk": "Customer concentration (71% HHI)", "severity": "high", "mitigation": "Revenue diversification clause in SPA"},
    {"risk": "18% post-close attrition", "severity": "medium", "mitigation": "$12M retention programme"},
    {"risk": "CFIUS review for defense customer", "severity": "medium", "mitigation": "Engage CFIUS counsel pre-signing"},
    {"risk": "Missing audited 2023 financials", "severity": "medium", "mitigation": "Require Big 4 audit as condition precedent"},
    {"risk": "Legacy verification toolchain", "severity": "low", "mitigation": "Budget $3.5M tech remediation post-close"}
  ],
  "total_risk_adjusted_cost_usd_m": 28.2,
  "recommendation_to_proceed": true,
  "conditions_to_proceed": ["Big 4 audit of FY2023", "CFIUS counsel engagement", "$12M retention programme committed"],
  "confidence": 0.84
}
```

### Step 6 — Human Gate (Interrupt)

Workflow pauses. CFO receives alert with `risk_synthesis` output link.

```
GET /workflows/{id}/health
→ { "interrupted": true, "pending_count": 1, "cb_trips_total": 0, "errors": [] }

GET /workflows/{id}/state/risk_synthesis
→ risk register output above

CFO decision: APPROVED — resume
POST /workflows/{id}/resume
```

### Step 7 — Fan-Out Market Scenarios (~15–30s parallel)

Three `MarketAnalysisAgent` instances run simultaneously with bear/base/bull assumptions. Highest-confidence result wins:

| Variant | TAM | Growth | Moat | Confidence |
|---------|-----|--------|------|-----------|
| Bear case | $12B | 3% | weak | 0.74 |
| **Base case** | $18B | 8% | moderate | **0.88** ← selected |
| Bull case | $28B | 15% | strong | 0.71 |

`context["market_scenarios"]` = base case output.

### Step 8 — Valuation (~10–20s, with potential cycle)

`ValuationAgent` receives: financial + risk_synthesis + market_scenarios.

**Scenario A — High confidence first run:**
```json
{
  "dcf_value_usd_m": 485,
  "cca_value_usd_m": 510,
  "precedent_tx_value_usd_m": 535,
  "lbo_max_bid_usd_m": 460,
  "recommended_bid_range_usd_m": [470, 525],
  "recommended_premium_pct": 35,
  "ev_to_ebitda_multiple": 6.7,
  "valuation_rationale": "Premium justified by IP portfolio and team quality; discount applied for customer concentration risk",
  "confidence": 0.89
}
→ confidence >= 0.85 → proceeds to compliance (no cycle)
```

**Scenario B — Low confidence triggers cycle:**
```
valuation.confidence = 0.71 → fires back-edge to financial
financial re-runs (now with risk_synthesis data in context too)
financial__reflect_2 may fire if still low
valuation re-runs → confidence = 0.91 → proceeds
```

### Step 9 — Compliance (~10–20s)

```json
{
  "hsr_filing_required": true,
  "eu_phase_1_likely": false,
  "eu_phase_2_risk": false,
  "cfius_review_required": true,
  "sector_specific_approvals": ["US DoD foreign ownership notification"],
  "estimated_clearance_months": 9,
  "remedies_likely_required": false,
  "regulatory_risk_rating": "medium",
  "confidence": 0.86
}
```

*If ComplianceAgent fails 3× (circuit breaker trips):*
`FallbackComplianceAgent` returns conservative 18-month estimate with `regulatory_risk_rating=high`.

### Step 10 — Executive Summary (~10–20s)

Board memo generated. Reflection fires if confidence < 0.88.

**Expected output:**
```json
{
  "headline_recommendation": "PROCEED_WITH_CONDITIONS",
  "deal_rationale": "NovaSemi's RISC-V IP portfolio and ML accelerator team directly accelerate GlobalTech's automotive chip strategy. At $470-525M the deal delivers 18-22% IRR at base-case growth. Three conditions must be satisfied before signing.",
  "headline_numbers": {
    "bid_range_usd_m": [470, 525],
    "ev_ebitda": 6.7,
    "expected_irr_pct": 20,
    "payback_years": 5.2
  },
  "top_3_value_creation_levers": [
    "Integration of NovaCore-V into GlobalTech automotive SoC roadmap ($180M NPV)",
    "Cross-selling NovaSemi IP to GlobalTech's Tier-1 OEM relationships",
    "Engineering team retention → accelerated ML accelerator product timeline"
  ],
  "top_3_deal_risks": [
    "Customer concentration — top 3 customers = 71% revenue",
    "CFIUS review timeline could delay close by 6-9 months",
    "18% estimated post-close attrition requiring $12M retention spend"
  ],
  "conditions_for_proceeding": [
    "Big 4 audit of FY2023 financials — condition precedent to signing",
    "Engage CFIUS counsel within 2 weeks",
    "Board-approved $12M retention programme committed at signing"
  ],
  "next_steps": [
    "Engage PwC for FY2023 audit (week 1)",
    "Letter of Intent to NovaSemi board (week 1)",
    "CFIUS pre-filing submission (week 2)",
    "HSR filing preparation (week 2-3)",
    "Final SPA negotiation (weeks 4-8)"
  ],
  "confidence": 0.91
}
```

---

## 10. Expected Outputs

### 10.1 Execution Metrics (Typical Run)

| Metric | Expected Range |
|--------|---------------|
| Total wall-clock time | 45–90 minutes (real LLM) / <30s (demo mode) |
| Nodes executed | 11–18 (depends on reflection count) |
| Reflections triggered | 0–6 (depends on LLM confidence) |
| Fan-outs executed | 1 (market_scenarios) |
| Circuit breaker trips | 0 (normal) / 1–3 (degraded) |
| MongoDB documents written | 11–18 |
| Temporal activities | 11–25 (tools + agents + persist) |

### 10.2 Output Quality Indicators

A high-quality run produces:
- `exec_summary.headline_recommendation` ∈ {PROCEED, PROCEED_WITH_CONDITIONS, DO_NOT_PROCEED}
- `exec_summary.confidence` ≥ 0.88 (or reflection fires to improve it)
- `risk_synthesis.deal_breakers` = [] for a viable deal
- All confidence scores ≥ respective thresholds (post-reflection)
- 0 dead letters

### 10.3 MCP Tool Call Sequence (Claude perspective)

When operated via Claude through the MCP server, the typical tool call sequence is:

```
1. multigen_list_agents()              → confirm all agents registered
2. multigen_run_graph(graph_def, payload) → get instance_id
3. [wait loop] multigen_get_metrics(id) → watch nodes_executed climb
4. multigen_get_node_state(id, "risk_synthesis") → read risk output
5. multigen_interrupt(id)              → pause for review
6. multigen_fan_out(id, market_scenarios) → inject bull/base/bear
7. multigen_resume(id)                 → continue
8. [wait] multigen_get_metrics(id)     → confirm valuation done
9. multigen_get_node_state(id, "exec_summary") → read final recommendation
10. multigen_get_state(id)             → read full state for report
```

---

## 11. Failure Modes and Recovery

### 11.1 LLM API Timeout
**Symptom:** agent_activity raises `openai.Timeout`
**Response:** Temporal retry_policy fires (up to 3 attempts, 1s initial backoff)
**If all retries fail:** Circuit breaker records failure; node goes to dead_letters if no fallback

### 11.2 Invalid JSON from LLM
**Symptom:** `json.JSONDecodeError` in agent's `_llm()` helper
**Response:** Agent returns `{"raw": "<text>", "confidence": 0.5}` — low confidence triggers reflection loop with instruction to format correctly

### 11.3 OpenAI Rate Limit
**Symptom:** HTTP 429 from OpenAI
**Response:** Temporal exponential backoff (configured in `RetryPolicy`). All 5 parallel agents share the same rate limit — stagger with `initial_interval=timedelta(seconds=2)` for production

### 11.4 ComplianceAgent Failure (Circuit Breaker)
**Symptom:** ComplianceAgent throws 3 consecutive exceptions
**Response:** CB trips to OPEN; `FallbackComplianceAgent` injected at queue front; returns conservative estimates; workflow continues

### 11.5 MongoDB Unavailable
**Symptom:** `persist_node_state_activity` fails
**Response:** Non-fatal — logged as warning. Temporal event history remains authoritative source of truth. MongoDB can be replayed from history when service recovers.

### 11.6 Worker Crash Mid-Graph
**Symptom:** Temporal worker process dies
**Response:** Temporal detects lost heartbeat; reassigns activities to another worker. GraphWorkflow instance state is replayed from event history — execution continues from exact failure point. BFS queue, context, and circuit breaker state are reconstructed deterministically.

### 11.7 Low Confidence Loop (Exceeds max_reflections)
**Symptom:** Agent consistently below threshold after max_reflections rounds
**Response:** Reflection budget exhausted; workflow proceeds with best available output; confidence score in final output indicates uncertainty to downstream consumers

---

## 12. Observability and Monitoring

### 12.1 OpenTelemetry Spans

Every node execution produces a span:
```
span: graph.node.financial
  graph.workflow_id = "a3f7c82b-..."
  graph.node_id     = "financial"
  graph.agent       = "FinancialAnalystAgent"
  graph.iteration   = 1
  graph.circuit_state = "closed"
  graph.confidence  = 0.83
  duration_ms       = 8420
```

Configure the OTel exporter via `OTEL_EXPORTER_OTLP_ENDPOINT` for Jaeger, Tempo, or Datadog.

### 12.2 Prometheus Metrics

```
multigen_graph_nodes_total{workflow_id,node_id,status}      # counter
multigen_graph_node_duration_seconds{node_id}               # histogram
multigen_graph_reflections_total{node_id}                   # counter
multigen_graph_circuit_open_total{node_id}                  # counter
multigen_graph_fan_out_total{workflow_id}                   # counter
multigen_graph_errors_total{workflow_id,node_id}            # counter
```

Scrape from the Prometheus metrics endpoint (configured via `METRICS_PORT`).

### 12.3 Live Query API

```bash
# Circuit breaker status + errors
curl http://localhost:8000/workflows/{id}/health | jq

# Execution counters
curl http://localhost:8000/workflows/{id}/metrics | jq

# How many nodes queued
curl http://localhost:8000/workflows/{id}/pending | jq

# All node outputs
curl http://localhost:8000/workflows/{id}/state | jq
```

### 12.4 Temporal UI

Navigate to `http://localhost:8088` to see:
- Workflow execution history (every activity + result)
- Signal delivery timeline (interrupt, resume, fan_out, etc.)
- Child workflow link for nested graph steps
- Replay history for debugging

---

## 13. MCP Integration

### 13.1 Installation

Add to Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "multigen": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/Multigen",
      "env": {
        "MULTIGEN_BASE_URL": "http://localhost:8000"
      }
    }
  }
}
```

### 13.2 Example Claude Conversation

```
User: Run M&A due diligence on NovaSemi Technologies being acquired by GlobalTech.
      Revenue $340M, EBITDA $78M, semiconductor sector. They make RISC-V chips.

Claude: I'll run this through Multigen's autonomous due diligence graph.
        [calls multigen_list_agents() → confirms 11 agents registered]
        [calls multigen_run_graph(graph_def=<built graph>, payload=<deal params>)]
        → Workflow started: instance_id = a3f7c82b-...

        I've launched the 11-node due diligence graph. The 5 expert agents
        (financial, legal, technical, market, culture) are now running in parallel.
        I'll monitor and notify you at key milestones.

        [polls multigen_get_metrics() every 30s]
        [nodes_executed reaches 6]

        The parallel expert analysis is complete. Risk synthesis is running.
        [calls multigen_get_node_state(id, "risk_synthesis")]

        Risk synthesis complete. Overall rating: MEDIUM. No deal breakers found.
        Top risk: customer concentration (71% HHI).
        Shall I pause here for your review before valuation, or continue?

User: Pause it. Also run a bear/base/bull market analysis before we continue.

Claude: [calls multigen_interrupt(id)]
        [calls multigen_fan_out(id, {group_id: "scenarios", nodes: [...]})]
        Workflow paused. Running 3 market scenarios in parallel.
        [retrieves fan-out results]
        Base case selected (confidence 0.88): TAM $18B, 8% CAGR, moderate moat.
        Ready to resume when you are.

User: Resume. What's the final recommendation?

Claude: [calls multigen_resume(id)]
        [waits for exec_summary node]
        [calls multigen_get_node_state(id, "exec_summary")]

        RECOMMENDATION: PROCEED WITH CONDITIONS
        Bid range: $470M–$525M (6.7× EV/EBITDA, 20% expected IRR)

        Three conditions before signing:
        1. Big 4 audit of FY2023 financials
        2. CFIUS counsel engagement within 2 weeks
        3. $12M retention programme committed at signing
```

### 13.3 Available MCP Tools Summary

| Tool | Description |
|------|-------------|
| `multigen_ping` | Health check |
| `multigen_run_workflow` | Start DSL or text workflow |
| `multigen_run_graph` | Start complex reasoning graph |
| `multigen_get_state` | Read all node outputs (MongoDB) |
| `multigen_get_node_state` | Read single node output |
| `multigen_interrupt` | Pause at next node boundary |
| `multigen_resume` | Unpause |
| `multigen_jump_to` | Priority-lane a node |
| `multigen_skip_node` | Branch no-go |
| `multigen_reroute` | Add dynamic edge |
| `multigen_inject_node` | Append node at runtime |
| `multigen_fan_out` | Parallel hypothesis testing |
| `multigen_prune_branch` | Cancel branch + descendants |
| `multigen_get_health` | CB status + errors |
| `multigen_get_metrics` | Execution counters |
| `multigen_list_agents` | Available agents |
| `multigen_list_capabilities` | Capability directory |
| `multigen_register_capability` | Register new agent |

---

## 14. Deployment Architecture

### 14.1 Docker Compose (Local / Dev)

```
docker-compose up -d
```

Services: Zookeeper, Kafka, MongoDB, Temporal (auto-setup), orchestrator, capability_service, temporal_worker.

### 14.2 Production (Kubernetes)

```
┌─────────────────────────────────────────────────────────────────┐
│                        KUBERNETES CLUSTER                          │
│                                                                   │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────┐   │
│  │  orchestrator   │   │ capability_svc   │   │ mcp_server   │   │
│  │  Deployment     │   │  Deployment      │   │ Deployment   │   │
│  │  replicas: 3    │   │  replicas: 2     │   │ replicas: 2  │   │
│  │  HPA: CPU>60%   │   │                  │   │              │   │
│  └─────────────────┘   └──────────────────┘   └──────────────┘   │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │               temporal_worker Deployment                    │  │
│  │               replicas: 5  (scale based on queue depth)    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │  Kafka   │  │ MongoDB  │  │ Temporal │  │  Prometheus +  │  │
│  │ (MSK or  │  │ Atlas or │  │  Cloud   │  │    Grafana     │  │
│  │ Confluent│  │ operator)│  │    or    │  │                │  │
│  │  Cloud)  │  │          │  │ operator │  │                │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 14.3 Key Environment Variables

```bash
# Orchestrator
OPENAI_API_KEY=sk-...
KAFKA_BROKER_URL=kafka:9092
TEMPORAL_SERVER_URL=temporal:7233
MONGODB_URI=mongodb://mongo:27017
CAPABILITY_SERVICE_URL=http://capability-service:8001
ALLOWED_ORIGINS=https://your-app.com
GRAPH_STATE_DB_NAME=multigen
GRAPH_STATE_COLLECTION=graph_state

# Worker
TEMPORAL_SERVER_URL=temporal:7233
TEMPORAL_TASK_QUEUE=flow-task-queue

# MCP Server
MULTIGEN_BASE_URL=http://orchestrator:8000
```

---

## 15. Performance Characteristics

### 15.1 Latency Budget (Real LLM, GPT-4o)

| Phase | Nodes | Parallelism | Latency |
|-------|-------|-------------|---------|
| Data ingestion | 1 | Sequential | 5–15s |
| Expert analysis | 5 | Full parallel | 15–45s |
| Reflection (if triggered) | 1–6 | Sequential | 8–20s each |
| Risk synthesis | 1 | Sequential | 10–20s |
| Fan-out market | 3 | Full parallel | 15–25s |
| Valuation | 1 | Sequential | 10–20s |
| Compliance | 1 | Sequential | 8–15s |
| Executive summary | 1 | Sequential | 12–20s |
| **Total (no reflection)** | | | **~75–160s** |
| **Total (max reflection)** | | | **~180–360s** |

vs. Traditional due diligence: **2–12 weeks**

### 15.2 Scaling

- **More parallel agents:** Add nodes with edges from `ingest`, no code changes
- **Higher throughput:** Scale `temporal_worker` replicas; each handles independent workflow instances
- **Faster LLM:** Use `gpt-4o-mini` for lower-stakes nodes (culture, market variants); `gpt-4o` for financial/legal/exec_summary
- **Caching:** Identical ingestion runs can short-circuit via MongoDB lookup before activity dispatch

---

## 16. Extending the Use Case

### 16.1 Add a Sector-Specialist Agent (Dynamic Blueprint)

For a semiconductor deal, inject a dynamic RISC-V specialist:

```python
await client.inject_node(wf_id, InjectNodeRequest(
    id="risc_v_specialist",
    blueprint={
        "system_prompt": "You are a RISC-V IP licensing expert with 15 years in semiconductor M&A.",
        "instruction": "Assess the RISC-V patent portfolio value and freedom-to-operate risk.",
    },
    edges_to=["risk_synthesis"],
    reflection_threshold=0.80,
))
```

The agent is created at runtime — no deployment needed.

### 16.2 Add a Second Human Gate (Board Vote)

After `exec_summary`:
```python
await client.interrupt(wf_id)          # pause after exec_summary
# Board reviews and votes
await client.reroute(wf_id,            # add dynamic edge based on vote outcome
    source="exec_summary",
    target="term_sheet_drafter",       # new node injected separately
    condition="",
)
await client.resume(wf_id)
```

### 16.3 Multi-Deal Portfolio Screening

Run 10 deals in parallel (independent Temporal workflow instances):

```python
deals = [("CompanyA", ...), ("CompanyB", ...), ...]
tasks = [client.run_workflow(dsl=build_dsl(d), payload=build_payload(d)) for d in deals]
responses = await asyncio.gather(*tasks)
# All 10 graphs run concurrently across the worker pool
```

### 16.4 Continuous Monitoring (Post-Acquisition)

After close, repurpose the graph as a recurring integration health monitor:
- Replace `FinancialAnalystAgent` with `IntegrationProgressAgent`
- Replace `CritiqueAgent` with `MilestoneReviewAgent`
- Add a loop step: `loop(until="integration_score >= 0.95", max_iterations=24)` (monthly runs)

---

*Generated by Multigen v0.2.0 — Enterprise Multi-Agent Orchestration Framework*
