
## 1. Client → API Gateway  
**Goal (Phase 1):** Expose a secure “start workflow” endpoint

```http
POST /v1/workflows/run
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "text": "Review this contract and extract any NDA clauses",
  "payload": {
    "document_url": "https://acme.example.com/contract.pdf"
  }
}
```

- **mTLS/JWT** validated, rate-limits enforced (Traefik + FastAPI).
- Routed to the **Orchestrator Core**.

---

## 2. Orchestrator Core  
**Goal (Phase 1–2 & 5–8):** Preprocess → parse DSL → RL policy → dispatch

1. **LLM Preprocessor**  
   - Sees `req.text`, calls `text_to_dsl("Review this…")`.  
   - Returns a JSON DSL:

     ```json
     {
       "steps":[
         { "name":"ocr",             "agent":"OCRAgent",          "params":{} },
         { "name":"ndadetect",       "agent":"NDAAgent",          "params":{} },
         { "name":"summarize",       "agent":"LangChainAgent",    "params":{} },
         { "condition":"ndadetect.output.count > 0",
           "then":      {"agent":"ApprovalAgent","params":{"role":"Legal"}},
           "else":      {"agent":"EchoAgent","params":{}}
         }
       ]
     }
     ```

2. **DSL Parser**  
   - `parse_workflow(...)` → in-memory `Step`/`ConditionalBranch` graph.  
   - Unknown-key warnings via `SyntaxWarning`.

3. **Pre-invoke Guardrails** (Phase 5)  
   - OPA policy checks: “Is NDA extraction allowed on this tenant?”  
   - May deny, pause, or proceed.

4. **RL Policy Hook** (Phase 8)  
   - `selectAction("OCRAgent", context)` may swap in `BetterOCRAgent` if prior rewards favored it.

5. **Capability Lookup** (Phase 4)  
   - Query **CapabilityDirectory** (MongoDB) for `NDAAgent`.  
   - **Case A: Agent exists** → continue.  
   - **Case B: Agent missing** → emit a `need.agent` event to Kafka (multigen.agent-factory).

---

## 3. AgentFactory Fallback  
**Goal (Phase 10):** Auto-generate missing agents

1. **AgentFactory Service** (listening on `multigen.agent-factory`)  
   - Receives `{ agent: "NDAAgent", schema: { /* optional hints */ } }`.  
   - Calls LLM with a prompt like:  
     > “Generate a Python agent class named `NDAAgent` subclassing `BaseAgent`, which takes text input and returns `{ count, clauses }` by detecting NDA clauses.”

2. **Generate & Test**  
   - Outputs `agents/nda_agent.py` containing:

     ```python
     from orchestrator.services.agent_registry import register_agent
     from agents.base_agent import BaseAgent

     @register_agent("NDAAgent")
     class NDAAgent(BaseAgent):
         async def run(self, params):
             text = params["text"] or params.get("ocr_output","")
             clauses = [c for c in text.split("\\n") if "NDA" in c]
             return {"count": len(clauses), "clauses": clauses}
     ```

   - CI pipeline runs SAST/DAST, then Helm-deploys to Kubernetes.

3. **Hot-Reload**  
   - Orchestrator worker dynamically imports this new module (via `discover_agents()`), so `get_agent("NDAAgent")` now succeeds.

---

## 4. Dispatch to Kafka → Flow Engine Worker  
**Goal (Phase 2):** Execute steps via Kafka + Temporal

1. **Orchestrator** publishes on **`multigen.invocations`**:

   ```json
   {
     "workflow_id":"wf-1234",
     "steps":[
       {"name":"ocr","params":{}},
       {"name":"ndadetect","params":{}},
       {"name":"summarize","params":{}},
       { "parallel_with":[ /* conditional group flattened */ ] }
     ],
     "payload":{ "document_url":"https://…" }
   }
   ```

2. **Flow-Messaging Worker**  
   - Consumes from `multigen.invocations`.  
   - Calls `run_complex_workflow(...)` on the Temporal client.

