# Credit Risk Assessment

## Overview

A complete multi-agent credit risk pipeline that processes loan applications through parallel analysis, weighted aggregation, routing, and MCMC confidence estimation for borderline cases.

## Architecture

```
ApplicationIngestor
       │
       ▼
┌──────────────────────────────────────┐
│          Parallel Analysis           │
│  CreditBureauAgent  (mock/real)      │
│  IncomeVerifier     (mock/real)      │
│  FraudScreener      (mock/real)      │
└────────────────┬─────────────────────┘
                 │ MapReduceCoordinator
                 ▼
          RiskAggregator
          (weighted composite)
                 │
         ┌───────┴───────┐
         ▼               ▼               ▼
      APPROVE         ESCALATE      MANUAL_REVIEW
                 │
       (borderline → MCMC ensemble, 5 chains)
```

## Key Agents

| Agent | Role | External Service (production) |
|-------|------|-------------------------------|
| `ApplicationIngestor` | Validate + normalise application data | Internal document parser |
| `CreditBureauAgent` | Credit score + derogatory marks | Equifax / Experian / TransUnion API |
| `IncomeVerifier` | Income stability + DTI | Plaid / Finicity bank statement API |
| `FraudScreener` | Fraud probability | FICO Falcon / LexisNexis API |
| `RiskAggregator` | Weighted composite score | Internal model |

## Patterns Used

- **`MapReduceCoordinator`**: runs all three analysis agents in parallel
- **`GuardrailSandwich`**: circuit-breaker protection around each external service
- **`GuildRouter`**: routes to approve/escalate/manual_review based on composite score
- **MCMC ensemble** (`chains=5`): consensus for borderline scores (0.35–0.45)
- **Custom state machine**: assess → review → decide with confidence gate

## Risk Score Model

```python
composite_risk = (
    (1.0 - bureau_risk_score) * 0.40 +   # credit history
    income_risk_score         * 0.35 +   # income stability
    fraud_probability         * 0.25     # fraud signal
)

# Decision thresholds
if composite_risk < 0.25:    decision = "APPROVE"
elif composite_risk < 0.45:  decision = "MANUAL_REVIEW"
else:                         decision = "DECLINE"
```

## Quick Code Sketch

```python
from agentic_codex.patterns import MapReduceCoordinator, GuardrailSandwich

parallel_analysis = MapReduceCoordinator(
    mappers=[bureau_agent, income_agent, fraud_agent],
    reducer=aggregator_agent,
)

result = parallel_analysis.run(
    goal="Assess credit application APP-001",
    inputs={"application": applicant_dict, "shards": ["APP-001"]}
)

decision = context.scratch.get("aggregated_result", {}).get("preliminary_decision")
```

## Regulatory Compliance

The pipeline produces a complete audit trail compliant with:
- **ECOA** (Equal Credit Opportunity Act) — decision rationale for every application
- **FCRA** (Fair Credit Reporting Act) — bureau data handling
- **Basel III** — risk model documentation

## Notebook

Full runnable implementation (no API keys required):

[`notebooks/use_cases/uc_01_credit_risk_assessment.ipynb`](../../notebooks/use_cases/uc_01_credit_risk_assessment.ipynb)

The notebook covers:
1. Mock applicant data setup (5 applicants)
2. Individual agent definitions
3. Pipeline assembly
4. Single application run with audit trail
5. Batch processing (5 applications)
6. MCMC ensemble for borderline case
7. State machine iterative review
8. Complete regulatory audit report
