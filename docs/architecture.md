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
