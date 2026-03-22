# Tutorial — Parallel Execution

## Overview

Multigen provides five parallel execution patterns. Each solves a different problem when you need multiple agents to work concurrently.

| Pattern | Class | Use Case |
|---------|-------|----------|
| MapReduce | `MapReduceCoordinator` | Process N items in parallel, combine results |
| Swarm | `SwarmCoordinator` | Same problem, multiple perspectives |
| FanOut | `GraphRunner` + parallel nodes | Branch into parallel paths, merge |
| Race | Custom async | First good answer wins |
| Batch | Custom loop | Process items in batches with concurrency |

---

## 1. MapReduceCoordinator

The workhorse of parallel execution. Distributes `shards` across mapper agents, then the reducer combines all mapper outputs.

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.patterns import MapReduceCoordinator

# Mapper: process one shard
def analyse_chunk(ctx: Context) -> AgentStep:
    chunk = ctx.scratch.get("current_shard", "")
    word_count = len(chunk.split())
    sentiment  = 0.7 if any(w in chunk for w in ["good", "great", "excellent"]) else 0.4
    return AgentStep(
        out_messages=[Message(role="assistant",
                             content=f"words={word_count} sentiment={sentiment}")],
        state_updates={"last_chunk_sentiment": sentiment},
        stop=False,
    )

# Reducer: combine all mapper outputs
def combine_analyses(ctx: Context) -> AgentStep:
    outputs = ctx.scratch.get("mapper_outputs", [])
    sentiments = []
    total_words = 0
    for output in outputs:
        for part in output.split():
            if part.startswith("sentiment="):
                sentiments.append(float(part.split("=")[1]))
            elif part.startswith("words="):
                total_words += int(part.split("=")[1])

    mean_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.5
    return AgentStep(
        out_messages=[Message(role="assistant",
                             content=f"mean_sentiment={mean_sentiment:.3f} total_words={total_words}")],
        state_updates={"mean_sentiment": mean_sentiment, "total_words": total_words},
        stop=True,
    )

analyser   = AgentBuilder("chunk-analyser", "analyser").with_step(analyse_chunk).build()
aggregator = AgentBuilder("aggregator",     "reducer").with_step(combine_analyses).build()

pipeline = MapReduceCoordinator(
    mappers=[analyser, analyser, analyser],   # 3 parallel mappers
    reducer=aggregator,
)

# documents will be assigned round-robin to mappers
result = pipeline.run(
    goal="Analyse all documents",
    inputs={
        "shards": [
            "This is a great product with excellent quality and fast delivery.",
            "Average performance. Nothing special about this item.",
            "Outstanding support team. Resolved my issue quickly. Very satisfied.",
        ]
    }
)
print(result.messages[-1].content)
# mean_sentiment=0.567 total_words=30
```

### MapReduce Mechanics

1. `shards` in `inputs` is split across mappers by index: `shard[i] → mapper[i % len(mappers)]`
2. Each mapper receives its shard in `ctx.scratch["current_shard"]`
3. After all mappers run, `ctx.scratch["mapper_outputs"]` = list of all mapper message content strings
4. The reducer runs once, with access to all mapper outputs

### Choosing the Number of Mappers

```python
documents = load_documents()    # e.g., 100 documents

# Option 1: One mapper per shard (true parallelism, more overhead)
pipeline = MapReduceCoordinator(
    mappers=[analyser] * len(documents),
    reducer=aggregator,
)

# Option 2: Fixed pool of mappers (round-robin distribution)
pipeline = MapReduceCoordinator(
    mappers=[analyser] * 4,   # pool of 4
    reducer=aggregator,
)
result = pipeline.run(goal="...", inputs={"shards": documents})
```

The framework distributes shards to mappers in round-robin: with 4 mappers and 100 shards, each mapper processes ~25 shards sequentially.

---

## 2. SwarmCoordinator

Multiple agents explore the same problem independently. Their outputs are aggregated by a central coordinator.

```python
from agentic_codex.patterns import SwarmCoordinator

