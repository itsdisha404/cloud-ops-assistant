const requestEl = document.getElementById("request");
const accountEl = document.getElementById("account_id");
const runBtn = document.getElementById("run-btn");
const statusEl = document.getElementById("run-status");
const resultCard = document.getElementById("result-card");
const resultBody = document.getElementById("result-body");
const pillsEl = document.getElementById("scenario-pills");

async function loadScenarioPills() {
  try {
    const scenarios = await apiGet("/api/scenarios");
    pillsEl.innerHTML = scenarios.map((s) =>
      `<button type="button" class="pill" data-request="${escapeHtml(s.request)}" data-account="${escapeHtml(s.account_id)}" title="${escapeHtml(s.covers)}">${escapeHtml(s.slug)}</button>`
    ).join("");
    pillsEl.querySelectorAll(".pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        requestEl.value = btn.dataset.request;
        accountEl.value = btn.dataset.account;
      });
    });
  } catch {
    // Quick-fill is a convenience, not required for the form to work.
  }
}

async function runRequest() {
  const text = requestEl.value.trim();
  if (!text) {
    statusEl.textContent = "Type a request first.";
    return;
  }
  runBtn.disabled = true;
  statusEl.textContent = "Running — calling the graph, this can take a few seconds…";
  resultCard.style.display = "none";

  try {
    const record = await apiPost("/api/run", {
      request: text,
      account_id: accountEl.value.trim() || null,
    });
    resultBody.innerHTML = renderRunDetail(record);
    resultCard.style.display = "block";
    statusEl.textContent = `Done — run ${record.run_id}. See it any time on the History page.`;
  } catch (err) {
    statusEl.textContent = "";
    resultBody.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
    resultCard.style.display = "block";
  } finally {
    runBtn.disabled = false;
  }
}

runBtn.addEventListener("click", runRequest);
requestEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runRequest();
});

loadScenarioPills();
