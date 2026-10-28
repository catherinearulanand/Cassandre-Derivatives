// CollateralOS control UI -- vanilla JS, no build step. Every function below
// calls a real CollateralOS API endpoint via fetch(); nothing here fabricates
// data. Session-local tables (obligation queue, snapshot log, reservation
// log, export history) exist only because the API has no list endpoints for
// those resources -- they are explicitly labelled as such in index.html.

const API_BASE = ""; // same-origin: this UI is mounted into the CollateralOS FastAPI app.

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

document.querySelectorAll("[data-tab-link]").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    activateTab(el.dataset.tabLink);
  });
});

// ---------------------------------------------------------------------------
// Fetch helper -- always surfaces the exact response body on error, never a
// generic "something went wrong" message (plan: "never a bare boolean" /
// show exact failure reasons).
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

// Convert a <input type="datetime-local"> value ("YYYY-MM-DDTHH:MM") into an
// RFC3339 UTC timestamp. The form labels every such field "(UTC)" so the
// entered wall-clock value is treated as UTC directly -- no timezone
// conversion is invented client-side.
function isoFromLocal(value) {
  if (!value) return value;
  return value.length === 16 ? `${value}:00Z` : `${value}Z`;
}

function tryParseJson(text, fallback) {
  if (text === undefined || text === null || text.trim() === "") return fallback;
  return JSON.parse(text);
}

// ---------------------------------------------------------------------------
// Obligation queue
// ---------------------------------------------------------------------------

const obligationLog = [];

function renderObligationTable() {
  const tbody = document.querySelector("#table-obligations tbody");
  tbody.innerHTML = "";
  for (const row of obligationLog) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${row.obligation_id}</td><td>${row.status}</td><td>${row.version}</td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#form-obligation").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    obligation_id: f.get("obligation_id"),
    counterparty_id: f.get("counterparty_id"),
    entity_id: f.get("entity_id"),
    agreement_id: f.get("agreement_id"),
    direction: f.get("direction"),
    amount: { amount: f.get("amount_amount"), currency: f.get("amount_currency") },
    due_at: isoFromLocal(f.get("due_at")),
    source: f.get("source"),
    version: f.get("version") || "v1",
    status: f.get("status"),
    actor_id: f.get("actor_id") || "SIM-USER-API-0001",
  };
  const result = await callApi("POST", "/v1/obligations", body);
  showResult(document.querySelector("#result-obligation"), result);
  if (result.ok) {
    obligationLog.push(result.body);
    renderObligationTable();
  }
});

// ---------------------------------------------------------------------------
// Inventory snapshots
// ---------------------------------------------------------------------------

const EXAMPLE_POSITIONS = [
  {
    position_id: "SIM-POS-0001",
    owner_entity_id: "SIM-ENTITY-CASSANDRE-BANK-NA",
    account_id: "SIM-ACCT-COLLAT-0001",
    location: "SIM-LOC-CUSTODY-0001",
    asset_class: "CASH_USD",
    issuer_group_id: null,
    currency: "USD",
    market_value: { amount: "500000.00", currency: "USD" },
    quantity: { amount: "500000.00", unit: "USD" },
    price_as_of: "2026-06-01T00:00:00Z",
    stale_after: "2026-06-08T00:00:00Z",
    is_encumbered: false,
    is_recalled: false,
    is_self_issued: false,
    is_affiliate_issued: false,
  },
  {
    position_id: "SIM-POS-0002",
    owner_entity_id: "SIM-ENTITY-CASSANDRE-BANK-NA",
    account_id: "SIM-ACCT-COLLAT-0001",
    location: "SIM-LOC-CUSTODY-0001",
    asset_class: "US_TREASURY",
    issuer_group_id: "SIM-ISSUER-UST",
    currency: "USD",
    market_value: { amount: "750000.00", currency: "USD" },
    quantity: { amount: "750000.00", unit: "FACE_USD" },
    price_as_of: "2026-06-01T00:00:00Z",
    stale_after: "2026-06-08T00:00:00Z",
    is_encumbered: false,
    is_recalled: false,
    is_self_issued: false,
    is_affiliate_issued: false,
  },
];

