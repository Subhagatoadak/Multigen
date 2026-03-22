# Clinical Decision Support

## Overview

An AI-assisted clinical decision support system (CDSS) that aggregates multi-source diagnostic signals (labs, imaging, patient history), produces differential diagnoses with epistemic confidence scores, and presents treatment recommendations for **mandatory physician review** before any clinical action is taken.

> **DISCLAIMER**: This is a software architecture demonstration. It does not constitute medical advice. Any clinical AI system must be validated by qualified medical professionals and comply with FDA, HIPAA, and applicable regulations.

## Architecture

```
PatientIntakeProcessor
         │
  ┌──────┼──────┬──────────────────┐
  ▼      ▼      ▼                  ▼
Labs   Imaging  History     [CB: OPEN]
(CLOSED)(CLOSED)(CLOSED)    → confidence degrades
  └──────┼──────┘
         │ MapReduceCoordinator
         ▼
DifferentialDiagnosis (aggregates all signals)
         │
  ─ confidence < 0.85 ─► iterate with more data
         │
  ┌──────────────────────────────┐
  │  MANDATORY PHYSICIAN REVIEW  │  ← HARD GATE: no bypass
  └──────────────────────────────┘
         │ physician approves
         ▼
TreatmentPlanner (recommendations only, not prescriptions)
```

## Safety Design

The system is designed with **safety as the primary constraint**:

1. **Hard human gate** — `TreatmentPlanner` checks `ctx.scratch["human_gate_approved"]` and will not execute if the gate has not been passed.
2. **Confidence transparency** — every agent outputs a `confidence` field. The aggregated confidence is always shown to the physician.
3. **Graceful degradation** — if a data source is unavailable (circuit breaker OPEN), confidence is reduced proportionally rather than the system failing or hiding the missing data.
4. **Audit trail** — every diagnostic step, the physician's approval, and any modifications are logged with timestamps.

## Confidence Degradation Model

```python
# Each unavailable data source reduces confidence
available_sources = sum([labs_available, imaging_available, history_available])
confidence = base_confidence - (3 - available_sources) * 0.12
```

| Sources Available | Expected Confidence |
|------------------|---------------------|
| 3/3 (all)        | ~0.87 (high)         |
| 2/3              | ~0.75 (medium)       |
| 1/3              | ~0.63 (low — physician alerted) |
| 0/3              | 0.00 (blocked)       |

## Key Agents

| Agent | Role | Data Source |
|-------|------|-------------|
| `PatientIntakeProcessor` | Validate + enrich vitals | EHR intake form |
| `LabResultsAnalyzer` | Interpret lab values | LIS / HL7 FHIR labs API |
| `ImagingAnalyzer` | Interpret radiology findings | PACS / radiology report |
| `HistoryReviewer` | Risk factors from PMH | EHR clinical notes |
| `DifferentialDiagnosis` | Rank diagnoses by evidence | Combined all sources |
| `TreatmentPlanner` | Evidence-based suggestions | Clinical knowledge base |

## Notebook

Full runnable implementation:

[`notebooks/use_cases/uc_04_clinical_decision_support.ipynb`](../../notebooks/use_cases/uc_04_clinical_decision_support.ipynb)

Sections:
1. Mock patient data (fictional patient with chest pain)
2. Each agent with circuit breaker
3. Circuit breaker OPEN demonstration + confidence degradation
4. Full pipeline run
5. Mandatory physician review gate
6. Treatment recommendations output
7. Complete audit trail
