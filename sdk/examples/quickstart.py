"""
Multigen SDK Quick-Start Examples
==================================
Run any example:
    python -m asyncio sdk/examples/quickstart.py

All examples use FunctionAgent (no API key required).
Swap with LLMAgent("name", prompt="...", provider="openai") for real LLM calls.
"""

import asyncio
from multigen import (
    FunctionAgent, RouterAgent,
    CircuitBreakerAgent, MemoryAgent,
    Chain, Parallel, MapReduce,
    Graph, StateMachine,
    InMemoryBus, Message,
    Runtime,
)


# ─── 1. Simple sequential chain ─────────────────────────────────────────────

async def example_chain():
    print("\n=== 1. Sequential Chain ===")

    tokenize  = FunctionAgent("tokenize",  fn=lambda ctx: {"tokens": ctx["text"].split()})
    uppercase = FunctionAgent("uppercase", fn=lambda ctx: {"words": [w.upper() for w in ctx["tokenize"]["tokens"]]})
    join      = FunctionAgent("join",      fn=lambda ctx: {"result": " ".join(ctx["uppercase"]["words"])})

    result = await Chain([tokenize, uppercase, join]).run({"text": "hello world from multigen"})
    print("Chain result:", result.final_output)
    print("Steps:", result.step_names)


# ─── 2. Parallel fan-out ─────────────────────────────────────────────────────

async def example_parallel():
    print("\n=== 2. Parallel Fan-Out ===")

    finance  = FunctionAgent("finance",    fn=lambda ctx: {"insight": f"Finance: bullish on {ctx['topic']}"})
    tech     = FunctionAgent("technology", fn=lambda ctx: {"insight": f"Tech: disruptive in {ctx['topic']}"})
    risk     = FunctionAgent("risk",       fn=lambda ctx: {"insight": f"Risk: moderate for {ctx['topic']}"})

    result = await Parallel([finance, tech, risk]).run({"topic": "AI regulation"})
    print("Parallel outputs:")
    for agent_name, output in result.outputs.items():
        print(f"  {agent_name}: {output['insight']}")
    print(f"All succeeded: {result.all_succeeded}")


# ─── 3. Graph DAG ───────────────────────────────────────────────────────────

async def example_graph():
    print("\n=== 3. Graph (DAG) ===")

    g = Graph(name="analysis_pipeline")
    g.node("fetch",    FunctionAgent("fetch",    fn=lambda ctx: {"data": ["row1", "row2", "row3"]}))
    g.node("parse",    FunctionAgent("parse",    fn=lambda ctx: {"parsed": len(ctx["fetch"]["data"])}))
    g.node("enrich_a", FunctionAgent("enrich_a", fn=lambda ctx: {"a_score": ctx["parse"]["parsed"] * 2}))
    g.node("enrich_b", FunctionAgent("enrich_b", fn=lambda ctx: {"b_score": ctx["parse"]["parsed"] * 3}))
    g.node("merge",    FunctionAgent("merge",    fn=lambda ctx: {
        "total": ctx["enrich_a"]["a_score"] + ctx["enrich_b"]["b_score"]
    }))

    g.edge("fetch",    "parse")
    g.edge("parse",    "enrich_a")
    g.edge("parse",    "enrich_b")
    g.edge("enrich_a", "merge")
    g.edge("enrich_b", "merge")

    result = await g.run()
    print("Graph executed:", result.executed)
    print("Final output:", result.final_output)


# ─── 4. MCMC State Machine ───────────────────────────────────────────────────

async def example_state_machine():
    print("\n=== 4. MCMC State Machine ===")

    draft  = FunctionAgent("draft",  fn=lambda ctx: {
        "text": f"Draft answer for: {ctx.get('query', '')}", "quality": 0.4
    })
    review = FunctionAgent("review", fn=lambda ctx: {
        "text": ctx.get("draft", {}).get("text", "") + " [reviewed]", "quality": 0.75
    })
    final  = FunctionAgent("final",  fn=lambda ctx: {
        "text": ctx.get("review", {}).get("text", "") + " [FINAL]", "quality": 1.0
    })

    sm = StateMachine(name="writing_loop", start_state="draft", terminal_states={"final"}, max_steps=10)
    sm.state("draft",  draft)
    sm.state("review", review)
    sm.state("final",  final)

    sm.transition("draft",  "review", prob=0.8)
    sm.transition("draft",  "draft",  prob=0.2)   # self-loop: revise draft
    sm.transition("review", "final",  prob=0.7)   # confident → publish
    sm.transition("review", "draft",  prob=0.3)   # not satisfied → back to draft

    # Enable MCMC with temperature
    sm.enable_mcmc(temperature=0.6, sampler="weighted")

    result = await sm.run({"query": "Explain quantum computing"})
    print("Path taken:", result.path)
    print("Status:", result.status)
    print("Final output:", result.final_output.get("text", ""))

    # Ensemble: run 3 independent chains
    ensemble = await sm.run_ensemble({"query": "Explain quantum computing"}, chains=3)
    print("Ensemble best path:", ensemble.best_path)
    print("Agreement:", ensemble.agreement_score)


