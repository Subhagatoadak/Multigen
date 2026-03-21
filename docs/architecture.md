# Multigen Architecture

## 1. System Architecture

High-level view of all services, their roles, and communication paths.

```mermaid
graph TB
    subgraph Clients["Clients"]
        CLI["CLI / SDK"]
        UI["Web UI<br/>(future)"]
    end

    subgraph Gateway["API Surface"]
        APIGW["API Gateway<br/>(future: Kong / AWS)"]
    end

    subgraph Orchestration["Orchestration Core  ·  FastAPI"]
        ORCH["Orchestrator<br/>:8000"]
        DSL["DSL Parser"]
        LLM["LLM Service<br/>(text → DSL)"]
        CAPVAL["Capability Validator"]
    end

    subgraph CapabilityService["Capability Service  ·  FastAPI :8001"]
        CAPREG["Registration API<br/>/capabilities"]
        CAPDB[("Capability Store<br/>MongoDB")]
    end

    subgraph Messaging["Message Bus  ·  Apache Kafka"]
        FREQ["flow-requests"]
        FRES["flow-responses"]
        DLQ["flow-dead-letter"]
    end

    subgraph FlowWorker["Flow Worker  ·  Python"]
        FM["Flow Messaging<br/>(Kafka Consumer)"]
        METRICS["Prometheus<br/>Metrics :8000"]
    end

    subgraph WorkflowEngine["Workflow Engine  ·  Temporal"]
        WF["ComplexSequenceWorkflow"]
        COND["Conditional Evaluator<br/>(AST-safe eval)"]
        PAR["Parallel Runner<br/>(asyncio.gather)"]
        DYN["Dynamic Subtree<br/>Executor"]
    end

    subgraph Agents["Agents  ·  BaseAgent"]
        ECHO["EchoAgent"]
        LC["LangChainAgent<br/>(GPT-4o via LCEL)"]
        LI["LlamaIndexAgent<br/>(vector retrieval)"]
        SPAWN["SpawnerAgent<br/>(dynamic steps)"]
        CUSTOM["Custom Agents<br/>@register_agent(...)"]
    end

    subgraph Observability["Observability"]
        OTEL["OpenTelemetry<br/>Collector"]
        JAEGER["Jaeger / Grafana<br/>(traces)"]
        PROM["Prometheus<br/>(metrics)"]
    end

    CLI --> ORCH
    UI --> APIGW --> ORCH

    ORCH --> DSL
    ORCH --> LLM
    ORCH --> CAPVAL --> CAPREG --> CAPDB
    ORCH --> FREQ

    FREQ --> FM
    FM --> WF
    WF --> COND & PAR & DYN
    WF -->|agent_activity| ECHO & LC & LI & SPAWN & CUSTOM
    WF --> FRES
    WF -->|on error| DLQ

    FM --> METRICS --> PROM
    ORCH --> OTEL
    WF --> OTEL
    OTEL --> JAEGER

    classDef service fill:#0d6efd,color:#fff,stroke:none
    classDef store fill:#6c757d,color:#fff,stroke:none
    classDef queue fill:#fd7e14,color:#fff,stroke:none
    classDef agent fill:#198754,color:#fff,stroke:none
    classDef obs fill:#6f42c1,color:#fff,stroke:none

    class ORCH,FM,CAPREG,WF service
    class CAPDB store
    class FREQ,FRES,DLQ queue
    class ECHO,LC,LI,SPAWN,CUSTOM agent
    class OTEL,JAEGER,PROM,METRICS obs
```

---

## 2. Request Flow