document.querySelector("#btn-load-snapshot-example").addEventListener("click", () => {
  document.querySelector('textarea[name="positions_json"]').value = JSON.stringify(EXAMPLE_POSITIONS, null, 2);
});

const snapshotLog = [];

function renderSnapshotTable() {
  const tbody = document.querySelector("#table-snapshots tbody");
  tbody.innerHTML = "";
  for (const row of snapshotLog) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${row.snapshot_id}</td><td>${row.version}</td><td>${row.position_count}</td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#form-snapshot").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  let positions;
  try {
    positions = JSON.parse(f.get("positions_json"));
  } catch (err) {
    showResult(document.querySelector("#result-snapshot"), {
      ok: false,
      status: "n/a",
      body: { error: "CLIENT_JSON_PARSE_ERROR", detail: String(err) },
    });
    return;
  }
  const body = {
    snapshot_id: f.get("snapshot_id"),
    version: f.get("version") || "v1",
    as_of: isoFromLocal(f.get("as_of")),
    positions,
    actor_id: f.get("actor_id") || "SIM-USER-API-0001",
  };
  const result = await callApi("POST", "/v1/inventory-snapshots", body);
  showResult(document.querySelector("#result-snapshot"), result);
  if (result.ok) {
    snapshotLog.push(result.body);
    renderSnapshotTable();
  }
});

// ---------------------------------------------------------------------------
// Policy bundle helpers (validate / activate)
// ---------------------------------------------------------------------------

document.querySelector("#btn-validate-policy").addEventListener("click", async () => {
  const result = await callApi("POST", "/v1/policy-bundles:validate", { actor_id: "SIM-USER-API-0001" });
  showResult(document.querySelector("#result-policy"), result);
  if (result.ok) {
    document.querySelector("#input-activate-policy-id").value = result.body.policy_id;
  }
});

document.querySelector("#btn-activate-policy").addEventListener("click", async () => {
  const policyId = document.querySelector("#input-activate-policy-id").value;
  if (!policyId) return;
  const result = await callApi("POST", `/v1/policy-bundles/${encodeURIComponent(policyId)}:activate`, {
    actor_id: "SIM-USER-API-0001",
  });
  showResult(document.querySelector("#result-policy"), result);
});

// ---------------------------------------------------------------------------
// Planner: create + look up plans
// ---------------------------------------------------------------------------

const planLog = [];
let lastPlanEvidenceById = {}; // plan_id -> evidence body, shared with eligibility/rejected tabs