3. **Temporal Workflow** (Phase 2,6)  
   - Executes each activity with `agent_activity`—invoking `get_agent(...)`.  
   - Applies **parallel**, **conditional**, **retry**, **error handling**.  
   - On error after retries: routes to **ErrorHandler** → publishes DLQ on `multigen.error`.

---

## 5. Agents & ToolAdapters in Action  
**Goal (Phase 3,3.6):** LLM calls, vector retrieval, external APIs

- **OCRAgent**: runs Tesseract → returns raw text.  
- **NDAAgent**: (dynamically generated) filters NDA clauses.  
- **LangChainAgent**: wraps LangChain’s `LLMChain` for summarization.  
- **ApprovalAgent**:  
  - Opens a task in the **ApprovalEngine** (Node.js + React UI).  
  - Blocks here until human “Legal” signs off or escalates via **EscalationManager**.

---

## 6. Publish Results  
**Goal (Phase 2)**  
- On success: publish to **`multigen.responses`**:
  ```json
  {
    "workflow_id":"wf-1234",
    "status":"completed",
    "results":[
      {"agent":"ocr","output":{/* text */}},
      {"agent":"ndadetect","output":{"count":2,"clauses":[…]}},
      {"agent":"summarize","output":{"generated":"…summary…"}},
      {"agent":"ApprovalAgent","output":{"approved":true}}
    ]
  }
  ```
- On unrecoverable error: publish to **`multigen.error`** DLQ.

---

## 7. Feedback Loop & Continuous Learning  
**Goals (Phase 7–9,11–12):**

1. **FeedbackCollector** captures:
   - Human corrections from `ApprovalAgent` (e.g. missed clauses).  
   - Final workflow outputs and metadata.

2. **FeedbackStore** (Postgres) persists annotations and model corrections.

3. **FeedbackRewarder** converts feedback to RL rewards:
   - +1 for correct NDA detection, –1 for misses/extras.

4. **PolicyLearner** (PyTorch + PPO)  
   - Trains on logged rewards and metrics (latency, cost).  
   - Emits an updated policy that the Orchestrator consults at decision-time.

5. **Prompt & Flow Optimizers** (Phase 9)  
   - Node.js services that tune the DSL generator prompts or agent parameters based on trending feedback.

6. **Observability** (Phase 11)  
   - Prometheus exports: workflow durations, queue lags, success vs DLQ rates.  
   - Grafana dashboards, Jaeger traces across API → Temporal → Agents.

7. **ChaosController & Autoscaler** (Phase 11,12)  
   - Periodic chaos injection into agent pods, plus Kubernetes HPA scaling on Kafka lag.

8. **CostMonitor & SecurityScanner** (Phase 10–12)  
   - Enforce budget quotas, scan generated agent code via SAST/DAST before deployment.

9. **Explainability Dashboard**  
   - Surface “Why did the policy choose `NDAAgent` v.s. `ClauseExtractorAgent`?”  
   - Show real-time state, pending approvals, SLA metrics.

---

### Complete Sequence Diagram (simplified)

```text
Client
  ↓ HTTP /run
API Gateway
  ↓ internal gRPC
Orchestrator
  ├─ text_to_dsl (LLM)
  ├─ parse_workflow (DSL Parser)
  ├─ guardrails check (OPA)
  ├─ selectAction (RL policy)
  ├─ need.agent? → AgentFactory → dynamic codegen
  └─ publish → Kafka(multigen.invocations)
      ↓
Flow-Messaging Worker
  └─ run_complex_workflow → Temporal SDK
      ├─ agent_activity (OCRAgent)
      ├─ agent_activity (NDAAgent)
      ├─ agent_activity (LangChainAgent)
      └─ agent_activity (ApprovalAgent)
      ↓
  publishes → Kafka(multigen.responses / multigen.error)
      ↓
Client polls or webhook callback

FeedbackCollector → FeedbackStore → PolicyLearner → Updated RL policy
```

**Key takeaways**:

- **Automatic fallback** via AgentFactory if an agent is missing.
- **Full feedback loop** drives continuous policy, prompt and codegen improvements.
- **All 12 phases** are touched—from API Gateway to Python SDK/CLI.