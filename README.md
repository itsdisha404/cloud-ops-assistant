# Cloud Operations Assistant

A multi-agent system that simulates a Cloud Operations Assistant: a Supervisor classifies an incoming request and routes it into one of two fixed ReAct-agent pipelines — **Cost Optimization** or **Security & Compliance Audit** — built with `langchain.agents.create_agent` and wired together with LangGraph's `StateGraph`. All cloud data is mocked with boto3-shaped responses; no real AWS access is used.

> **Status:** implemented and verified — every acceptance scenario in [`demo/scenarios.py`](cloud_assistant/demo/scenarios.py) passes its expected graph path. Run it from the browser (`python -m cloud_assistant.web.server`) or the terminal (`python -m cloud_assistant.demo.run_demo`). See [planning.md](planning.md) for the build order that produced it.

## Architecture

```
START
  │
  ▼
┌─────────────┐
│ supervisor  │  structured-output LLM classification (NOT create_agent — no tools)
└──────┬──────┘
       │  add_conditional_edges (3-way)
   ┌───┼──────────────┐
   ▼   ▼               ▼
 cost  security        END
 _analysis _audit       (ambiguous / out-of-scope: clarification or decline)
   │        │
   │ add_conditional_edges     │ add_conditional_edges
   │ (idle_resource_count≥1?)  │ (security_finding_count≥1?)
   ▼        ▼                  ▼        ▼
 cost      END               security   END
 _recommendation  (no waste)  _remediation (no findings)
   │                            │
   ▼                            ▼
  END                          END
```

**Nodes:** `supervisor`, `cost_analysis`, `cost_recommendation`, `security_audit`, `security_remediation`.

**Edges:**
- `START → supervisor`
- `supervisor → {cost_analysis | security_audit | END}` — one `add_conditional_edges` call, 3-way `path_map`
- `cost_analysis → {cost_recommendation | END}` — conditional on `idle_resource_count`
- `cost_recommendation → END`
- `security_audit → {security_remediation | END}` — conditional on `security_finding_count`
- `security_remediation → END`

**Design note on the supervisor's third branch.** The literal task spec lists only two conditional supervisor edges (`→cost_analysis`, `→security_audit`), but the "ambiguous/out-of-scope" scenario requires the graph to also be able to end without forcing a route. The correct LangGraph pattern for this is **not** two separate `add_conditional_edges` calls — it's a single `add_conditional_edges("supervisor", router_fn, path_map)` where the router function has three possible return values and `path_map` maps the third one to `END`. This is called out explicitly here so it reads as an intentional resolution of an underspecified edge list, not an oversight.

Every one of the 4 tool-calling agents (`cost_analysis`, `cost_recommendation`, `security_audit`, `security_remediation`) is built with `langchain.agents.create_agent` — the current LangChain 1.0 ReAct factory, not the deprecated `langgraph.prebuilt.create_react_agent`. Each `create_agent(...)` call is itself a compiled LangGraph graph with its own message-based `AgentState`; since that differs from the parent graph's custom state, each is wrapped by a plain node function that translates parent state → sub-agent input → parent state on the way out (the standard "different state schema" subgraph pattern), rather than being nested directly as a node.

### Typed state schema (`state.py`)

Shared parent-graph state, `CloudOpsState(TypedDict, total=False)`:

| Field | Type | Purpose |
|---|---|---|
| `messages` | `Annotated[list[AnyMessage], add_messages]` | accumulated conversation/tool transcript across all nodes |
| `workflow` | `Literal["cost","security","unclear",None]` | supervisor's routing decision |
| `supervisor_rationale`, `supervisor_confidence` | `str`, `float` | why the supervisor classified as it did (for logs) |
| `account_id` | `str` | target mocked AWS account |
| `cost_analysis_result` | `CostAnalysisResult \| None` | structured output of `cost_analysis` |
| `idle_resource_count` | `int` | **flattened** top-level count, read by the router |
| `cost_recommendation_result` | `CostRecommendationResult \| None` | structured output of `cost_recommendation` |
| `security_audit_result` | `SecurityAuditResult \| None` | structured output of `security_audit` |
| `security_finding_count` | `int` | **flattened** top-level count, read by the router |
| `security_remediation_result` | `SecurityRemediationResult \| None` | structured output of `security_remediation` |
| `final_response` | `str` | the human-readable answer returned to the user |
| `error`, `error_node` | `str \| None` | set when a node degrades gracefully instead of crashing |