End-to-end trace of a workflow from client submission to agent execution.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant O as Orchestrator
    participant L as LLM Service
    participant D as DSL Parser
    participant Cap as Capability Dir
    participant K as Kafka
    participant FM as Flow Worker
    participant T as Temporal
    participant A as Agent

    C->>O: POST /workflows/run<br/>{dsl | text, payload}

    alt text input
        O->>L: text_to_dsl(text)
        L-->>O: DSL dict
    end

    O->>D: parse_workflow(dsl)
    D-->>O: List[Step]

    loop each step with agent
        O->>Cap: validate_agent(name)
        Cap-->>O: Capability ✓
    end

    O->>K: publish(flow-requests, {workflow_id, steps, payload})
    O-->>C: {instance_id: "uuid"}

    K->>FM: consume(flow-requests)
    FM->>T: start_workflow(ComplexSequenceWorkflow)

    loop each step
        alt sequential
            T->>A: agent_activity(name, params)
            A-->>T: {agent, output}
        else parallel
            par branch A
                T->>A: agent_activity(a, params)
            and branch B
                T->>A: agent_activity(b, params)
            end
        else conditional
            Note over T: evaluate_condition(expr, context)
            T->>A: agent_activity(chosen_branch)
            A-->>T: {agent, output}
        else dynamic
            T->>A: agent_activity(SpawnerAgent)
            A-->>T: {spawned_steps: [...]}
            loop spawned step
                T->>A: agent_activity(spawned)
            end
        end
        T->>T: context[step_name] = output
    end

    alt success
        T->>K: publish(flow-responses, results)
    else error
        T->>K: publish(flow-dead-letter, error)
    end
```

---

## 3. Step Execution Model

How the workflow engine handles the four DSL step types.

```mermaid
flowchart TD
    START([Incoming Step]) --> TYPE{step.type?}

    TYPE -->|sequential| SEQ[Execute agent_activity<br/>name, agent, params]
    TYPE -->|parallel| PAR[asyncio.gather all branches<br/>parallel_with list]
    TYPE -->|conditional| COND_EVAL[Evaluate each condition<br/>against context dict]
    TYPE -->|dynamic| DYN_SPAWN[Run SpawnerAgent<br/>get spawned_steps]

    COND_EVAL --> MATCH{condition matched?}
    MATCH -->|yes| COND_RUN[Execute matched branch]
    MATCH -->|no, try next| COND_EVAL
    MATCH -->|else branch| ELSE_RUN[Execute else branch]

    DYN_SPAWN --> SUB_LOOP[Loop: execute each<br/>spawned step sequentially]

    SEQ & PAR & COND_RUN & ELSE_RUN & SUB_LOOP --> STORE[Store output in context<br/>context-step_name-= output]
    STORE --> NEXT([Next Step])
```

---

## 4. Agent Registration & Discovery

How agents register themselves and how the orchestrator resolves them.

```mermaid
flowchart LR
    subgraph Agent Process
        A["@register_agent('Name')<br/>class MyAgent(BaseAgent)"]
        REG_CALL["self_register()<br/>on startup"]
    end

    subgraph Registry
        MEM["In-Memory Registry<br/>_registry: Dict[str, Type]"]
        CAP_SVC["Capability Directory<br/>(MongoDB)"]
    end

    subgraph Orchestrator
        LOOKUP["get_agent(name)<br/>from agent_registry"]
        VALIDATE["validate_agent(name)<br/>HTTP → capability-service"]
    end

    A -->|@register_agent decorator| MEM
    REG_CALL -->|POST /capabilities| CAP_SVC

    VALIDATE -->|GET /capabilities/name| CAP_SVC
    CAP_SVC -->|Capability metadata| VALIDATE

    LOOKUP -->|instantiate + run| A
```

---

## 5. DSL Reference

Valid step structures in the Multigen workflow DSL.

```mermaid
block-beta
  columns 1
  block:seq["Sequential Step"]:1
    S1["name: step_name\nagent: EchoAgent\nparams:\n  key: value"]
  end
  block:par["Parallel Step"]:1
    S2["name: parallel_step\nparallel:\n  - name: branch_a\n    agent: AgentA\n  - name: branch_b\n    agent: AgentB"]
  end
  block:cond["Conditional Step"]:1
    S3["name: route\nconditional:\n  - condition: \"status == 'approved'\"\n    then: ApprovalAgent\nelse: RejectionAgent"]
  end
  block:dyn["Dynamic Subtree Step"]:1
    S4["name: expand\nagent: SpawnerAgent\nparams:\n  count: 3\n  agent: EchoAgent\ndynamic_subtree:\n  config: {}"]
  end
