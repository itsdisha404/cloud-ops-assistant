# Implementation Plan — Cloud Operations Assistant

Detailed, stepwise build plan for the multi-agent LangGraph system architected in [README.md](README.md). Nothing below has been implemented yet. README explains *why* the design is what it is; this file specifies *what to write, in what order, and how to know each step is done*.

**Environment note:** the existing virtualenv in this repo is `.venv/` (not `venv/`). Use `.venv\Scripts\python.exe` everywhere; fix the README's `venv\Scripts\...` snippets in Step 14 to match, or rename the venv — but pick one and make both files agree.

**Conventions used by every step below**
- Python 3.11+, `from __future__ import annotations` at the top of every module.
- Absolute imports only, rooted at the `cloud_assistant` package (`from cloud_assistant.state import CloudOpsState`).
- Every public function gets a type-annotated signature and a one-line docstring. No bare `except:` — always `except Exception as exc`.
- Nothing outside `state.py` defines a Pydantic model or a TypedDict.

---

## Step 1 — Scaffold and dependencies

**Goal:** an importable, empty package skeleton with dependencies installed, so every later step can be run and tested in isolation.

**Create this tree:**

```
cloud_assistant/
  __init__.py
  config.py
  state.py
  logging_setup.py
  graph.py
  mock_data/__init__.py
  mock_data/fixtures.py
  mock_data/errors.py
  tools/__init__.py
  tools/cost_tools.py
  tools/savings_tools.py
  tools/security_tools.py
  tools/remediation_tools.py
  agents/__init__.py
  agents/_common.py
  agents/supervisor.py
  agents/cost_analysis.py
  agents/cost_recommendation.py
  agents/security_audit.py
  agents/security_remediation.py
  demo/__init__.py
  demo/scenarios.py
  demo/run_demo.py
transcripts/            # created at runtime, .gitkeep committed
.gitignore
.env                    # local only, git-ignored
```

**`.gitignore`:** `.venv/`, `venv/`, `.env`, `__pycache__/`, `*.pyc`, `*.log`, `transcripts/*.json`.
**`.env`:** holds `OPENAI_API_KEY` and `CLOUD_ASSISTANT_MODEL=openai:gpt-4o-mini`. It is never committed and there is no template/example file — the real key lives in `.env` only.

**Commands:**
```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -c "import langchain, langgraph, pydantic; print(langchain.__version__, langgraph.__version__, pydantic.VERSION)"
```

**Done when:** `.venv\Scripts\python.exe -c "import cloud_assistant"` exits 0, and the printed langchain/langgraph majors are both `1.x`. If either resolves to `0.x`, stop and fix `requirements.txt` before continuing — the entire design depends on `langchain.agents.create_agent` existing.

---

## Step 2 — `config.py`

**Goal:** one place that reads the environment, so no other module calls `os.getenv`.

**Contents:**
- `load_dotenv()` at import time (from `python-dotenv`), tolerant of a missing `.env`.
- `MODEL_ID: str = os.getenv("CLOUD_ASSISTANT_MODEL", "openai:gpt-4o-mini")`
- `MOCK_SEED: int = int(os.getenv("CLOUD_ASSISTANT_SEED", "1337"))` — base seed mixed with `account_id` in fixtures.
- `FAULT_ACCOUNT_ID: str = "999999999999"` — the reserved fault-injection account.
- `CLEAN_ACCOUNT_ID: str = "111111111111"` — the reserved "nothing wrong here" account that drives both skip branches.
- `LOG_PATH: Path = Path("transcripts/run.log")`, `TRANSCRIPT_DIR: Path = Path("transcripts")`.
- `def require_api_key() -> None:` raises `RuntimeError` with an actionable message if `OPENAI_API_KEY` is unset. Called by `run_demo.py`, never at import time (so tests/imports work without a key).

**Done when:** importing `config` with no `.env` present does not raise, and `require_api_key()` raises a clear error when the key is absent.

---

## Step 3 — `state.py` (build this before anything else that imports it)

**Goal:** the single source of truth for every typed structure in the system. Every other module imports from here; nothing here imports from anywhere else in the package.

**3a. Finding/result models (Pydantic v2 `BaseModel`).** Every field gets a `Field(description=...)` — those descriptions are what the LLM actually sees when the model is used as a `response_format`, so they are prompt engineering, not decoration.

