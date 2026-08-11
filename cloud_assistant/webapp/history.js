const tableEl = document.getElementById("runs-table");
const detailCard = document.getElementById("detail-card");
const detailTitle = document.getElementById("detail-title");
const detailBody = document.getElementById("detail-body");

function renderTable(runs) {
  if (!runs.length) {
    tableEl.innerHTML = `<p class="empty">No runs yet — go to the Run page and try a request.</p>`;
    return;
  }
  const rows = runs.map((r) => {
    const status = r.error ? `<span class="badge error">error</span>` : `<span class="badge ok">ok</span>`;
    return `<tr class="run-row" data-run-id="${escapeHtml(r.run_id)}">
      <td class="mono">${escapeHtml((r.started_at || "").replace("T", " ").slice(0, 19))}</td>
      <td class="req-cell">${escapeHtml(r.request)}</td>
      <td>${r.workflow ? `<span class="${workflowBadgeClass(r.workflow)}">${escapeHtml(r.workflow)}</span>` : "—"}</td>
      <td class="mono">${escapeHtml((r.path || []).join(" → ") || "—")}</td>
      <td class="mono">${escapeHtml((r.agents_called || []).join(", ") || "—")}</td>
      <td class="mono">${escapeHtml((r.tools_called || []).join(", ") || "—")}</td>
      <td>${status}</td>
    </tr>`;
  }).join("");

  tableEl.innerHTML = `<div class="scroll-x"><table class="runs">
    <thead><tr>
      <th>time (utc)</th><th>request</th><th>workflow</th><th>path</th>
      <th>agents called</th><th>tools called</th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;

  tableEl.querySelectorAll(".run-row").forEach((row) => {
    row.addEventListener("click", () => selectRun(row.dataset.runId));
  });
}

async function selectRun(runId) {
  tableEl.querySelectorAll(".run-row").forEach((row) => {
    row.classList.toggle("selected", row.dataset.runId === runId);
  });
  detailCard.style.display = "block";
  detailTitle.textContent = `Run detail — ${runId}`;
  detailBody.innerHTML = `<p class="loading">Loading…</p>`;
  detailBody.scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    const record = await apiGet(`/api/runs/${encodeURIComponent(runId)}`);
    detailBody.innerHTML = renderRunDetail(record);
    history.replaceState(null, "", `/logs?run=${encodeURIComponent(runId)}`);
  } catch (err) {
    detailBody.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function init() {
  tableEl.innerHTML = `<p class="loading">Loading past runs…</p>`;
  try {
    const runs = await apiGet("/api/runs");
    renderTable(runs);
    const params = new URLSearchParams(location.search);
    const runId = params.get("run");
    if (runId) selectRun(runId);
  } catch (err) {
    tableEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

init();