`idle_resource_count` / `security_finding_count` are deliberately duplicated out of the nested Pydantic result objects as flat top-level ints, so the conditional-edge router functions can do a trivial, defensive `state.get("idle_resource_count", 0) >= 1` check without reaching into a possibly-`None` nested object.

Every agent's `response_format` is a Pydantic model (also defined in `state.py`): `CostAnalysisResult`, `CostRecommendationResult`, `SecurityAuditResult`, `SecurityRemediationResult`, plus `SupervisorDecision` for the classifier. Passing `response_format=<Model>` to `create_agent` makes it capture the model's structured output into `result["structured_response"]`, validated against that schema — this is how conditional routing gets typed, machine-readable fields instead of having to parse free-text LLM prose, which is what keeps the "branching logic must be a graph edge condition, not hidden in a prompt" constraint honest.

### Mocked tools (boto3-shaped)

All 6 tools are `@tool`-decorated functions with typed Pydantic input schemas, backed by an in-memory fixture module seeded per `account_id` (same account → same data within a run; different accounts → different plausible data):

| Tool | Used by | boto3 analog | Notes |
|---|---|---|---|
| `get_cost_by_service(account_id)` | cost_analysis | Cost Explorer `get_cost_and_usage` | `ResultsByTime[].Groups[].{Keys, Metrics.UnblendedCost}` |
| `get_idle_resources(account_id)` | cost_analysis | (hypothetical, cross-service aggregation) | `{"IdleResources":[{ResourceId, ResourceType, State, Region, MonthlyCostUsd, IdleReason}]}`; a reserved "clean account" id returns an empty list to exercise the skip branch |
| `estimate_savings(resources)` | cost_recommendation | Compute Optimizer-style | `{"SavingsEstimates":[...], "TotalEstimatedMonthlySavingsUsd": ...}` |
| `check_public_s3_buckets(account_id)` | security_audit | `s3:GetBucketPolicyStatus` / `GetPublicAccessBlock` | `{"Buckets":[{Name, PolicyStatus.IsPublic, PublicAccessBlockConfiguration, Severity}]}` |
| `check_overpermissioned_iam_roles(account_id)` | security_audit | `iam:list_roles` / `get_role_policy` | `{"Roles":[{RoleName, Arn, AttachedPolicies, OverPermissioned, Severity}]}` |
| `generate_remediation_plan(findings)` | security_remediation | (hypothetical) | `{"RemediationSteps":[{FindingId, Action, Priority}]}` |

A reserved fault-injection `account_id` (documented as `999999999999`) deterministically makes tools return a malformed payload, used to exercise error handling in the demo without random flakiness.

### Error handling

Two layers, so the graph never crashes:
1. **Tool level** — each mocked tool catches its own injected/expected faults and *returns* a structured error dict rather than raising, so the ReAct agent sees it as a normal (if degraded) tool result and can reason about/report it.
2. **Framework level** — `create_agent`'s default `ToolNode` behavior auto-catches genuinely unexpected tool exceptions and feeds a `ToolMessage` back to the model instead of propagating.
3. **Wrapper-node level** — every agent-node wrapper function wraps its `.invoke()` + `structured_response` extraction in try/except. Any failure (missing/malformed structured output, unexpected exception) is routed through a shared `_degrade(node_name, exc, state)` helper that logs the error and returns a safe partial state update: `error`, `error_node`, a graceful `final_response`, and **zeroed count fields** — so downstream conditional routers always resolve deterministically to `END` instead of `KeyError`ing on a missing field.

### Observability

