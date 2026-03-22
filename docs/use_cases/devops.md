# AIOps Incident Response

## Overview

An AI-driven incident response pipeline that automates triage, root cause analysis, and remediation for P1/P2/P3 alerts — with confidence-gated automation (high confidence → auto-remediate, low → human escalate).

## Architecture

```
Alert Payload
      │
  AlertParser (normalise + enrich)
      │
  TriageRouter ─── P3 → create ticket
      │             P2 → standard path
      └─────────── P1 → full path ──────────┐
                                             │
              ┌── Race ──┐     ┌── Parallel ─┤─────────────┐
              │ Heuristic│     │ LogAnalyzer │ MetricAnalyzer│
              │ ML Model │     └──────────┬──┴──────────────┘
              └──first ──┘               │
                                  RootCauseAggregator
                                         │
                                RemediationPlanner
                                         │
                          confidence ≥ 0.75 → AutoRemediate
                          confidence < 0.75 → HumanEscalate
                                         │
                                PostMortemGenerator
```

## Key Agents

| Agent | Role | Data Source |
|-------|------|-------------|
| `AlertParser` | Normalise + enrich alert payload | PagerDuty / OpsGenie webhook |
| `TriageRouter` | Route by severity P1/P2/P3 | Alert `severity` field |
| `LogAnalyzer` | Find error patterns in logs | CloudWatch / Datadog Logs |
| `MetricsAnalyzer` | Detect metric anomalies | Prometheus / VictoriaMetrics |
| `RootCauseAggregator` | Synthesise diagnostic signals | Combined log + metric analysis |
| `RemediationPlanner` | Select playbook steps | Internal runbook library |
| `PostMortemGenerator` | 5-why incident report | All prior agent outputs |

## Race Pattern

```python
def run_race_analysis(ctx):
    heuristic_result = heuristic_agent.run(ctx)   # fast: ~100ms
    ml_result        = ml_agent.run(ctx)            # thorough: ~800ms

    # Race winner: highest confidence (in production: first to finish)
    winner = ml_result if ml_data["confidence"] > h_data["confidence"] else heuristic_result
    return winner
```

The race pattern reduces mean time to root cause (MTTRC) by always having the fast heuristic as a fallback when the ML model is slow.

## Confidence-Gated Automation

```python
CONFIDENCE_GATE = 0.75  # minimum confidence for auto-remediation

if auto_eligible and confidence >= CONFIDENCE_GATE:
    # Auto-remediate: execute kubectl commands, scale deployments
    actions_executed = [s["action"] for s in remediation_steps]
    outcome = "AUTO_REMEDIATED"
else:
    # Human escalation: page on-call engineer with full context
    outcome = "HUMAN_ESCALATED"
```

This ensures the system auto-resolves only when it has high confidence in both the root cause and the remediation plan.

## Typical Outcomes

| Severity | Root Cause Pattern | Typical Outcome |
|----------|-------------------|----------------|
| P1 | Memory leak + OOM | Human escalated (high risk steps) |
| P2 | Slow DB queries | Auto-remediated (enable cache) |
| P3 | Expected batch load | Ticket created (no action needed) |

## Notebook

Full runnable implementation:

[`notebooks/use_cases/uc_03_devops_aiops.ipynb`](../../notebooks/use_cases/uc_03_devops_aiops.ipynb)

Sections:
1. Mock alert payloads (P1/P2/P3)
2. All 7 agent definitions
3. TriageRouter implementation
4. Race pattern demo
5. Full pipeline run
6. Confidence gating
7. Human escalation path
8. Batch alert summary