```python
class CostFinding(BaseModel):
    resource_id: str
    resource_type: str           # "ec2-instance" | "ebs-volume" | "elastic-ip" | "rds-instance"
    region: str
    monthly_cost_usd: float
    idle_reason: str

class CostAnalysisResult(BaseModel):
    account_id: str
    total_monthly_spend_usd: float
    top_services: list[str]              # highest-spend service names, descending
    idle_resources: list[CostFinding]
    idle_resource_count: int             # MUST equal len(idle_resources)
    summary: str

class SavingsEstimate(BaseModel):
    resource_id: str
    action: str                          # "stop" | "delete" | "rightsize" | "release"
    estimated_monthly_savings_usd: float
    risk: Literal["low", "medium", "high"]

class CostRecommendationResult(BaseModel):
    estimates: list[SavingsEstimate]
    total_estimated_monthly_savings_usd: float
    prioritized_actions: list[str]       # human-readable, highest ROI first
    summary: str

class SecurityFinding(BaseModel):
    finding_id: str                      # stable id, e.g. "S3-PUBLIC-001"
    resource_arn: str
    finding_type: str                    # "public_s3_bucket" | "overpermissioned_iam_role"
    severity: Literal["low", "medium", "high", "critical"]
    description: str

class SecurityAuditResult(BaseModel):
    account_id: str
    findings: list[SecurityFinding]
    security_finding_count: int          # MUST equal len(findings)
    highest_severity: Literal["none", "low", "medium", "high", "critical"]
    summary: str

class RemediationStep(BaseModel):
    finding_id: str
    action: str
    priority: Literal["P0", "P1", "P2", "P3"]
    rationale: str

class SecurityRemediationResult(BaseModel):
    steps: list[RemediationStep]
    summary: str

class SupervisorDecision(BaseModel):
    workflow: Literal["cost", "security", "unclear"]
    account_id: str | None               # extracted from the request if present
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    clarification: str | None            # populated only when workflow == "unclear"
```

**3b. `CloudOpsState(TypedDict, total=False)`** — exactly the fields in the README table:
`messages: Annotated[list[AnyMessage], add_messages]`, `user_request: str`, `account_id: str`, `workflow`, `supervisor_rationale`, `supervisor_confidence`, `cost_analysis_result`, `idle_resource_count: int`, `cost_recommendation_result`, `security_audit_result`, `security_finding_count: int`, `security_remediation_result`, `final_response: str`, `error: str | None`, `error_node: str | None`.

**Design rules to honor here (from README):** the two `*_count` fields are flattened to the top level *on purpose* so router functions never dereference an optional nested object. Populating them is the wrapper node's job (Steps 9–10), not the LLM's alone — the wrapper recomputes them from `len(...)` and does not trust the model's own count field.

**Done when:** `.venv\Scripts\python.exe -c "from cloud_assistant.state import *; print(SupervisorDecision(workflow='cost', account_id=None, rationale='x', confidence=0.9, clarification=None))"` round-trips, and `CostAnalysisResult.model_json_schema()` renders without error (this is what gets sent to the model).

---

## Step 4 — `logging_setup.py`

**Goal:** a reviewer can reconstruct the full graph path from the log file alone.