Structured logging only (no LangSmith requirement for this build). `logging_setup.py` configures a JSON-line formatter on a single logger, and `log_decision(component, event, detail)` is called at every decision point: supervisor classification, every router's routing decision, every agent node's `invoking_subagent` / `completed` / `error`, and every tool call's args/output/duration. A reviewer can reconstruct the exact path the graph took — classification → route → tool calls → route → final response — purely by reading the log file, without touching code.

### Design trade-offs

- **Structured output for routing, not text parsing.** Every agent's `response_format` Pydantic schema gives routers typed fields to branch on. The alternative — parsing the agent's final message text for keywords/counts — is brittle and exactly what the task explicitly disallows ("not hidden inside a node's prompt").
- **Supervisor is a bare structured-output call, not a ReAct agent.** It doesn't call tools, so `create_agent` would be overkill; `init_chat_model(...).with_structured_output(SupervisorDecision)` is cheaper and simpler.
- **Subgraph invocation via wrapper functions, not nested compiled graphs.** Each `create_agent` has its own `AgentState` shape, different from `CloudOpsState`; LangGraph only lets you nest a compiled graph directly as a node when state schemas share keys, which isn't the case here.
- **Flattened count fields duplicated from nested result objects**, purely so router functions stay simple, defensive one-liners rather than reaching into optional nested Pydantic objects.

### Known limitations (planned, not yet mitigated)

- Mocked data is boto3-*shaped*, not a byte-for-byte match to any single real AWS API (e.g. no single boto3 call returns cross-service idle resources) — the pattern transfers, but response shapes may need adjustment against a real SDK later.
- Single-turn per workflow: no loop back to the supervisor for follow-up questions within the same run.
- No checkpointer/persistence wired in — state is per-invocation only.
- No LangSmith tracing — structured logs are the only observability surface (per explicit scope decision, see below).

## Web frontend

A single-page query console served by FastAPI, in [cloud_assistant/web/](cloud_assistant/web/). It exists to make the routing visible: the same `graph.stream(..., stream_mode="updates")` call the demo uses to record the path taken yields one chunk per finished node, and the server forwards those chunks to the browser as server-sent events. Nodes light up as the graph reaches them, so **a node that stays dim is one a conditional edge skipped** — the clean account visibly stops after `cost_analysis` / `security_audit` rather than merely reporting that it did.

```powershell
.venv\Scripts\python -m cloud_assistant.web.server   # then open http://127.0.0.1:8000
```

The page offers the six sample requests from [demo/scenarios.py](cloud_assistant/demo/scenarios.py) as one-click chips and the four reserved accounts as a dropdown, then renders the typed results — idle-resource and savings tables for the cost workflow, findings and a prioritized remediation plan for the security one — plus the raw graph state for inspection.

| Endpoint | Purpose |
|---|---|
| `GET /` | the query console |
| `GET /api/config` | accounts, sample requests, node names — so the page hardcodes nothing Python already defines |
| `POST /api/query` | runs one request, streams `start` / `node` / `done` / `error` SSE frames |
| `GET /api/docs` | generated OpenAPI docs |

The graph is imported lazily on the first query, so the page still serves without an `OPENAI_API_KEY` and the missing key arrives as a readable message in the UI instead of a failed import at startup.

## Setup

```powershell
cd d:\cloud_assistant
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`OPENAI_API_KEY` and `CLOUD_ASSISTANT_MODEL` are read from the `.env` file in the project root (git-ignored) — no extra setup step needed.

## How to run the scenarios

```powershell
.venv\Scripts\python -m cloud_assistant.demo.run_demo
```

Runs the 5 required sample scenarios (plus a bonus error-injection scenario) through the compiled graph, prints the path taken for each, and writes transcripts to `transcripts/scenario_<n>_<slug>.json` plus a combined structured-log file — see [planning.md](planning.md) for the full scenario table and build order.

## Requirements

LLM provider: **OpenAI** (`langchain-openai`, model id `openai:gpt-4o-mini`), configured via `OPENAI_API_KEY`. See [requirements.txt](requirements.txt) for the full package list (LangChain/LangGraph 1.0+, Pydantic v2).