```

---

## 6. Enterprise Target Architecture

The planned production-scale deployment.

```mermaid
graph TB
    subgraph Users
        DEV["Developers<br/>(CLI / SDK)"]
        OPS["Operators<br/>(Dashboard)"]
        ENT["Enterprise Apps<br/>(API)"]
    end

    subgraph Edge["Edge Layer"]
        GW["API Gateway<br/>Kong / AWS API GW"]
        AUTH["Auth Service<br/>JWT / mTLS"]
    end

    subgraph K8s["Kubernetes Cluster  (EKS / GKE)"]
        subgraph Control["Control Plane"]
            ORCH2["Orchestrator<br/>Pods (HPA)"]
            GUARD["Guardrails Engine<br/>(OPA)"]
            APPROVAL["Approval Engine<br/>(Human-in-loop)"]
            ERRH["Error Handler<br/>+ Escalation"]
        end

        subgraph DataPlane["Data Plane"]
            FLOW2["Flow Workers<br/>Pods (HPA)"]
            AGENTS["Agent Pods<br/>(per-agent Deployments)"]
        end

        subgraph Learning["Feedback & Learning"]
            FC["Feedback Collector"]
            RL["RL Policy Learner<br/>(PPO / SageMaker)"]
            AF["AgentFactory<br/>(LLM + SAST + CI)"]
        end
    end

    subgraph Infra["Managed Infrastructure"]
        KAFKA2["Kafka (MSK)"]
        MONGO2["MongoDB Atlas"]
        TEMPORAL2["Temporal Cloud"]
        PINECONE["Vector Store<br/>(Pinecone / Weaviate)"]
    end

    subgraph Observe["Observability Stack"]
        OTEL2["OTel Collector"]
        GRAFANA["Grafana Dashboards"]
        JAEGER2["Jaeger (Traces)"]
        PROM2["Prometheus (Metrics)"]
        SPLUNK["Splunk (Audit)"]
    end

    DEV & OPS & ENT --> GW --> AUTH --> ORCH2
    ORCH2 --> GUARD --> FLOW2
    FLOW2 --> KAFKA2 --> AGENTS
    AGENTS --> KAFKA2 --> FLOW2
    FLOW2 --> APPROVAL & ERRH
    FLOW2 --> FC --> RL --> ORCH2
    AF --> AGENTS
    ORCH2 & FLOW2 & AGENTS --> OTEL2
    OTEL2 --> GRAFANA & JAEGER2 & PROM2 & SPLUNK
    ORCH2 --> MONGO2 & TEMPORAL2
    AGENTS --> PINECONE

    classDef k8s fill:#326CE5,color:#fff,stroke:none
    classDef infra fill:#FF9900,color:#000,stroke:none
    classDef obs fill:#6f42c1,color:#fff,stroke:none
    class ORCH2,FLOW2,AGENTS,GUARD,APPROVAL,ERRH,FC,RL,AF k8s
    class KAFKA2,MONGO2,TEMPORAL2,PINECONE infra
    class OTEL2,GRAFANA,JAEGER2,PROM2,SPLUNK obs
```

---

## 7. Parallel BFS Execution Model

How the graph workflow executes dependency-aware waves of nodes in parallel using a BFS loop.

```mermaid
flowchart TD
    START([Start BFS Loop]) --> COLLECT["_collect_ready_batch()\nDrain pending queue\nFor each candidate node:\ncheck _deps_satisfied()"]

    COLLECT --> DEPS_CHECK{"_deps_satisfied(node)?"}

    DEPS_CHECK --> DEPS_DETAIL["Union of:\n• edge-upstream nodes\n• explicit depends_on list\nAll must be in context"]

    DEPS_DETAIL --> READY_CHECK{"Any nodes\nready?"}

    READY_CHECK -->|"No — pending\nnot empty"| DEADLOCK["Deadlock / cycle\ndetected → break"]
    READY_CHECK -->|"No — pending\nempty"| DONE([BFS Complete])
    READY_CHECK -->|"Yes"| GATHER["asyncio.gather(\n  *[_execute_node_full(n)\n    for n in ready_batch]\n)"]

    GATHER --> RESULTS["For each result:\n_process_node_result(node, result)"]

    RESULTS --> UPDATE["• Write output to context\n• Persist to MongoDB\n• Enqueue successors\n  (deduped via pending_set)"]

    UPDATE --> START