def explorer_step(ctx: Context) -> AgentStep:
    """
    Each explorer takes a different approach to the same problem.
    In production, use different LLM models, temperatures, or prompts.
    """
    import random
    import hashlib

    # Simulate different agents producing slightly different analyses
    agent_id = ctx.scratch.get("_agent_name", "explorer")
    seed_hash = int(hashlib.md5(agent_id.encode()).hexdigest()[:8], 16)
    random.seed(seed_hash)

    quality  = round(0.5 + random.uniform(0, 0.4), 3)
    approach = random.choice(["statistical", "heuristic", "rule_based"])

    return AgentStep(
        out_messages=[Message(role="assistant",
                             content=f"approach={approach} quality={quality}")],
        state_updates={"quality": quality, "approach": approach},
        stop=False,
    )

explorers = [
    AgentBuilder(f"explorer_{i}", "explorer").with_step(explorer_step).build()
    for i in range(5)
]

swarm = SwarmCoordinator(explorers=explorers)

result = swarm.run(
    goal="Find the best approach to this classification problem",
    inputs={"problem": "classify customer intent from chat messages"}
)

print("Swarm messages:")
for msg in result.messages:
    print(f"  {msg.content}")
```

### When to use Swarm vs MapReduce

| Dimension | SwarmCoordinator | MapReduceCoordinator |
|-----------|-----------------|---------------------|
| Input | Same context for all | Different shards per mapper |
| Purpose | Multiple perspectives on one problem | Same operation on many items |
| Output | Aggregated best answer | Combined statistics |
| Examples | Debate, voting, ensemble | Batch analysis, distributed extraction |

---

## 3. FanOut Pattern (via GraphRunner)

Use `GraphRunner` to branch into parallel paths and merge:

```python
from agentic_codex.patterns import GraphRunner, GraphNodeSpec, AssemblyCoordinator, Stage

def path_a_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content="path_a: technical analysis done")],
        state_updates={"path_a_done": True},
        stop=True,
    )

def path_b_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content="path_b: business analysis done")],
        state_updates={"path_b_done": True},
        stop=True,
    )

def merge_step(ctx: Context) -> AgentStep:
    a_done = ctx.scratch.get("path_a_done", False)
    b_done = ctx.scratch.get("path_b_done", False)
    return AgentStep(
        out_messages=[Message(role="assistant",
                             content=f"merged: technical={a_done} business={b_done}")],
        state_updates={"merged": True},
        stop=True,
    )

path_a  = AgentBuilder("path_a",  "analyst").with_step(path_a_step).build()
path_b  = AgentBuilder("path_b",  "analyst").with_step(path_b_step).build()
merger  = AgentBuilder("merger",  "merger").with_step(merge_step).build()

graph = GraphRunner(nodes=[
    GraphNodeSpec(id="path_a", coordinator=AssemblyCoordinator([Stage(agent=path_a, name="a")]), deps=[]),
    GraphNodeSpec(id="path_b", coordinator=AssemblyCoordinator([Stage(agent=path_b, name="b")]), deps=[]),
    GraphNodeSpec(id="merge",  coordinator=AssemblyCoordinator([Stage(agent=merger,  name="m")]), deps=["path_a", "path_b"]),
])

result = graph.run(goal="Analyse from multiple angles", inputs={})
for msg in result.messages:
    print(msg.content)
```

---

## 4. Race Pattern

The Race pattern runs two or more agents and takes the first one to return a satisfactory result. This is useful when you have:
- A fast heuristic (low confidence, quick) vs a slow ML model (high confidence, slow)
- Multiple LLM providers where the fastest one wins

```python
import threading
import queue

