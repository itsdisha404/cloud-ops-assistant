/* Query console client.
 *
 * Reads server-sent events from POST /api/query and re-renders from the state
 * accumulated so far, so every frame is a full redraw of what is known — no
 * incremental DOM patching to keep in sync with a partially finished graph run.
 */

const el = (id) => document.getElementById(id);
const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
const usd = (n) =>
  "$" + Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

let ACCOUNTS = [];
let running = false;

/* ── Bootstrap ──────────────────────────────────────────────── */

async function boot() {
  const cfg = await (await fetch("/api/config")).json();
  ACCOUNTS = cfg.accounts;

  el("meta").textContent = `model ${cfg.model} · mock date ${cfg.reference_date}`;

  el("account").innerHTML = cfg.accounts
    .map((a) => `<option value="${esc(a.id)}">${esc(a.id)} — ${esc(a.label)}</option>`)
    .join("");
  syncAccountNote();

  el("samples").innerHTML = cfg.samples
    .map(
      (s, i) =>
        `<button class="chip" data-i="${i}">` +
        `<span class="chip-slug">${esc(s.slug)}</span>${esc(s.request)}</button>`
    )
    .join("");

  el("samples").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const s = cfg.samples[Number(chip.dataset.i)];
    el("request").value = s.request;
    el("account").value = s.account_id;
    syncAccountNote();
  });

  el("account").addEventListener("change", syncAccountNote);
  el("run").addEventListener("click", run);
  el("request").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run();
  });
}

function syncAccountNote() {
  const account = ACCOUNTS.find((a) => a.id === el("account").value);
  el("account-note").textContent = account ? account.note : "";
}

/* ── Pipeline diagram ───────────────────────────────────────── */

function resetPipeline() {
  document.querySelectorAll(".node").forEach((n) => (n.className = n.classList.contains("end") ? "node end" : "node"));
  document.querySelectorAll(".connector").forEach((c) => (c.className = "connector"));
  el("path").textContent = "";
}

function mark(node, cls) {
  const target = document.querySelector(`.node[data-node="${node}"]`);
  if (!target) return;
  target.classList.remove("active");
  target.classList.add(cls);
}

function setActive(node) {
  document.querySelectorAll(".node.active").forEach((n) => n.classList.remove("active"));
  if (node) document.querySelector(`.node[data-node="${node}"]`)?.classList.add("active");
}

/* Mirrors the router functions in graph.py — for display only. The server is
 * still the authority: a node is drawn as visited only when its event arrives. */
function projectNext(node, update) {
  if (node === "supervisor") {
    return { cost: "cost_analysis", security: "security_audit" }[update.workflow] || "END";
  }
  if (node === "cost_analysis") {
    const taken = (update.idle_resource_count || 0) >= 1;
    el("pipeline").querySelector('[data-connector="cost"]').classList.add(taken ? "taken" : "skipped");
    return taken ? "cost_recommendation" : "END";
  }
  if (node === "security_audit") {
    const taken = (update.security_finding_count || 0) >= 1;
    el("pipeline").querySelector('[data-connector="security"]').classList.add(taken ? "taken" : "skipped");
    return taken ? "security_remediation" : "END";
  }
  return "END";
}

function setStatus(text, cls) {
  el("status").textContent = text;
  el("status").className = `status ${cls}`;
}

/* ── Run ────────────────────────────────────────────────────── */