function renderPlanTable() {
  const tbody = document.querySelector("#table-plans tbody");
  tbody.innerHTML = "";
  for (const row of planLog) {
    const ev = row.evidence;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${row.plan_id}</td><td>${statusBadge(row.status)}</td><td>${ev.method}</td>` +
      `<td>${ev.objective_value ?? ""}</td><td>${ev.gap_pct ?? ""}</td><td>${ev.verified}</td>`;
    tbody.appendChild(tr);
  }
}

function statusBadge(status) {
  const okStatuses = ["FEASIBLE_OPTIMAL", "FEASIBLE_BOUNDED_GAP", "FEASIBLE_HEURISTIC_VERIFIED"];
  const cls = okStatuses.includes(status) ? "ok" : "bad";
  return `<span class="badge ${cls}">${status}</span>`;
}

document.querySelector("#form-plan-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  let existingHeldByGroup, costRateByPosition;
  try {
    existingHeldByGroup = tryParseJson(f.get("existing_held_by_issuer_group"), {});
    costRateByPosition = tryParseJson(f.get("cost_rate_by_position"), null);
  } catch (err) {
    alert("Invalid JSON in existing_held_by_issuer_group or cost_rate_by_position: " + err);
    return;
  }
  const body = {
    plan_id: f.get("plan_id"),
    obligation_id: f.get("obligation_id"),
    policy_id: f.get("policy_id"),
    snapshot_id: f.get("snapshot_id"),
    as_of: isoFromLocal(f.get("as_of")),
    existing_held_by_issuer_group: existingHeldByGroup,
    existing_held_total: f.get("existing_held_total") || "0",
    cost_rate_by_position: costRateByPosition,
    actor_id: f.get("actor_id") || "SIM-USER-API-0001",
  };
  const result = await callApi("POST", "/v1/plans", body);
  showResult(document.querySelector("#result-plan"), result);
  if (result.ok) {
    planLog.push(result.body);
    lastPlanEvidenceById[result.body.plan_id] = result.body.evidence;
    renderPlanTable();
  }
});

document.querySelector("#form-plan-lookup").addEventListener("submit", async (e) => {
  e.preventDefault();
  const planId = new FormData(e.target).get("plan_id");
  const result = await callApi("GET", `/v1/plans/${encodeURIComponent(planId)}`);
  showResult(document.querySelector("#result-plan"), result);
  if (result.ok) {
    lastPlanEvidenceById[result.body.plan_id] = result.body.evidence;
  }
});

// ---------------------------------------------------------------------------
// Eligibility matrix (derived from plan evidence's verifier_notes)
// ---------------------------------------------------------------------------

// verifier_notes entries look like:
//   "mip_status=... mip_message='...'"
//   "MIP candidate rejected by independent verifier: INELIGIBLE_POSITION:SIM-POS-1:STALE_PRICE,ENCUMBERED"
//   "INELIGIBLE_POSITION:SIM-POS-1:STALE_PRICE,ENCUMBERED"   (from verification.violations, appended raw)
//   "CONCENTRATION_CAP_EXCEEDED:SIM-ISSUER-X:123>100"
//   "EXCEEDS_AVAILABLE_INVENTORY:SIM-POS-2"
// This parser extracts every colon-delimited violation token it can find,
// wherever it appears in a note (including inside the "rejected by
// independent verifier: ..." prefix), rather than assuming one note = one
// violation.
const VIOLATION_PREFIXES = [
  "INELIGIBLE_POSITION",
  "EXCEEDS_AVAILABLE_INVENTORY",
  "MINIMUM_LOT_VIOLATION",
  "NO_MINIMUM_LOT_POLICY",
  "CONCENTRATION_CAP_EXCEEDED",
  "INSUFFICIENT_COVERAGE",
  "DUPLICATE_LINE_FOR_POSITION",
  "UNKNOWN_POSITION",
  "NON_POSITIVE_QUANTITY",
  "CURRENCY_MISMATCH",
  "STATED_VALUE_OVERSTATED",
];

function extractViolationTokens(notes) {
  const tokens = [];
  // Scan the whole joined text for VIOLATION_PREFIX:... tokens, rather than
  // assuming one note = one violation (a note can be a prefixed sentence
  // like "MIP candidate rejected by independent verifier: <token>,<token>").
  const joined = notes.join(" | ");
  for (const prefix of VIOLATION_PREFIXES) {
    const re = new RegExp(`${prefix}:[^\\s,]+(,[A-Z_]+)*`, "g");
    let m;
    while ((m = re.exec(joined)) !== null) {
      tokens.push(m[0]);
    }
  }
  return tokens;
}

function parseIneligiblePositions(notes) {
  // Returns { position_id: [reason, ...] } from INELIGIBLE_POSITION:<id>:<r1>,<r2> tokens.
  const out = {};
  for (const token of extractViolationTokens(notes)) {
    if (!token.startsWith("INELIGIBLE_POSITION:")) continue;
    const rest = token.slice("INELIGIBLE_POSITION:".length);
    const idx = rest.indexOf(":");
    if (idx === -1) continue;
    const positionId = rest.slice(0, idx);
    const reasons = rest.slice(idx + 1).split(",");
    out[positionId] = reasons;
  }
  return out;
}

document.querySelector("#form-eligibility").addEventListener("submit", async (e) => {
  e.preventDefault();
  const planId = new FormData(e.target).get("plan_id");
  const result = await callApi("GET", `/v1/plans/${encodeURIComponent(planId)}/evidence`);
  showResult(document.querySelector("#result-eligibility-raw"), result);
  const tbody = document.querySelector("#table-eligibility tbody");
  tbody.innerHTML = "";
  if (!result.ok) return;
  lastPlanEvidenceById[planId] = result.body;
  const ineligible = parseIneligiblePositions(result.body.verifier_notes);
  if (Object.keys(ineligible).length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="2">No INELIGIBLE_POSITION entries found in this plan's verifier_notes ` +
      `(either every candidate position was eligible, or the plan never reached a candidate to verify -- see the ` +
      `raw evidence above and the "Rejected assets" tab for the full diagnosis).</td>`;
    tbody.appendChild(tr);
    return;
  }
  for (const [positionId, reasons] of Object.entries(ineligible)) {
    const tr = document.createElement("tr");
    const reasonItems = reasons.map((r) => `<li>${r}</li>`).join("");
    tr.innerHTML = `<td>${positionId}</td><td><ul class="reasons-list">${reasonItems}</ul></td>`;
    tbody.appendChild(tr);
  }
});

