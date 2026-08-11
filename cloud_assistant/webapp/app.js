// Shared helpers used by both index.html (run.js) and logs.html (history.js).
// No build step, no framework — this is a small test harness for the graph,
// not a product.

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function fmtMoney(n) {
  return typeof n === "number" ? `$${n.toFixed(2)}` : "—";
}

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data;
}

function workflowBadgeClass(workflow) {
  if (workflow === "cost") return "badge cost";
  if (workflow === "security") return "badge security";
  return "badge unclear";
}

function renderRoute(path, workflow) {
  path = path || [];
  let seq;
  let secClass = "";
  if (workflow === "cost") {
    seq = ["supervisor", "cost_analysis", "cost_recommendation"];
  } else if (workflow === "security") {
    seq = ["supervisor", "security_audit", "security_remediation"];
    secClass = " sec";
  } else {
    seq = ["supervisor"];
  }
  const parts = seq.map((n) => {
    const on = path.includes(n);
    const cls = on ? `node on${secClass}` : "node skip";
    return `<span class="${cls}">${n}</span>`;
  });
  parts.push('<span class="node end on">END</span>');
  return `<div class="route">${parts.join('<span class="arrow">→</span>')}</div>`;
}

function renderChips(label, items) {
  if (!items || items.length === 0) return "";
  const chips = items.map((i) => `<span class="chip">${escapeHtml(i)}</span>`).join("");
  return `<div class="chip-label">${escapeHtml(label)}</div><div class="chip-row">${chips}</div>`;
}

function sevSpan(sev) {
  const s = (sev || "none").toLowerCase();
  return `<span class="sev ${escapeHtml(s)}">${escapeHtml(s)}</span>`;
}

function renderResultTables(results) {
  if (!results) return "";
  let html = "";

  const car = results.cost_analysis_result;
  if (car && car.idle_resources && car.idle_resources.length) {
    html += `<div class="chip-label">idle resources (${car.idle_resources.length})</div>`;
    html += `<div class="scroll-x"><table class="data"><thead><tr>
      <th>resource</th><th>type</th><th>region</th><th>reason</th><th>monthly cost</th>
      </tr></thead><tbody>`;
    for (const r of car.idle_resources) {
      html += `<tr><td class="mono">${escapeHtml(r.resource_id)}</td><td>${escapeHtml(r.resource_type)}</td>
        <td>${escapeHtml(r.region)}</td><td>${escapeHtml(r.idle_reason)}</td>
        <td class="num">${fmtMoney(r.monthly_cost_usd)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  const crr = results.cost_recommendation_result;
  if (crr && crr.estimates && crr.estimates.length) {
    html += `<div class="chip-label">savings estimates — total ${fmtMoney(crr.total_estimated_monthly_savings_usd)}/mo</div>`;
    html += `<div class="scroll-x"><table class="data"><thead><tr>
      <th>resource</th><th>action</th><th>risk</th><th>savings</th>
      </tr></thead><tbody>`;
    for (const e of crr.estimates) {
      html += `<tr><td class="mono">${escapeHtml(e.resource_id)}</td><td>${escapeHtml(e.action)}</td>
        <td>${escapeHtml(e.risk)}</td><td class="num">${fmtMoney(e.estimated_monthly_savings_usd)}/mo</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  const sar = results.security_audit_result;
  if (sar && sar.findings && sar.findings.length) {
    html += `<div class="chip-label">findings (${sar.findings.length}) — highest severity ${escapeHtml(sar.highest_severity)}</div>`;
    html += `<div class="scroll-x"><table class="data"><thead><tr>
      <th>finding</th><th>type</th><th>severity</th><th>description</th>
      </tr></thead><tbody>`;
    for (const f of sar.findings) {
      html += `<tr><td class="mono">${escapeHtml(f.finding_id)}</td><td>${escapeHtml(f.finding_type)}</td>
        <td>${sevSpan(f.severity)}</td><td>${escapeHtml(f.description)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  const srr = results.security_remediation_result;
  if (srr && srr.steps && srr.steps.length) {
    html += `<div class="chip-label">remediation plan (${srr.steps.length} steps)</div>`;
    html += `<div class="scroll-x"><table class="data"><thead><tr>
      <th>finding</th><th>priority</th><th>action</th><th>rationale</th>
      </tr></thead><tbody>`;
    for (const s of srr.steps) {
      html += `<tr><td class="mono">${escapeHtml(s.finding_id)}</td><td class="mono">${escapeHtml(s.priority)}</td>
        <td>${escapeHtml(s.action)}</td><td>${escapeHtml(s.rationale)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  return html;
}

function renderRunDetail(record) {
  const workflow = record.workflow || "unclear";
  const confidence = typeof record.supervisor_confidence === "number"
    ? `${Math.round(record.supervisor_confidence * 100)}%` : "—";

  let html = `<div class="result-head">
    <span class="${workflowBadgeClass(workflow)}">${escapeHtml(workflow)}</span>
    <span class="conf">confidence ${confidence}</span>
    ${record.account_id ? `<span class="conf">account <span class="mono">${escapeHtml(record.account_id)}</span></span>` : ""}
    ${record.error ? `<span class="badge error">degraded at ${escapeHtml(record.error_node)}</span>` : `<span class="badge ok">completed</span>`}
  </div>`;

  if (record.supervisor_rationale) {
    html += `<p class="rationale">“${escapeHtml(record.supervisor_rationale)}”</p>`;
  }

  html += renderRoute(record.path, workflow);
  html += renderChips("agents called", record.agents_called);
  html += renderChips("tools called", record.tools_called);
  html += renderResultTables(record.results);

  if (record.final_response) {
    html += `<div class="terminal">${escapeHtml(record.final_response)}</div>`;
  }
  if (record.error) {
    html += `<div class="error-box"><b>${escapeHtml(record.error_node || "error")}:</b> ${escapeHtml(record.error)}</div>`;
  }

  html += `<details class="raw"><summary>raw run record (decision log + full structured output)</summary>
    <pre>${escapeHtml(JSON.stringify(record, null, 2))}</pre></details>`;

  return html;
}