```

Dependency resolution detail — `depends_on` union with edge-derived deps:

```mermaid
flowchart LR
    subgraph Node["Node: process_data"]
        EDGE_DEPS["Edge-upstream deps\n(inferred from graph edges)"]
        EXPLICIT["depends_on field\n(explicit extra deps)"]
        UNION["Union of both sets"]
    end

    subgraph Context["Workflow Context"]
        CTX["context dict\n{node_name: output}"]
    end

    EDGE_DEPS --> UNION
    EXPLICIT --> UNION
    UNION --> SATISFIED{"All dep keys\npresent in context?"}
    CTX --> SATISFIED
    SATISFIED -->|"Yes"| ENQUEUE["Add to ready_batch"]
    SATISFIED -->|"No"| WAIT["Stay in pending queue\nretry next wave"]
```

---

## 8. Partition-Aware Fan-Out

How fan-out nodes are distributed across multiple Temporal task queues for parallel execution on separate worker pools.

```mermaid
flowchart TD
    ENV["TEMPORAL_TASK_QUEUES env var\ne.g. 'queue-a,queue-b,queue-c'"] --> CONFIG["config.TEMPORAL_TASK_QUEUES\n['queue-a', 'queue-b', 'queue-c']"]

    FAN_SIG["fan_out signal received\n{nodes: [n0, n1, n2, n3, n4],\n task_queues: [...] (optional)}"] --> ASSIGN

    CONFIG --> ASSIGN["Assign task queues\nnode.task_queue =\ntask_queues[idx % len(task_queues)]"]

    ASSIGN --> N0["n0 → queue-a"]
    ASSIGN --> N1["n1 → queue-b"]
    ASSIGN --> N2["n2 → queue-c"]
    ASSIGN --> N3["n3 → queue-a"]
    ASSIGN --> N4["n4 → queue-b"]

    subgraph WorkerPools["Worker Pools (Temporal Workers)"]
        WPA["Worker Pool A\nlistening: queue-a"]
        WPB["Worker Pool B\nlistening: queue-b"]
        WPC["Worker Pool C\nlistening: queue-c"]
    end

    N0 & N3 --> WPA
    N1 & N4 --> WPB
    N2 --> WPC

    GATHER_FO["asyncio.gather(\n  *[dispatch_activity(n)\n    for n in fan_out_nodes]\n)"]

    WPA & WPB & WPC --> GATHER_FO

    GATHER_FO --> CONSENSUS["select_consensus(results)\nAggregate / vote on outputs"]
    CONSENSUS --> CTX_UPDATE["Write consensus result\nto workflow context"]
```

---

## 9. SSE Streaming Architecture

How completed node events are streamed to clients in real time via Server-Sent Events, with polling fallback.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant F as FastAPI Orchestrator
    participant T as Temporal Workflow

    Note over T: _completed_events list<br/>(append-only, indexed)

    C->>F: GET /workflows/{id}/stream<br/>Last-Event-ID: 4 (optional)
    F->>F: StreamingResponse(_event_generator())<br/>cursor = int(Last-Event-ID) if present else 0

    loop Every 500 ms
        F->>T: handle.query("get_completed_nodes")
        T-->>F: {events: [...], done: bool}
        loop For each event where index > cursor
            F-->>C: data: {"node": "...", "output": {...}}\nid: {index}\n\n
            F->>F: cursor = index
        end
        alt done == True
            F-->>C: data: {"done": true}\n\n
            F->>F: close generator
        end
    end

    Note over C,F: Reconnect flow
    C->>F: GET /workflows/{id}/stream<br/>Last-Event-ID: 4
    F->>F: Resume from cursor = 4<br/>skip already-sent events
```

Polling fallback for clients that do not support SSE:

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant F as FastAPI Orchestrator
    participant T as Temporal Workflow

    C->>F: GET /workflows/{id}/events?since=4
    F->>T: handle.query("get_completed_nodes")
    T-->>F: {events: [...], done: bool}
    F->>F: Filter events where index > 4
    F-->>C: 200 OK\n[{index: 5, node: "...", output: {...}}, ...]

    Note over C: Client increments since param<br/>and polls again after delay
