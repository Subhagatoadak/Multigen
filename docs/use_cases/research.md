# Automated Research Pipeline

## Overview

A multi-stage research synthesis pipeline that gathers sources, summarises and fact-checks in parallel, iteratively improves quality through a critique loop, and produces a structured research report.

**Research topic demonstrated**: "Impact of Large Language Models on Software Development Productivity"

## Architecture

```
TopicExtractor
      │
SourceSearcher (mock: Semantic Scholar / arXiv in production)
      │
   ┌──┴──┐  (MapReduce parallel)
   ▼     ▼
Summarize  FactCheck
   └──┬──┘  (reducer: QualityCritic)
      │
QualityCritic ◄──────────────────────┐
      │                               │
      │ quality < 0.75?               │ revise loop
      ▼                               │
ReportGenerator ─────────────────────┘
```

## Key Agents

| Agent | Role | Real Implementation |
|-------|------|---------------------|
| `TopicExtractor` | Decompose research question into sub-topics | GPT-4o structured output |
| `SourceSearcher` | Retrieve relevant literature | Semantic Scholar API + vector DB |
| `SummarySynthesizer` | Write coherent narrative synthesis | GPT-4o synthesis prompt |
| `FactChecker` | Cross-check claims against sources | GPT-4o with source context |
| `QualityCritic` | Score synthesis quality | GPT-4o as judge (0–1 score) |
| `ReportGenerator` | Produce final structured report | GPT-4o report template |

## Self-Correction Loop

The pipeline uses an iterative refinement state machine:

```python
for iteration in range(max_iterations):
    # Run parallel summarise + fact-check
    parallel_analysis.run(goal, inputs)

    quality = context.scratch["critique"]["overall_quality"]
    if quality >= 0.75:
        break  # quality threshold met

    # Accumulate critique in EpisodicMemory for next iteration
    memory["critique_iter_{i}"] = critique
```

Quality improves each iteration as the pipeline incorporates critique feedback. `EpisodicMemory` ensures the improving context is available across iterations.

## Ensemble Confidence Score

Run the pipeline N times to measure output stability:

```
agreement_score = 1.0 - (std_quality / mean_quality)
```

High agreement (> 0.90) indicates the pipeline produces consistent results regardless of LLM sampling randomness. Low agreement suggests the research topic is genuinely ambiguous or sources are contradictory.

## Notebook

Full runnable implementation:

[`notebooks/use_cases/uc_02_research_pipeline.ipynb`](../../notebooks/use_cases/uc_02_research_pipeline.ipynb)

Sections:
1. Mock source data (5 academic sources)
2. All 6 agent definitions
3. DAG construction
4. Single run with iterative refinement
5. Quality progression analysis
6. Ensemble confidence measurement
7. Final report output
