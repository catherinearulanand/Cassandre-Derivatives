// Cassandre control UI -- vanilla JS, no build step. Every function below
// calls the real Cassandre control API via fetch(); nothing here fabricates
// data. Session-local tables exist only where the API has no list endpoint
// (trust-key registry has no GET list route) and are labelled as such.

const API_BASE = ""; // same-origin: this UI is mounted into the Cassandre control FastAPI app.

const REQUIRED_PRODUCTS = ["clauseos", "riskos", "collateralos"];

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

function activateTab(name) {
  document.querySelectorAll("nav#tabs button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll("main .panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${name}`);
  });
}

document.querySelectorAll("nav#tabs button").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

// ---------------------------------------------------------------------------
// Fetch helper
// ---------------------------------------------------------------------------

async function callApi(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(API_BASE + path, opts);
  let json = null;
  try {
    json = await resp.json();
  } catch (e) {
    json = { note: "response had no JSON body" };
  }
  return { ok: resp.ok, status: resp.status, body: json };
}

function showResult(el, result) {
  el.hidden = false;
  el.textContent = `HTTP ${result.status}\n` + JSON.stringify(result.body, null, 2);
  el.classList.toggle("error", !result.ok);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------------------
// Trust keys
// ---------------------------------------------------------------------------

const keyLog = [];

function renderKeyTable() {
  const tbody = document.querySelector("#table-keys tbody");
  tbody.innerHTML = "";
  for (const row of keyLog) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(row.key_id)}</td><td>${row.action}</td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#form-register-key").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = { key_id: f.get("key_id"), public_key_b64: f.get("public_key_b64") };
  const result = await callApi("POST", "/v1/trust-keys", body);
  showResult(document.querySelector("#result-register-key"), result);
  if (result.ok) {
    keyLog.push({ key_id: body.key_id, action: "registered" });
    renderKeyTable();
  }
});

document.querySelector("#form-revoke-key").addEventListener("submit", async (e) => {
  e.preventDefault();
  const keyId = new FormData(e.target).get("key_id");
  const result = await callApi("POST", `/v1/trust-keys/${encodeURIComponent(keyId)}:revoke`);
  showResult(document.querySelector("#result-revoke-key"), result);
  if (result.ok) {
    keyLog.push({ key_id: keyId, action: "revoked" });
    renderKeyTable();
  }
});

// ---------------------------------------------------------------------------
// Envelope submission
// ---------------------------------------------------------------------------

document.querySelector("#file-envelope").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const text = await file.text();
  document.querySelector('textarea[name="envelope_json"]').value = text;
});

document.querySelector("#form-submit-envelope").addEventListener("submit", async (e) => {
  e.preventDefault();
  const raw = new FormData(e.target).get("envelope_json");
  const resultEl = document.querySelector("#result-envelope");
  let envelope;
  try {
    envelope = JSON.parse(raw);
  } catch (err) {
    resultEl.innerHTML = `<div class="result-box error">Envelope is not valid JSON: ${escapeHtml(String(err))}</div>`;
    return;
  }
  const result = await callApi("POST", "/v1/envelopes:submit", envelope);
  if (result.ok) {
    resultEl.innerHTML =
      `<p><span class="badge ok">ACCEPTED</span></p>` +
      `<table><tbody>` +
      `<tr><th>correlation_key</th><td>${escapeHtml(result.body.correlation_key ?? "")}</td></tr>` +
      `<tr><th>producer_product</th><td>${escapeHtml(result.body.producer_product ?? "")}</td></tr>` +
      `<tr><th>object_id</th><td>${escapeHtml(result.body.object_id ?? "")}</td></tr>` +
      `</tbody></table>`;
    return;
  }
  // 422 REJECTED: show the exact failures list from VerificationResult.failures, never a generic error.
  const failures = (result.body && result.body.detail && result.body.detail.failures) || null;
  if (result.status === 422 && Array.isArray(failures)) {
    resultEl.innerHTML =
      `<p><span class="badge bad">REJECTED</span></p>` +
      `<ul class="reasons-list">${failures.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>`;
  } else {
    resultEl.innerHTML = `<div class="result-box error">HTTP ${result.status}\n${escapeHtml(JSON.stringify(result.body, null, 2))}</div>`;
  }
});

// ---------------------------------------------------------------------------
// Correlated case view
// ---------------------------------------------------------------------------

document.querySelector("#form-correlated-case").addEventListener("submit", async (e) => {
  e.preventDefault();
  const key = new FormData(e.target).get("correlation_key");
  const result = await callApi("GET", `/v1/correlated-cases/${encodeURIComponent(key)}`);
  const el = document.querySelector("#result-correlated-case");
  if (!result.ok) {
    el.innerHTML = `<div class="result-box error">HTTP ${result.status}\n${escapeHtml(JSON.stringify(result.body, null, 2))}</div>`;
    return;
  }
  const present = new Set(result.body.products_present);
  const rows = REQUIRED_PRODUCTS.map(
    (p) => `<tr><td>${p}</td><td>${present.has(p) ? '<span class="badge ok">submitted</span>' : '<span class="badge bad">missing</span>'}</td></tr>`
  ).join("");
  el.innerHTML =
    `<p>Correlation key <code>${escapeHtml(result.body.correlation_key)}</code> &mdash; ` +
    `cross-product complete: <span class="badge ${result.body.is_cross_product_complete ? "ok" : "warn"}">` +
    `${result.body.is_cross_product_complete}</span></p>` +
    `<table><thead><tr><th>product</th><th>status</th></tr></thead><tbody>${rows}</tbody></table>`;
});

// ---------------------------------------------------------------------------
// Federated audit export
// ---------------------------------------------------------------------------

document.querySelector("#form-audit-export").addEventListener("submit", async (e) => {
  e.preventDefault();
  const key = new FormData(e.target).get("correlation_key");
  const result = await callApi("GET", `/v1/correlated-cases/${encodeURIComponent(key)}/federated-audit-export`);
  const el = document.querySelector("#result-audit-export");
  if (!result.ok) {
    el.innerHTML = `<div class="result-box error">HTTP ${result.status}\n${escapeHtml(JSON.stringify(result.body, null, 2))}</div>`;
    return;
  }
  const body = result.body;
  const rows = body.receipts
    .map(
      (r) =>
        `<tr><td>${escapeHtml(r.producer_product)}</td><td>${escapeHtml(r.object_id)}</td>` +
        `<td>${escapeHtml(r.object_version)}</td><td style="font-family:monospace;font-size:11px;">${escapeHtml(r.content_hash)}</td>` +
        `<td>${escapeHtml(r.authority_status)}</td><td>${escapeHtml(r.received_at)}</td></tr>`
    )
    .join("");
  const rawId = `raw-json-${Math.random().toString(36).slice(2)}`;
  el.innerHTML =
    `<p>Correlation key <code>${escapeHtml(body.correlation_key)}</code>, assembled at ` +
    `<code>${escapeHtml(body.assembled_at)}</code>, ${body.receipts.length} receipt(s).</p>` +
    `<table><thead><tr><th>producer_product</th><th>object_id</th><th>object_version</th>` +
    `<th>content_hash</th><th>authority_status</th><th>received_at</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>` +
    `<p class="toggle-link" id="toggle-${rawId}">view raw JSON</p>` +
    `<div class="result-box" id="${rawId}" hidden>${escapeHtml(JSON.stringify(body, null, 2))}</div>`;

  document.querySelector(`#toggle-${rawId}`).addEventListener("click", () => {
    const box = document.querySelector(`#${rawId}`);
    box.hidden = !box.hidden;
  });
});