```

---

## 10. Updated Request Flow (Graph Workflow with All Features)

End-to-end sequence covering parallel BFS execution, partition-aware fan-out, SSE streaming, A2A nodes, human approval gates, and dynamic agents.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant SSE as SSE Connection<br/>(Client EventSource)
    participant O as Orchestrator<br/>FastAPI :8000
    participant T as Temporal<br/>GraphWorkflow
    participant QA as Worker Pool A<br/>(queue-a)
    participant QB as Worker Pool B<br/>(queue-b)
    participant QC as Worker Pool C<br/>(queue-c)
    participant RA as Remote Agent<br/>(A2A JSON-RPC)
    participant H as Human Approver
    participant M as MongoDB

    C->>O: POST /workflows/graph/run\n{graph_dsl, payload}
    O->>T: start_workflow(GraphWorkflow, graph_dsl)
    O-->>C: {workflow_id: "uuid"}

    C->>O: GET /workflows/uuid/stream
    O-->>SSE: StreamingResponse (SSE open)

    Note over T: BFS Wave 1 — entry nodes (no deps)
    T->>QA: agent_activity(entry_node_1) via queue-a
    T->>QB: agent_activity(entry_node_2) via queue-b
    Note over T: asyncio.gather() — both in parallel
    QA-->>T: result_1
    QB-->>T: result_2
    T->>T: context[entry_node_1] = result_1\ncontext[entry_node_2] = result_2
    T->>M: persist(entry_node_1, entry_node_2 results)
    T->>T: _completed_events.append(...)
    T-->>SSE: query → {events: [node1, node2], done: false}
    SSE-->>C: data: {"node": "entry_node_1", ...}\ndata: {"node": "entry_node_2", ...}

    Note over T: BFS Wave 2 — fan-out node (deps satisfied)
    T->>T: fan_out signal\n{nodes: [n0..n4], task_queues: [a,b,c]}
    T->>QA: agent_activity(n0) queue-a
    T->>QB: agent_activity(n1) queue-b
    T->>QC: agent_activity(n2) queue-c
    T->>QA: agent_activity(n3) queue-a
    T->>QB: agent_activity(n4) queue-b
    Note over T: asyncio.gather() across all pools
    QA-->>T: n0, n3 results
    QB-->>T: n1, n4 results
    QC-->>T: n2 result
    T->>T: select_consensus(fan_out_results)
    T->>T: _completed_events.append(fan_out result)
    SSE-->>C: data: {"node": "fan_out", "output": {...}}

    Note over T: BFS Wave 3 — A2A node
    T->>QA: a2a_activity(remote_agent_url, task)
    QA->>RA: JSON-RPC tasks/send\n{task_id, message, params}
    RA-->>QA: JSON-RPC response\n{result, artifacts}
    QA-->>T: a2a result
    T->>T: context[a2a_node] = result
    T->>T: _completed_events.append(a2a result)
    SSE-->>C: data: {"node": "a2a_node", "output": {...}}

    Note over T: BFS Wave 4 — Human Approval Gate
    T->>T: pause and wait for\napprove_signal / reject_signal
    T->>M: persist(pending_approval state)
    SSE-->>C: data: {"node": "approval_gate", "status": "pending"}
    H->>O: POST /workflows/uuid/signal\n{signal: "approve", data: {...}}
    O->>T: signal(approve_signal)
    T->>T: resume BFS
    T->>T: _completed_events.append(approval result)
    SSE-->>C: data: {"node": "approval_gate", "status": "approved"}

    Note over T: BFS Wave 5 — Dynamic Agent
    T->>M: store dynamic agent spec\n{agent_spec, config}
    T->>QB: dynamic_agent_activity(spec) via queue-b
    Note over QB: Worker-2 hydrates agent\nfrom MongoDB spec at runtime
    QB->>M: fetch agent_spec(id)
    M-->>QB: agent spec
    QB->>QB: instantiate dynamic agent
    QB-->>T: dynamic agent result
    T->>T: context[dynamic_node] = result
    T->>T: _completed_events.append(dynamic result)
    SSE-->>C: data: {"node": "dynamic_agent", "output": {...}}

    Note over T: All nodes complete — done = true
    T->>T: _completed_events done flag set
    SSE-->>C: data: {"done": true}
    Note over C,SSE: SSE connection closed
```