**Implement:**
- `configure_logging(log_path: Path = config.LOG_PATH, level: int = logging.INFO) -> logging.Logger` — creates the parent dir, attaches **one** `FileHandler` and one `StreamHandler` to the logger named `cloud_assistant`, sets `logger.propagate = False`, and is **idempotent** (returns early if `logger.handlers` is non-empty, so repeated demo runs don't duplicate lines).
- A `JsonLineFormatter(logging.Formatter)` whose `format()` emits one compact JSON object per line: `{"ts", "level", "component", "event", "detail"}`, pulling `component`/`event`/`detail` off the record's `extra` dict with safe defaults, and `json.dumps(..., default=str)` so Pydantic/enum values never blow up the formatter.
- `log_decision(component: str, event: str, detail: dict | None = None) -> None` — the only logging call site used by the rest of the codebase.

**Event vocabulary to standardize on now** (used verbatim in later steps, keep the list short so logs are greppable): `classified`, `routing`, `invoking_subagent`, `subagent_completed`, `tool_call`, `tool_result`, `node_error`, `scenario_start`, `scenario_complete`.

**Done when:** a standalone smoke run
```powershell
.venv\Scripts\python.exe -c "from cloud_assistant.logging_setup import configure_logging, log_decision; configure_logging(); log_decision('smoke','tool_call',{'a':1})"
```
appends exactly one line to `transcripts/run.log` and that line parses with `json.loads`.

---

## Step 5 — `mock_data/fixtures.py` and `mock_data/errors.py`

**Goal:** deterministic, boto3-*shaped* fake data, keyed by `account_id`.

**`fixtures.py`:**
- `def _rng(account_id: str, salt: str) -> random.Random:` → `random.Random(f"{config.MOCK_SEED}:{account_id}:{salt}")`. A **local** `Random` instance, never the module-global `random`, so nothing else in the process perturbs results. The `salt` keeps `get_cost_by_service` and `get_idle_resources` from producing correlated draws off one stream.
- `cost_by_service(account_id) -> dict` — Cost Explorer shape:
  `{"ResultsByTime":[{"TimePeriod":{"Start","End"},"Groups":[{"Keys":["Amazon Elastic Compute Cloud - Compute"],"Metrics":{"UnblendedCost":{"Amount":"1234.56","Unit":"USD"}}}, ...]}]}`
  Amounts are **strings**, as real Cost Explorer returns them — this is deliberate, it forces the agent/tool boundary to do the same coercion real code would.
- `idle_resources(account_id) -> dict` — `{"IdleResources":[{"ResourceId","ResourceType","State","Region","MonthlyCostUsd","IdleReason"}]}`. Returns `{"IdleResources": []}` for `config.CLEAN_ACCOUNT_ID`. Otherwise 2–5 resources drawn from a fixed catalog of plausible types/reasons.
- `public_buckets(account_id) -> dict` — `{"Buckets":[{"Name","PolicyStatus":{"IsPublic":bool},"PublicAccessBlockConfiguration":{...4 booleans...},"Severity"}]}`. Clean account → only non-public buckets.
- `iam_roles(account_id) -> dict` — `{"Roles":[{"RoleName","Arn","AttachedPolicies":[{"PolicyName","PolicyArn"}],"OverPermissioned":bool,"Severity"}]}`. Clean account → no `OverPermissioned: true` roles.
- Region/service/policy name catalogs live as module-level constants at the top of the file.

**`errors.py`:**
- `class MockFaultError(RuntimeError)` — used only for the "unexpected exception" path.
- `def maybe_inject_fault(account_id: str, tool_name: str) -> dict | None:` — returns a **malformed payload** (e.g. `{"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}`, or a structurally wrong `{"ResultsByTime": None}`) when `account_id == config.FAULT_ACCOUNT_ID`, else `None`. Deterministic per `tool_name` so the same tool always fails the same way — no random flakiness in the demo.

**Done when:**
```powershell
.venv\Scripts\python.exe -c "from cloud_assistant.mock_data import fixtures as f; a=f.idle_resources('222222222222'); b=f.idle_resources('222222222222'); c=f.idle_resources('333333333333'); print(a==b, a!=c, f.idle_resources('111111111111'))"
```
prints `True True {'IdleResources': []}`.

---

## Step 6 — Cost-side tools (`tools/cost_tools.py`, `tools/savings_tools.py`)

**Goal:** three `@tool`-decorated callables the cost agents can bind.

**Shared pattern for all 6 tools (Steps 6 and 7) — write it once and repeat:**

```python
class GetCostByServiceArgs(BaseModel):
    account_id: str = Field(description="12-digit AWS account id to analyze")

@tool("get_cost_by_service", args_schema=GetCostByServiceArgs)
def get_cost_by_service(account_id: str) -> dict:
    """Return month-to-date cost grouped by AWS service for the account."""
    log_decision("tool", "tool_call", {"tool": "get_cost_by_service", "account_id": account_id})
    started = time.perf_counter()
    try:
        fault = maybe_inject_fault(account_id, "get_cost_by_service")
        payload = fault if fault is not None else fixtures.cost_by_service(account_id)
    except Exception as exc:                       # never raise out of a tool
        payload = {"Error": {"Code": type(exc).__name__, "Message": str(exc)}}
    log_decision("tool", "tool_result", {"tool": "get_cost_by_service",
                                         "duration_ms": round((time.perf_counter()-started)*1000, 1),
                                         "ok": "Error" not in payload})
    return payload
```

The `Field(description=...)` text and the docstring are the tool's contract with the model — write them as instructions, not as notes to a human.

**`cost_tools.py`:** `get_cost_by_service(account_id)`, `get_idle_resources(account_id)`.
**`savings_tools.py`:** `estimate_savings(resources: list[dict])` — args schema `EstimateSavingsArgs(resources: list[IdleResourceInput])` where `IdleResourceInput` mirrors the `IdleResources[]` item shape. Computes a savings estimate per resource with a fixed action→multiplier table (`stop`→1.0, `rightsize`→0.4, `delete`→1.0, `release`→1.0 of `MonthlyCostUsd`) plus a `risk` label, and returns `{"SavingsEstimates":[...], "TotalEstimatedMonthlySavingsUsd": <float>}`. Deterministic arithmetic, no RNG — the recommendation agent's job is prioritization, not invention.

**Done when:** each tool is invocable directly (`get_cost_by_service.invoke({"account_id": "222222222222"})`), returns a dict, and `get_cost_by_service.invoke({"account_id": "999999999999"})` returns an `Error` dict **without raising**.

---

## Step 7 — Security-side tools (`tools/security_tools.py`, `tools/remediation_tools.py`)

Same pattern as Step 6.

**`security_tools.py`:**
- `check_public_s3_buckets(account_id)` → `fixtures.public_buckets`, fault-injected.
- `check_overpermissioned_iam_roles(account_id)` → `fixtures.iam_roles`, fault-injected.

**`remediation_tools.py`:**
- `generate_remediation_plan(findings: list[dict])` — args schema mirrors `SecurityFinding` fields (`finding_id`, `finding_type`, `severity`, `resource_arn`). Maps each finding to a concrete action from a fixed lookup table (`public_s3_bucket` → "Enable PublicAccessBlock and remove the public bucket policy statement"; `overpermissioned_iam_role` → "Replace the wildcard policy with a least-privilege inline policy scoped to the role's observed actions"), and derives `Priority` from severity (`critical`→P0, `high`→P1, `medium`→P2, `low`→P3). Returns `{"RemediationSteps":[{"FindingId","Action","Priority"}]}`.

**Done when:** all four tools return well-formed dicts for a normal account, `Error` dicts for `999999999999`, and empty/clean results for `111111111111`.

---

## Step 8 — `agents/_common.py` (build before any agent node)

**Goal:** one graceful-degradation helper and one sub-agent invocation helper shared by all four agent nodes.

**`_degrade(node_name: str, exc: Exception, state: CloudOpsState) -> dict`**
- Logs `log_decision(node_name, "node_error", {"error": str(exc), "type": type(exc).__name__})`.
- Returns a **partial state update** — never a full state — containing:
  `{"error": str(exc), "error_node": node_name, "final_response": <user-facing apology naming the failed stage>, "idle_resource_count": 0, "security_finding_count": 0}`.
- Zeroing **both** count fields is the critical bit: it guarantees every downstream conditional router resolves deterministically to `END` instead of `KeyError`ing or routing into a node whose inputs don't exist.

**`invoke_subagent(agent, node_name: str, prompt: str, result_model: type[BaseModel]) -> BaseModel`**
- Logs `invoking_subagent`, calls `agent.invoke({"messages": [HumanMessage(content=prompt)]})`.
- Pulls `result["structured_response"]`; raises `ValueError(f"{node_name}: agent returned no structured_response")` if missing or not an instance of `result_model`.
- Logs `subagent_completed` with a small detail dict (never the full transcript — keep log lines readable).
- Returns the validated Pydantic object. Callers wrap this in `try/except Exception as exc: return _degrade(...)`.

**`build_model()`** — thin wrapper over `init_chat_model(config.MODEL_ID, temperature=0)`, so temperature/model policy lives in exactly one place.

**Done when:** `_degrade("test", ValueError("boom"), {})` returns a dict containing all five keys and writes one `node_error` log line.

---

## Step 9 — `agents/supervisor.py`

**Goal:** classify the request and produce a routing decision as typed data, never as prose.

**Implement:**
- `SUPERVISOR_PROMPT` — a module-level constant. Must state: you are a router; classify into exactly `cost` | `security` | `unclear`; `cost` covers spend/waste/idle/billing/savings; `security` covers public buckets, IAM permissions, compliance, exposure; use `unclear` for anything ambiguous, mixed, or out of scope, and put a specific follow-up question in `clarification`; extract a 12-digit account id if the request contains one, else `null`. Include 2–3 few-shot examples inline, one of which is deliberately ambiguous.
- `classify(user_request: str) -> SupervisorDecision` — `build_model().with_structured_output(SupervisorDecision)` invoked with the system prompt + the request. **Not** `create_agent` — the supervisor calls no tools (README trade-off #2).
- `supervisor_node(state: CloudOpsState) -> dict`:
  1. `user_request = state["user_request"]` (fall back to the last human message in `state["messages"]`).
  2. `decision = classify(user_request)`, wrapped in try/except → on failure return `_degrade("supervisor", exc, state)` **plus** `{"workflow": "unclear"}` so the router still has a value.
  3. `log_decision("supervisor", "classified", {"workflow", "confidence", "rationale", "account_id"})`.
  4. Resolve the account: `decision.account_id or state.get("account_id") or DEFAULT_ACCOUNT_ID`.
  5. Return `{"workflow", "supervisor_rationale", "supervisor_confidence", "account_id"}`, and when `workflow == "unclear"` also `{"final_response": decision.clarification or <generic ask>}` — because the `unclear` branch goes straight to `END` and nothing downstream will ever set `final_response`.

**Done when:** with a live key, `classify("why is my AWS bill so high?")` → `cost`; `classify("are any of my S3 buckets public?")` → `security`; `classify("can you help me with my cloud stuff?")` → `unclear` with a non-empty `clarification`.

---

## Step 10 — Workflow 1 nodes (`agents/cost_analysis.py`, `agents/cost_recommendation.py`)

Each file follows the identical three-part shape: prompt constant → `create_agent(...)` at module level → wrapper node function.

**`cost_analysis.py`**
```python
cost_analysis_agent = create_agent(
    model=build_model(),
    tools=[get_cost_by_service, get_idle_resources],
    system_prompt=COST_ANALYSIS_PROMPT,
    response_format=CostAnalysisResult,
)
```
- `COST_ANALYSIS_PROMPT`: call **both** tools before answering; if a tool returns an `Error` key, say so in `summary` and report what you could gather rather than inventing numbers; `idle_resource_count` must equal the length of `idle_resources`; cost amounts arrive as strings, coerce to float.
- `cost_analysis_node(state) -> dict`: build a prompt embedding `state["account_id"]` and `state["user_request"]`, call `invoke_subagent(...)`, then return
  `{"cost_analysis_result": result, "idle_resource_count": len(result.idle_resources), "final_response": result.summary, "messages": [AIMessage(content=result.summary)]}`.
  **Recompute the count from `len(...)`** — do not trust `result.idle_resource_count`, since routing correctness must not depend on the model's arithmetic. Wrap in `try/except → _degrade("cost_analysis", exc, state)`.
  Setting `final_response` here matters: if the router then skips to `END` (no idle resources), this is the answer the user gets.

**`cost_recommendation.py`**
- Agent: tools `[estimate_savings]`, `response_format=CostRecommendationResult`.
- `COST_RECOMMENDATION_PROMPT`: you are given already-identified idle resources; call `estimate_savings` **once** with all of them; order `prioritized_actions` by savings descending, breaking ties toward lower risk.
- `cost_recommendation_node(state)`: serialize `state["cost_analysis_result"].idle_resources` into the prompt as JSON; return `{"cost_recommendation_result": result, "final_response": <analysis summary + "\n\n" + recommendation summary>}` so the terminal answer covers the whole workflow, not just the last hop.

**Done when:** invoked with a hand-built state (`{"account_id": "222222222222", "user_request": "..."}`), `cost_analysis_node` returns a populated `CostAnalysisResult` and an `idle_resource_count` matching the fixture's list length; the same call against `111111111111` returns count `0`.

---

## Step 11 — Workflow 2 nodes (`agents/security_audit.py`, `agents/security_remediation.py`)

Mirror image of Step 10.

**`security_audit.py`** — tools `[check_public_s3_buckets, check_overpermissioned_iam_roles]`, `response_format=SecurityAuditResult`. Prompt: call both tools; emit one `SecurityFinding` per *actually risky* item only (a bucket with `IsPublic: false` is **not** a finding — this is the discipline that makes the clean-account skip branch real rather than accidental); assign stable `finding_id`s of the form `S3-PUBLIC-<n>` / `IAM-PERM-<n>`; set `highest_severity` to `"none"` when there are no findings.
`security_audit_node`: returns `{"security_audit_result", "security_finding_count": len(result.findings), "final_response": result.summary, "messages": [...]}` — count recomputed from `len(...)`, same reasoning as Step 10.

**`security_remediation.py`** — tools `[generate_remediation_plan]`, `response_format=SecurityRemediationResult`. Prompt: call the tool once with all findings; keep `steps` ordered P0→P3; every step's `rationale` must reference its finding's severity. Node returns the concatenated audit + remediation summary as `final_response`.

**Done when:** `security_audit_node` on `111111111111` yields `security_finding_count == 0` and `highest_severity == "none"`; on a normal account it yields ≥1 finding with a valid severity.

---

## Step 12 — `graph.py`

**Goal:** assemble the five nodes and three conditional edges; this module imports every node, so it is built last among source files.

**Router functions (plain, defensive, no LLM calls — the branching lives here, in the graph, not in a prompt):**
```python
def route_from_supervisor(state: CloudOpsState) -> str:
    decision = state.get("workflow") or "unclear"
    log_decision("router", "routing", {"from": "supervisor", "to": decision})
    return decision

def route_after_cost_analysis(state: CloudOpsState) -> str:
    nxt = "recommend" if state.get("idle_resource_count", 0) >= 1 else "end"
    log_decision("router", "routing", {"from": "cost_analysis", "to": nxt,
                                       "idle_resource_count": state.get("idle_resource_count", 0)})
    return nxt

def route_after_security_audit(state: CloudOpsState) -> str:
    nxt = "remediate" if state.get("security_finding_count", 0) >= 1 else "end"
    log_decision("router", "routing", {"from": "security_audit", "to": nxt,
                                       "security_finding_count": state.get("security_finding_count", 0)})
    return nxt
```
Every `.get()` carries a default so a degraded node can never produce a `KeyError` at a routing boundary.

**Assembly (`build_graph() -> CompiledStateGraph`):**
```python
g = StateGraph(CloudOpsState)
g.add_node("supervisor", supervisor_node)
g.add_node("cost_analysis", cost_analysis_node)
g.add_node("cost_recommendation", cost_recommendation_node)
g.add_node("security_audit", security_audit_node)
g.add_node("security_remediation", security_remediation_node)

g.add_edge(START, "supervisor")
g.add_conditional_edges("supervisor", route_from_supervisor,
                        {"cost": "cost_analysis", "security": "security_audit", "unclear": END})
g.add_conditional_edges("cost_analysis", route_after_cost_analysis,
                        {"recommend": "cost_recommendation", "end": END})
g.add_edge("cost_recommendation", END)
g.add_conditional_edges("security_audit", route_after_security_audit,
                        {"remediate": "security_remediation", "end": END})
g.add_edge("security_remediation", END)
return g.compile()
```
Note the supervisor's **single** 3-way `add_conditional_edges` with `END` in the `path_map` — the intentional resolution of the underspecified edge list documented in the README. Expose a module-level `graph = build_graph()` for convenience, but keep `build_graph()` callable so tests can rebuild it.

**Done when:** `build_graph()` compiles without error and `graph.get_graph().draw_ascii()` shows all five nodes with the three branch points.

---

## Step 13 — `demo/scenarios.py` and `demo/run_demo.py`

**`scenarios.py`** — a list of typed dicts, each `{"id", "slug", "request", "account_id", "expected_path": [...]}`. `expected_path` is what makes this an acceptance test rather than a demo:

| # | slug | Request | Account | Expected path |
|---|---|---|---|---|
| 1 | cost-full | "Our AWS bill jumped 30% this month — find the waste and tell me what to shut off." | `222222222222` | supervisor → cost_analysis → cost_recommendation → END |
| 2 | cost-clean | "Review this account for wasted spend." | `111111111111` (clean) | supervisor → cost_analysis → END |
| 3 | security-full | "Audit this account for public S3 buckets and over-permissioned IAM roles." | `333333333333` | supervisor → security_audit → security_remediation → END |
| 4 | security-clean | "Run a compliance check on this account." | `111111111111` (clean) | supervisor → security_audit → END |
| 5 | ambiguous | "Can you take a look at my cloud setup?" | `222222222222` | supervisor → END (clarification, neither workflow touched) |
| 6 | fault-injection (bonus) | "Find idle resources and estimate savings." | `999999999999` (fault) | supervisor → cost_analysis → END, `error` set, no crash |

**`run_demo.py`:**
1. `config.require_api_key()`, `configure_logging()`, `build_graph()`.
2. For each scenario: `log_decision("demo", "scenario_start", ...)`, then invoke the graph with a **`stream_mode="updates"` stream** rather than a plain `.invoke()` — accumulating the node names yielded gives you the actual path taken for free, which is exactly what needs to be compared against `expected_path`.
3. Print a per-scenario block: request, classification + confidence, actual path, whether it matched `expected_path` (`PASS`/`FAIL`), and the `final_response`.
4. Write `transcripts/scenario_<id>_<slug>.json` containing `{scenario, expected_path, actual_path, passed, workflow, supervisor_rationale, supervisor_confidence, results:{...}, final_response, error}`, serializing Pydantic objects via `model_dump(mode="json")`.
5. Print a closing summary table and exit non-zero if any scenario's actual path diverged from expected — so this doubles as CI.

**Done when:** the file runs end-to-end and produces six transcripts plus one `run.log`.

---

## Step 14 — Run, verify, and reconcile the docs

```powershell
.venv\Scripts\python.exe -m cloud_assistant.demo.run_demo
```

**Verify each claim explicitly, don't just eyeball the output:**
- All 6 scenarios report `PASS`; process exit code is 0.
- Scenarios 2 and 4 (clean account) show the downstream node **absent** from `actual_path` — the skip branches are genuinely skipped, not just quiet.
- Scenario 5 touches neither workflow node and still returns a non-empty clarification in `final_response`.
- Scenario 6 sets `error` / `error_node` and returns a graceful message; nothing raises.
- `transcripts/run.log`: every line parses as JSON, and grepping `"event":"routing"` alone reconstructs each scenario's path (the README's observability claim — if it doesn't hold, add the missing `log_decision` calls rather than weakening the claim).

**Then reconcile [README.md](README.md) against the as-built system:** flip the "architecture + plan only" status banner, fix the `venv\Scripts` → `.venv\Scripts` paths, and correct anything that drifted during implementation (tool shapes, state fields, scenario table). If a README design claim turned out to be wrong in practice, change the README to match reality and note why — don't leave an aspirational description standing.

---

## Step 15 — Cleanup

- Confirm `.gitignore` actually excludes `.env`, `.venv/`, `*.log`; confirm no key is present in any committed file (`git grep -i "sk-"`).
- Keep at least 3 representative transcripts as deliverable evidence (recommended: one full path, one skip path, one fault-injection) — commit those explicitly, since `transcripts/*.json` is otherwise gitignored.
- Delete dead scaffolding, verify a clean-clone install works from `requirements.txt` alone.

---

## Build-order dependencies

Not arbitrary — these are hard import-order constraints:
- `state.py` → before everything (all modules import its schemas).
- `config.py` and `logging_setup.py` → before tools and agents (both are imported by them).
- `mock_data/` → before `tools/` (tools are thin wrappers over fixtures).
- `agents/_common.py` → before the four agent nodes (all four call `_degrade` and `invoke_subagent`).
- `graph.py` → last among source files (imports every node).
- `demo/` → last overall (imports the compiled graph).

## Invariants to preserve throughout

1. **Branching lives in the graph, never in a prompt.** Router functions read flat integer state fields; no LLM decides an edge except the supervisor, and it does so via typed `SupervisorDecision`, not prose.
2. **Counts are recomputed by wrapper nodes from `len(...)`**, never trusted from model output.
3. **Tools return error dicts; they do not raise.** Wrapper nodes catch everything else via `_degrade`, which zeroes both count fields so routing stays deterministic under failure.
4. **`final_response` is set by every node that could be terminal** — otherwise a skip branch ends with an empty answer.