# ─── 5. Router + Circuit Breaker ─────────────────────────────────────────────

async def example_router_circuit_breaker():
    print("\n=== 5. Router + Circuit Breaker ===")

    pos_agent = FunctionAgent("positive_handler", fn=lambda ctx: {"action": "upsell", "reason": "positive sentiment"})
    neg_agent = FunctionAgent("negative_handler", fn=lambda ctx: {"action": "escalate", "reason": "negative sentiment"})
    neu_agent = FunctionAgent("neutral_handler",  fn=lambda ctx: {"action": "follow_up", "reason": "neutral"})

    def _classify(ctx):
        if ctx.get("score", 0) > 0.6:
            return "positive"
        if ctx.get("score", 0) < 0.3:
            return "negative"
        return "neutral"

    router = RouterAgent(
        "sentiment_router",
        classifier=_classify,
        routes={"positive": pos_agent, "negative": neg_agent, "neutral": neu_agent},
    )

    # Wrap in circuit breaker
    protected = CircuitBreakerAgent("protected_router", router, failure_threshold=3)

    for score in [0.8, 0.2, 0.5]:
        result = await protected({"score": score, "message": "Customer feedback"})
        print(f"  Score={score} → {result.get('action')}: {result.get('reason')}")


# ─── 6. Pub/Sub Event Bus ────────────────────────────────────────────────────

async def example_event_bus():
    print("\n=== 6. Event Bus (Pub/Sub + Request/Reply) ===")

    bus = InMemoryBus()
    received = []

    @bus.subscribe("analysis.done")
    async def on_analysis(msg: Message) -> None:
        received.append(msg.content)
        print(f"  Subscriber received: {msg.content}")

    @bus.on_request("classify")
    async def classify(msg: Message):
        text = msg.content.get("text", "")
        return {"label": "positive" if "good" in text.lower() else "negative", "text": text}

    # Publish
    await bus.publish(Message(topic="analysis.done", content={"score": 0.9, "source": "agent_A"}))

    # Request/Reply
    reply = await bus.request("classify", content={"text": "This is good!"})
    print(f"  Request/Reply: {reply.content}")
    print(f"  Bus stats: {bus.stats}")


# ─── 7. FanOut + MapReduce ───────────────────────────────────────────────────

async def example_map_reduce():
    print("\n=== 7. MapReduce ===")

    documents = [
        "The Fed raised rates by 25bp citing persistent inflation",
        "Tech stocks rallied 3% on strong earnings from NVIDIA",
        "Oil prices fell 2% amid demand concerns from China",
    ]

    mapper  = FunctionAgent("extract_keywords", fn=lambda ctx: {
        "keywords": ctx["item"].split()[:3], "doc": ctx["item"][:40]
    })
    reducer = FunctionAgent("aggregate",        fn=lambda ctx: {
        "summary": f"Processed {len(ctx['records'])} documents",
        "all_keywords": [kw for r in ctx['records'] for kw in r.get('keywords', [])],
    })

    result = await MapReduce(mapper=mapper, reducer=reducer, input_key="documents").run({"documents": documents})
    print("MapReduce result:", result.get("summary"))
    print("Keywords:", result.get("all_keywords", [])[:10])


# ─── 8. Runtime with simulator integration ───────────────────────────────────

async def example_runtime():
    print("\n=== 8. Unified Runtime ===")

    # Local runtime (no server needed)
    rt = Runtime()

    chain_result = await rt.run_chain(
        [
            FunctionAgent("fetch",  fn=lambda ctx: {"data": "raw_data"}),
            FunctionAgent("process", fn=lambda ctx: {"processed": ctx["fetch"]["data"].upper()}),
        ],
        ctx={"query": "test"},
        workflow_id="demo-001",
    )
    print("Runtime chain:", chain_result.final_output)
    print("Runtime stats:", rt.stats())

    # To push events to agentic-simulator UI:
    # rt = Runtime(simulator_url="http://localhost:8003")
    # All workflow events will appear in the Observability tab automatically


# ─── 9. Memory Agent (multi-turn) ───────────────────────────────────────────

async def example_memory():
    print("\n=== 9. Memory Agent (multi-turn) ===")

    base = FunctionAgent("responder", fn=lambda ctx: {
        "response": f"Turn {len(ctx.get('history', []))+1}: responding to '{ctx.get('message', '')}'"
    })
    memory_agent = MemoryAgent("memory_bot", base, window=5)

    for msg in ["Hello!", "What did I say first?", "Summarise our conversation"]:
        result = await memory_agent({"message": msg})
        print(f"  User: {msg}")
        print(f"  Bot:  {result['response']} (history: {result['history_length']} turns)")


# ─── Main ────────────────────────────────────────────────────────────────────

async def main():
    await example_chain()
    await example_parallel()
    await example_graph()
    await example_state_machine()
    await example_router_circuit_breaker()
    await example_event_bus()
    await example_map_reduce()
    await example_runtime()
    await example_memory()
    print("\n✓ All examples completed")


if __name__ == "__main__":
    asyncio.run(main())