// ---------------------------------------------------------------------------
// Rejected assets / infeasibility diagnosis (same evidence, grouped view)
// ---------------------------------------------------------------------------

document.querySelector("#form-rejected").addEventListener("submit", async (e) => {
  e.preventDefault();
  const planId = new FormData(e.target).get("plan_id");
  const [planResult, evidenceResult] = await Promise.all([
    callApi("GET", `/v1/plans/${encodeURIComponent(planId)}`),
    callApi("GET", `/v1/plans/${encodeURIComponent(planId)}/evidence`),
  ]);
  const statusEl = document.querySelector("#result-rejected-status");
  const groupsEl = document.querySelector("#result-rejected-groups");
  if (!evidenceResult.ok) {
    statusEl.innerHTML = `<div class="result-box error">HTTP ${evidenceResult.status}\n${JSON.stringify(evidenceResult.body, null, 2)}</div>`;
    groupsEl.innerHTML = "";
    return;
  }
  const evidence = evidenceResult.body;
  const planStatus = planResult.ok ? planResult.body.status : evidence.status;
  statusEl.innerHTML = `<p>Solver status: ${statusBadge(planStatus)} &nbsp; method: <code>${evidence.method}</code> ` +
    `&nbsp; verified: <code>${evidence.verified}</code></p>`;

  const tokens = extractViolationTokens(evidence.verifier_notes);
  const groups = {};
  for (const token of tokens) {
    const category = token.split(":")[0];
    (groups[category] = groups[category] || []).push(token);
  }
  if (Object.keys(groups).length === 0) {
    groupsEl.innerHTML = `<div class="note">No structured violation tokens found in verifier_notes for this plan. ` +
      `Raw notes:</div><div class="result-box">${evidence.verifier_notes.map(escapeHtml).join("\n")}</div>`;
    return;
  }
  let html = "";
  for (const [category, items] of Object.entries(groups)) {
    html += `<h3 style="font-size:13px;margin:10px 0 4px;">${category} (${items.length})</h3>`;
    html += `<ul class="reasons-list">${items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
  }
  html += `<h3 style="font-size:13px;margin:10px 0 4px;">Full verifier_notes (unparsed)</h3>`;
  html += `<div class="result-box">${evidence.verifier_notes.map(escapeHtml).join("\n")}</div>`;
  groupsEl.innerHTML = html;
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------------------
// Reservation / conflict
// ---------------------------------------------------------------------------

const reservationLog = [];

function renderReservationTable() {
  const tbody = document.querySelector("#table-reservations tbody");
  tbody.innerHTML = "";
  for (const row of reservationLog) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${row.reservation_id}</td><td>${row.plan_id}</td><td>${row.status}</td>` +
      `<td>${row.expires_at}</td><td>${row.instruction_id}</td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#form-reserve").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const planId = f.get("plan_id");
  const body = {
    reservation_id: f.get("reservation_id"),
    instruction_id: f.get("instruction_id"),
    ttl_minutes: f.get("ttl_minutes") ? Number(f.get("ttl_minutes")) : null,
    actor_id: f.get("actor_id") || "SIM-USER-API-0001",
  };
  const result = await callApi("POST", `/v1/plans/${encodeURIComponent(planId)}:reserve`, body);
  showResult(document.querySelector("#result-reserve"), result);
  const conflictEl = document.querySelector("#result-reserve-conflict");
  if (result.ok) {
    conflictEl.hidden = true;
    reservationLog.push(result.body);
    renderReservationTable();
  } else if (result.status === 409 && result.body && result.body.shortfalls) {
    // RESERVATION_CONFLICT: show the exact per-position shortfalls, never a bare "conflict" message.
    conflictEl.hidden = false;
    const rows = Object.entries(result.body.shortfalls)
      .map(([pid, amt]) => `<li><code>${pid}</code>: short by <code>${amt}</code></li>`)
      .join("");
    conflictEl.innerHTML = `<div class="note" style="border-color:#c0392b;background:#fdecec;">` +
      `RESERVATION_CONFLICT &mdash; per-position shortfalls:</div><ul class="reasons-list">${rows}</ul>`;
  } else {
    conflictEl.hidden = true;
  }
});

// ---------------------------------------------------------------------------
// Maker/checker approval
// ---------------------------------------------------------------------------

document.querySelector("#form-maker-approve").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const reservationId = f.get("reservation_id");
  const result = await callApi("POST", `/v1/reservations/${encodeURIComponent(reservationId)}:maker-approve`, {
    maker_id: f.get("maker_id"),
  });
  showResult(document.querySelector("#result-maker-approve"), result);
});

document.querySelector("#form-checker-approve").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const reservationId = f.get("reservation_id");
  const result = await callApi("POST", `/v1/reservations/${encodeURIComponent(reservationId)}:checker-approve`, {
    checker_id: f.get("checker_id"),
  });
  showResult(document.querySelector("#result-checker-approve"), result);
});

// ---------------------------------------------------------------------------
// Preflight
// ---------------------------------------------------------------------------

document.querySelector("#form-preflight").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const instructionId = f.get("instruction_id");
  const result = await callApi("POST", `/v1/instructions/${encodeURIComponent(instructionId)}:preflight`, {
    actor_id: f.get("actor_id") || "SIM-USER-API-0001",
  });
  const el = document.querySelector("#result-preflight");
  if (!result.ok) {
    el.innerHTML = `<div class="result-box error">HTTP ${result.status}\n${JSON.stringify(result.body, null, 2)}</div>`;
    return;
  }
  const passed = result.body.passed;
  const reasons = result.body.failure_reasons || [];
  let html = `<p>Preflight <span class="badge ${passed ? "ok" : "bad"}">${passed ? "PASSED" : "FAILED"}</span> ` +
    `&mdash; preflight_id <code>${result.body.preflight_id}</code>, performed_at <code>${result.body.performed_at}</code></p>`;
  if (reasons.length > 0) {
    html += `<ul class="reasons-list">${reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`;
  }
  el.innerHTML = html;
});

// ---------------------------------------------------------------------------
// Export history
// ---------------------------------------------------------------------------

const exportLog = [];

function renderExportTable() {
  const tbody = document.querySelector("#table-exports tbody");
  tbody.innerHTML = "";
  for (const row of exportLog) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${row.instruction_id}</td><td>${row.object_id ?? ""}</td>` +
      `<td>${row.content_hash ?? ""}</td><td>${row.payload ? row.payload.transmitted : ""}</td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#form-export").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const instructionId = f.get("instruction_id");
  const body = {
    idempotency_key: f.get("idempotency_key"),
    actor_id: f.get("actor_id") || "SIM-USER-API-0001",
  };
  const result = await callApi("POST", `/v1/instructions/${encodeURIComponent(instructionId)}:export-internal`, body);
  showResult(document.querySelector("#result-export"), result);
  if (result.ok) {
    exportLog.push({ instruction_id: instructionId, ...result.body });
    renderExportTable();
  }
});