async function run() {
  if (running) return;
  const request = el("request").value.trim();
  if (!request) {
    el("request").focus();
    return;
  }

  running = true;
  el("run").disabled = true;
  resetPipeline();
  setStatus("running", "running");
  setActive("supervisor");

  const state = {};
  el("output").innerHTML = '<p class="empty">Classifying the request…</p>';

  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request, account_id: el("account").value }),
    });

    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`HTTP ${res.status}: ${detail.slice(0, 300)}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line; the tail may be a partial frame.
      const frames = buffer.split("\n\n");
      buffer = frames.pop();
      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (line) handleEvent(JSON.parse(line.slice(6)), state);
      }
    }
  } catch (err) {
    setStatus("failed", "failed");
    el("output").innerHTML = card("failure", "Request failed", `<p class="answer-text">${esc(err.message)}</p>`);
  } finally {
    running = false;
    el("run").disabled = false;
    setActive(null);
  }
}

function handleEvent(event, state) {
  if (event.type === "node") {
    Object.assign(state, event.update);
    mark(event.node, event.update.error_node === event.node ? "errored" : "visited");
    setActive(projectNext(event.node, event.update));
    el("path").textContent = "path: " + [...event.path, "END"].join(" → ");
    render(state);
  } else if (event.type === "done") {
    mark("END", "visited");
    setActive(null);
    setStatus(state.error ? "degraded" : "done", state.error ? "failed" : "done");
    el("path").textContent = "path: " + [...event.path, "END"].join(" → ");
    render(state);
  } else if (event.type === "error") {
    setStatus("failed", "failed");
    setActive(null);
    el("output").innerHTML = card("failure", "Run failed", `<p class="answer-text">${esc(event.message)}</p>`);
  }
}

/* ── Rendering ──────────────────────────────────────────────── */

function card(cls, title, body) {
  return `<section class="card ${cls}"><h3>${title}</h3>${body}</section>`;
}

function badge(value, cls) {
  return `<span class="badge ${cls || String(value).toLowerCase()}">${esc(value)}</span>`;
}

function stat(label, value, cls) {
  return `<div><span class="stat-label">${esc(label)}</span><span class="stat-value ${cls || ""}">${esc(value)}</span></div>`;
}

function table(headers, rows) {
  return (
    '<div class="table-wrap"><table><thead><tr>' +
    headers.map((h) => `<th>${esc(h)}</th>`).join("") +
    "</tr></thead><tbody>" +
    rows.map((r) => `<tr>${r.join("")}</tr>`).join("") +
    "</tbody></table></div>"
  );
}

function render(state) {
  const out = [];

  if (state.workflow) {
    out.push(
      card(
        "",
        `Classification ${badge(state.workflow)}` +
          `<span class="badge low">confidence ${Number(state.supervisor_confidence || 0).toFixed(2)}</span>`,
        `<p class="rationale">${esc(state.supervisor_rationale || "")}</p>`
      )
    );
  }

  if (state.error) {
    out.push(
      card(
        "failure",
        `Degraded at ${esc(state.error_node || "unknown")}`,
        `<p class="rationale">${esc(state.error)}</p>`
      )
    );
  }

  if (state.final_response) {
    out.push(card("answer", "Answer", `<p class="answer-text">${esc(state.final_response)}</p>`));
  }

  if (state.cost_analysis_result) out.push(renderCostAnalysis(state.cost_analysis_result));
  if (state.cost_recommendation_result) out.push(renderCostRecommendation(state.cost_recommendation_result));
  if (state.security_audit_result) out.push(renderSecurityAudit(state.security_audit_result));
  if (state.security_remediation_result) out.push(renderRemediation(state.security_remediation_result));

  out.push(
    '<details class="raw"><summary>Raw graph state</summary>' +
      `<pre>${esc(JSON.stringify(state, null, 2))}</pre></details>`
  );

  el("output").innerHTML = out.join("");
}

function renderCostAnalysis(r) {
  const rows = r.idle_resources.map((f) => [
    `<td class="mono">${esc(f.resource_id)}</td>`,
    `<td class="mono">${esc(f.resource_type)}</td>`,
    `<td class="mono">${esc(f.region)}</td>`,
    `<td class="num">${usd(f.monthly_cost_usd)}</td>`,
    `<td>${esc(f.idle_reason)}</td>`,
  ]);

  const body =
    '<div class="stats">' +
    stat("Monthly spend", usd(r.total_monthly_spend_usd)) +
    stat("Idle resources", r.idle_resource_count) +
    stat("Top service", r.top_services[0] || "—", "text") +
    "</div>" +
    (rows.length
      ? table(["Resource", "Type", "Region", "Monthly cost", "Why it is idle"], rows)
      : '<p class="rationale">No idle resources — the recommendation node is skipped.</p>') +
    `<p class="summary">${esc(r.summary)}</p>`;

  return card("", `Cost analysis <span class="badge cost">${esc(r.account_id)}</span>`, body);
}

function renderCostRecommendation(r) {
  const rows = r.estimates.map((e) => [
    `<td class="mono">${esc(e.resource_id)}</td>`,
    `<td class="mono">${esc(e.action)}</td>`,
    `<td class="num">${usd(e.estimated_monthly_savings_usd)}</td>`,
    `<td>${badge(e.risk)}</td>`,
  ]);

  const body =
    '<div class="stats">' +
    stat("Estimated monthly savings", usd(r.total_estimated_monthly_savings_usd), "savings") +
    stat("Actions", r.prioritized_actions.length) +
    "</div>" +
    table(["Resource", "Action", "Monthly savings", "Risk"], rows) +
    '<ol class="actions" style="margin-top:12px">' +
    r.prioritized_actions.map((a) => `<li>${esc(a)}</li>`).join("") +
    "</ol>" +
    `<p class="summary">${esc(r.summary)}</p>`;

  return card("", "Savings recommendation", body);
}

function renderSecurityAudit(r) {
  const rows = r.findings.map((f) => [
    `<td class="mono">${esc(f.finding_id)}</td>`,
    `<td>${badge(f.severity)}</td>`,
    `<td class="mono">${esc(f.finding_type)}</td>`,
    `<td class="mono">${esc(f.resource_arn)}</td>`,
    `<td>${esc(f.description)}</td>`,
  ]);

  const body =
    '<div class="stats">' +
    stat("Findings", r.security_finding_count) +
    stat("Highest severity", r.highest_severity) +
    "</div>" +
    (rows.length
      ? table(["ID", "Severity", "Type", "Resource", "Detail"], rows)
      : '<p class="rationale">No findings — the remediation node is skipped.</p>') +
    `<p class="summary">${esc(r.summary)}</p>`;

  return card("", `Security audit <span class="badge security">${esc(r.account_id)}</span>`, body);
}

function renderRemediation(r) {
  const rows = r.steps.map((s) => [
    `<td>${badge(s.priority)}</td>`,
    `<td class="mono">${esc(s.finding_id)}</td>`,
    `<td>${esc(s.action)}</td>`,
    `<td>${esc(s.rationale)}</td>`,
  ]);

  return card(
    "",
    "Remediation plan",
    table(["Priority", "Finding", "Action", "Rationale"], rows) + `<p class="summary">${esc(r.summary)}</p>`
  );
}

boot();
