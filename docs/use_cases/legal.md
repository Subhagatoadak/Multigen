# Legal Document Analysis

## Overview

An automated legal document review pipeline that extracts clauses, classifies risk, checks jurisdiction enforceability, searches for adverse precedents, and produces a compliance report — all subject to **mandatory lawyer review** before any legal conclusions are acted upon.

> **DISCLAIMER**: This is a software architecture demonstration only. It does not constitute legal advice. Any production legal AI system must be reviewed and supervised by qualified legal professionals.

## Architecture

```
DocumentParser
      │
  MapReduce: ClauseExtractor × N clauses (parallel)
      │
  RiskClassifier (reducer: aggregates all clause risks)
      │
  ┌───┴───────────────┐  parallel
  ▼                   ▼
JurisdictionChecker  PrecedentSearcher
  └───────┬───────────┘
          │
  StateMachine: review loop (confidence ≥ 0.85, max 3 iterations)
          │
  EpisodicMemory: accumulate critique context
          │
  ┌────────────────────────────┐
  │  MANDATORY LAWYER REVIEW   │
  └────────────────────────────┘
          │
  ComplianceReporter (final report)
```

## Risk Classification

```python
RISK_SCORE_MAP = {"CRITICAL": 1.0, "HIGH": 0.75, "MEDIUM": 0.45, "LOW": 0.15}

# Example: 5-clause SaaS agreement
clause_risks = {
    "CL-001 Limitation of Liability":        "HIGH",     # 3-month cap
    "CL-002 Data Processing & Privacy":      "LOW",      # standard SOC2 language
    "CL-003 IP Assignment":                  "CRITICAL", # perpetual/irrevocable grant
    "CL-004 Auto-Renewal & Termination":     "MEDIUM",   # 90-day notice
    "CL-005 Governing Law & Dispute":        "HIGH",     # arbitration waiver
}
```

## Key Agents

| Agent | Role | Real Implementation |
|-------|------|---------------------|
| `DocumentParser` | Extract text + identify clause boundaries | Azure Document Intelligence / Unstructured.io |
| `ClauseExtractor` | Annotate one clause with risk indicators | GPT-4o structured output schema |
| `RiskClassifier` | Aggregate per-clause risks | Weighted scoring model |
| `JurisdictionChecker` | Check enforceability by jurisdiction | Legal knowledge base / LLM |
| `PrecedentSearcher` | Find relevant case law | Westlaw / LexisNexis API |
| `ComplianceReporter` | Final structured compliance report | GPT-4o report template |

## MapReduce Clause Extraction

```python
# Each clause is a "shard" — the mapper annotates it, the reducer aggregates
pipeline = MapReduceCoordinator(
    mappers=[clause_extractor] * 4,  # 4 parallel extractors
    reducer=risk_classifier,
)
result = pipeline.run(goal="Extract clauses", inputs={"shards": clause_ids})
```

## Iterative Review State Machine

```python
for iteration in range(max_iterations):
    confidence = aggregate_confidence(risk_class, jurisdiction, precedents)
    memory[f"iter_{iteration}"] = {"confidence": confidence}

    if confidence >= 0.85:
        break  # ready for lawyer review
```

## Notebook

Full runnable implementation:

[`notebooks/use_cases/uc_05_legal_document_analysis.ipynb`](../../notebooks/use_cases/uc_05_legal_document_analysis.ipynb)

Sections:
1. Mock SaaS contract (5 clauses of varying risk)
2. All 6 agent definitions
3. MapReduce clause extraction
4. Parallel jurisdiction + precedent search
5. Iterative review state machine
6. Mandatory lawyer gate
7. Final compliance report with recommendations
8. Complete audit trail