def run_race(agents_and_steps, ctx, confidence_threshold=0.70):
    """
    Run agents concurrently. Return the first result meeting the confidence threshold.
    Falls back to highest-confidence result if no agent meets the threshold.
    """
    results_queue = queue.Queue()
    threads = []

    def run_agent(agent_fn, agent_name, ctx_copy):
        import copy
        local_ctx = copy.deepcopy(ctx_copy)
        result = agent_fn(local_ctx)
        confidence = local_ctx.scratch.get("confidence", 0.5)
        results_queue.put({
            "agent": agent_name,
            "result": result,
            "confidence": confidence,
            "ctx": local_ctx,
        })

    import copy
    for agent_fn, name in agents_and_steps:
        t = threading.Thread(target=run_agent, args=(agent_fn, name, ctx))
        t.daemon = True
        threads.append(t)
        t.start()

    collected = []
    while len(collected) < len(agents_and_steps):
        item = results_queue.get(timeout=30)
        collected.append(item)
        if item["confidence"] >= confidence_threshold:
            # First to meet threshold wins
            return item

    # Fall back to highest confidence
    return max(collected, key=lambda x: x["confidence"])


# Example usage
def fast_heuristic(ctx: Context) -> AgentStep:
    import time; time.sleep(0.1)  # fast
    confidence = 0.65
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"heuristic result (conf={confidence})")],
        state_updates={"confidence": confidence, "method": "heuristic"},
        stop=True,
    )

def thorough_ml(ctx: Context) -> AgentStep:
    import time; time.sleep(0.8)  # slower
    confidence = 0.91
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"ML result (conf={confidence})")],
        state_updates={"confidence": confidence, "method": "ml"},
        stop=True,
    )

ctx = Context(goal="Classify this incident")
winner = run_race([(fast_heuristic, "heuristic"), (thorough_ml, "ml")], ctx)
print(f"Race winner: {winner['agent']} with confidence={winner['confidence']}")
```

---

## 5. Batch Processing Pattern

Process large datasets in parallel batches:

```python
import math
from typing import List, Any

def run_in_batches(
    items: List[Any],
    batch_size: int,
    mapper_agent,
    reducer_agent,
) -> dict:
    """Process items in batches using MapReduce."""
    n_batches = math.ceil(len(items) / batch_size)
    all_results = []

    for batch_idx in range(n_batches):
        batch = items[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        print(f"  Processing batch {batch_idx + 1}/{n_batches} ({len(batch)} items)")

        pipeline = MapReduceCoordinator(
            mappers=[mapper_agent] * min(len(batch), 4),
            reducer=reducer_agent,
        )
        result = pipeline.run(goal="Process batch", inputs={"shards": batch})
        # Collect batch-level results from last message
        if result.messages:
            all_results.append(result.messages[-1].content)

    return {"batches_processed": n_batches, "batch_results": all_results}


# Usage
documents = [f"Document {i}: content about topic {i % 5}" for i in range(20)]
batch_result = run_in_batches(documents, batch_size=5, mapper_agent=analyser, reducer_agent=aggregator)
print(f"Processed {batch_result['batches_processed']} batches")
```

---

## Latency Math

Understanding parallel vs sequential latency helps you choose the right pattern:

```
Sequential (N agents):    latency ≈ L1 + L2 + ... + LN
Parallel (N agents):      latency ≈ max(L1, L2, ..., LN)

For 3 agents, each taking 1 second:
  Sequential: 3 seconds
  Parallel:   1 second  (3x speedup)

For a MapReduce with 10 shards, 4 mappers, each 200ms:
  3 shards per mapper (round-robin) × 200ms = 600ms map phase
  + reducer latency (200ms) = 800ms total
  vs sequential: 10 × 200ms = 2000ms (2.5x speedup)
```

---

## Related Notebooks

- `notebooks/06_fan_out_consensus.ipynb` — fan-out with voting strategies
- `notebooks/11_parallel_bfs_streaming_a2a.ipynb` — parallel BFS + streaming
- `notebooks/use_cases/uc_01_credit_risk_assessment.ipynb` — MapReduce in production
- `notebooks/use_cases/uc_03_devops_aiops.ipynb` — Race pattern for incident response
