// RiskOS control UI -- vanilla JS, no build step. Every function below calls
// a real RiskOS API endpoint via fetch(); nothing here fabricates data.
// Mounted at /ui by apps/riskos_api/src/riskos_api/main.py (StaticFiles),
// so this UI and the API it calls run in the same process, same origin.

const API_BASE = ""; // same-origin: mounted into the RiskOS FastAPI app.

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
// Fetch helper -- always surfaces the exact response body on error, never a
// generic "something went wrong" message.
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

function money(amount, currency, roundingContext) {
  return { amount: String(amount), currency, rounding_context: roundingContext || "riskos.ui.manual_entry" };
}

function fmtMoney(m) {
  if (!m) return "—";
  return `${m.amount} ${m.currency}`;
}

function tryParseJsonArray(text) {
  const trimmed = (text || "").trim();
  if (trimmed === "") return [];
  const parsed = JSON.parse(trimmed);
  if (!Array.isArray(parsed)) throw new Error("expected a JSON array");
  return parsed;
}

// ---------------------------------------------------------------------------
// Input readiness and freshness (DEC-OPS-03: FRESHNESS_WINDOW_HOURS = 24)
// ---------------------------------------------------------------------------

const FRESHNESS_WINDOW_HOURS = 24;

function renderReadiness(el, asOfIso, label) {
  const asOf = new Date(asOfIso);
  const ageHours = (Date.now() - asOf.getTime()) / 3_600_000;
  const fresh = ageHours <= FRESHNESS_WINDOW_HOURS;
  el.hidden = false;
  el.classList.toggle("fresh", fresh);
  el.classList.toggle("stale", !fresh);
  el.textContent =
    `${label} as_of: ${asOfIso}\n` +
    `age: ${ageHours.toFixed(2)}h (freshness window: ${FRESHNESS_WINDOW_HOURS}h)\n` +
    `${fresh ? "FRESH" : "STALE"} (client-side judgement; the authoritative gate is riskos.core.gates.run_freshness_gate)`;
}

document.querySelector("#form-lookup-portfolio").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = new FormData(e.target).get("id");
  const result = await callApi("GET", `/v1/portfolio-snapshots/${encodeURIComponent(id)}`);
  const el = document.querySelector("#result-readiness-portfolio");
  if (!result.ok) {
    el.hidden = false;
    el.className = "readiness-box empty";
    el.textContent = `HTTP ${result.status}: ${JSON.stringify(result.body)}`;
    return;
  }
  renderReadiness(el, result.body.as_of, `Portfolio snapshot ${result.body.snapshot_id} (${result.body.trades.length} trades)`);
});

document.querySelector("#form-lookup-collateral").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = new FormData(e.target).get("id");
  const result = await callApi("GET", `/v1/collateral-state-packages/${encodeURIComponent(id)}`);
  const el = document.querySelector("#result-readiness-collateral");
  if (!result.ok) {
    el.hidden = false;
    el.className = "readiness-box empty";
    el.textContent = `HTTP ${result.status}: ${JSON.stringify(result.body)}`;
    return;
  }
  renderReadiness(
    el,
    result.body.as_of,
    `Collateral package ${result.body.package_id} (custodian_reconciliation_status=${result.body.custodian_reconciliation_status})`
  );
});

document.querySelector("#form-lookup-market").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = new FormData(e.target).get("id");
  const result = await callApi("GET", `/v1/market-snapshots/${encodeURIComponent(id)}`);
  const el = document.querySelector("#result-readiness-market");
  if (!result.ok) {
    el.hidden = false;
    el.className = "readiness-box empty";
    el.textContent = `HTTP ${result.status}: ${JSON.stringify(result.body)}`;
    return;
  }
  // MarketSnapshot.as_of is a date, not a datetime; treat as UTC midnight.
  renderReadiness(el, `${result.body.as_of}T00:00:00Z`, `Market snapshot ${result.body.snapshot_id} (${result.body.discount_curves.length} curves)`);
});

// ---------------------------------------------------------------------------
// Membership
// ---------------------------------------------------------------------------

document.querySelector("#btn-load-netting-sets").addEventListener("click", async () => {
  const result = await callApi("GET", "/v1/netting-set-versions");
  const tbody = document.querySelector("#table-netting-sets tbody");
  tbody.innerHTML = "";
  if (!result.ok) return;
  for (const n of result.body.netting_sets) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${n.netting_set_id}</td><td>${n.calculation_entity_id}</td><td>${n.counterparty_entity_id}</td><td>${n.governing_agreement_id}</td><td>${n.approved}</td>`;
    tbody.appendChild(tr);
  }
});

document.querySelector("#btn-load-legal-entities").addEventListener("click", async () => {
  const result = await callApi("GET", "/v1/legal-entity-versions");
  const tbody = document.querySelector("#table-legal-entities tbody");
  tbody.innerHTML = "";
  if (!result.ok) return;
  for (const e of result.body.legal_entities) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${e.entity_id}</td><td>${e.display_name}</td><td>${e.home_currency}</td><td>${e.is_active_calculation_entity}</td><td>${e.approved}</td>`;
    tbody.appendChild(tr);
  }
});

// ---------------------------------------------------------------------------
// Margin run and waterfall
// ---------------------------------------------------------------------------

function renderStatusGrid(prefix, run) {
  document.querySelector(`#${prefix}-status-grid`).hidden = false;
  document.querySelector(`#${prefix}-status-calculation`).textContent = run.calculation_status;
  document.querySelector(`#${prefix}-status-data-quality`).textContent = run.data_quality_status;
  document.querySelector(`#${prefix}-status-reconciliation`).textContent = run.reconciliation_status;
  document.querySelector(`#${prefix}-status-validation`).textContent = run.validation_status;
  document.querySelector(`#${prefix}-status-actionability`).textContent = run.actionability;
}

document.querySelector("#form-run-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const otherCounts = (f.get("expected_other_counts") || "0,0,0,0,0,0").split(",").map((s) => parseInt(s.trim(), 10) || 0);
  const body = {
    run_id: f.get("run_id"),
    purpose: f.get("purpose"),
    calculation_entity_id: f.get("calculation_entity_id"),
    counterparty_entity_id: f.get("counterparty_entity_id"),
    agreement_id: f.get("agreement_id"),
    portfolio_snapshot_id: f.get("portfolio_snapshot_id"),
    collateral_package_id: f.get("collateral_package_id"),
    manifest: {
      manifest_id: f.get("manifest_id"),
      expected_accepted_count: parseInt(f.get("expected_accepted_count"), 10),
      expected_rejected_count: otherCounts[0],
      expected_duplicate_count: otherCounts[1],
      expected_matured_count: otherCounts[2],
      expected_cancelled_count: otherCounts[3],
      expected_amended_count: otherCounts[4],
      expected_unsupported_count: otherCounts[5],
      expected_accepted_mtm_total: money(f.get("expected_accepted_mtm_total"), "USD", "riskos.trade_mtm.source_incumbent.raw"),
    },
    counterparty_call: {
      party_label: f.get("cp_party_label") || "COUNTERPARTY",
      call_status: f.get("cp_call_status"),
      call_amount: money(f.get("cp_call_amount"), f.get("cp_call_currency") || "USD"),
      responded: true,
    },
  };
  const result = await callApi("POST", "/v1/calculation-runs", body);
  showResult(document.querySelector("#result-run-create"), result);
});

async function loadRunAndWaterfall(runId) {
  const runResult = await callApi("GET", `/v1/calculation-runs/${encodeURIComponent(runId)}`);
  showResult(document.querySelector("#result-run-lookup"), runResult);
  if (!runResult.ok) {
    document.querySelector("#run-status-grid").hidden = true;
    document.querySelector("#table-waterfall").hidden = true;
    return;
  }
  renderStatusGrid("run", runResult.body);

  const table = document.querySelector("#table-waterfall");
  const tbody = table.querySelector("tbody");
  tbody.innerHTML = "";
  if (runResult.body.waterfall) {
    table.hidden = false;
    for (const step of runResult.body.waterfall.steps) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${step.step_name}</td><td>${fmtMoney(step.value)}</td><td>${step.note || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else {
    table.hidden = true;
  }
}

document.querySelector("#form-run-lookup").addEventListener("submit", async (e) => {
  e.preventDefault();
  await loadRunAndWaterfall(new FormData(e.target).get("run_id"));
});

// ---------------------------------------------------------------------------
// Trade-to-total drilldown
// ---------------------------------------------------------------------------

document.querySelector("#form-drilldown").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const snapshotId = f.get("snapshot_id");
  const runId = f.get("run_id");
  const statusEl = document.querySelector("#result-drilldown-status");
  statusEl.textContent = "";

  const snapshotResult = await callApi("GET", `/v1/portfolio-snapshots/${encodeURIComponent(snapshotId)}`);
  const tbody = document.querySelector("#table-drilldown tbody");
  tbody.innerHTML = "";
  if (!snapshotResult.ok) {
    statusEl.textContent = `HTTP ${snapshotResult.status}: ${JSON.stringify(snapshotResult.body)}`;
    return;
  }
  let total = null;
  let currency = null;
  for (const t of snapshotResult.body.trades) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${t.trade_id}</td><td>${t.product_family}</td><td>${t.disposition}</td><td>${t.lifecycle_state}</td><td>${fmtMoney(t.mtm)}</td>`;
    tbody.appendChild(tr);
    if (t.disposition === "ACCEPTED") {
      currency = t.mtm.currency;
      total = (total === null ? 0 : total) + parseFloat(t.mtm.amount);
    }
  }
  document.querySelector("#drilldown-total").textContent = total === null ? "—" : `${total.toFixed(2)} ${currency}`;

  if (runId) {
    const wfResult = await callApi("GET", `/v1/calculation-runs/${encodeURIComponent(runId)}/waterfall`);
    if (wfResult.ok) {
      statusEl.textContent = `waterfall.exposure = ${fmtMoney(wfResult.body.exposure)} (compare against the sum above)`;
    } else {
      statusEl.textContent = `run/waterfall lookup: HTTP ${wfResult.status}: ${JSON.stringify(wfResult.body)}`;
    }
  }
});

// ---------------------------------------------------------------------------
// Reconciliation
// ---------------------------------------------------------------------------

function renderBreaks(breaks) {
  const table = document.querySelector("#table-breaks");
  const tbody = table.querySelector("tbody");
  tbody.innerHTML = "";
  if (!breaks || breaks.length === 0) {
    table.hidden = true;
    return;
  }
  table.hidden = false;
  for (const b of breaks) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${b.break_type}</td><td>${b.description}</td><td>${fmtMoney(b.calculation_party_amount)}</td><td>${fmtMoney(b.counterparty_amount)}</td><td>${fmtMoney(b.difference)}</td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#form-reconciliation").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    run_id: f.get("run_id"),
    counterparty_call: {
      party_label: f.get("party_label") || "COUNTERPARTY",
      call_status: f.get("call_status"),
      call_amount: money(f.get("call_amount"), f.get("call_currency") || "USD"),
      responded: f.get("responded") === "on",
    },
  };
  const result = await callApi("POST", "/v1/reconciliations", body);
  showResult(document.querySelector("#result-reconciliation"), result);
  if (result.ok) renderBreaks(result.body.breaks);
});

document.querySelector("#form-reconciliation-lookup").addEventListener("submit", async (e) => {
  e.preventDefault();
  const caseId = new FormData(e.target).get("case_id");
  const result = await callApi("GET", `/v1/reconciliation-cases/${encodeURIComponent(caseId)}`);
  showResult(document.querySelector("#result-reconciliation-lookup"), result);
  if (result.ok) renderBreaks(result.body.breaks);
});

// ---------------------------------------------------------------------------
// Method pack
// ---------------------------------------------------------------------------

document.querySelector("#btn-load-method-packs").addEventListener("click", async () => {
  const result = await callApi("GET", "/v1/method-packs");
  const tbody = document.querySelector("#table-method-packs tbody");
  tbody.innerHTML = "";
  if (!result.ok) return;
  for (const p of result.body.method_packs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.method_id}</td><td>${p.version}</td><td>${p.call_currency}</td><td>${p.minimum_transfer_amount.amount} ${p.minimum_transfer_amount.currency}</td><td>${p.validation_status}</td><td>${p.provisional}</td>`;
    tbody.appendChild(tr);
  }
});

// ---------------------------------------------------------------------------
// Challenger comparison (RSK-X01, DEC-RSK-02)
// ---------------------------------------------------------------------------

document.querySelector("#form-market-snapshot").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  let body;
  try {
    body = {
      snapshot_id: f.get("snapshot_id"),
      as_of: f.get("as_of"),
      calendars: (f.get("calendars") || "").split(",").map((s) => s.trim()).filter(Boolean),
      discount_curves: tryParseJsonArray(f.get("discount_curves_json")),
      fx_spot_rates: tryParseJsonArray(f.get("fx_spot_rates_json")),
    };
  } catch (err) {
    showResult(document.querySelector("#result-market-snapshot"), { ok: false, status: 0, body: { error: `invalid JSON: ${err.message}` } });
    return;
  }
  const result = await callApi("POST", "/v1/market-snapshots", body);
  showResult(document.querySelector("#result-market-snapshot"), result);
});

function renderChallengerComparisons(comparisons) {
  const table = document.querySelector("#table-challenger-comparisons");
  const tbody = table.querySelector("tbody");
  tbody.innerHTML = "";
  if (!comparisons || comparisons.length === 0) {
    table.hidden = true;
    return;
  }
  table.hidden = false;
  for (const c of comparisons) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${c.trade_id}</td><td>${c.product_family}</td>` +
      `<td>${fmtMoney(c.challenger_pv)}</td><td>${fmtMoney(c.incumbent_pv)}</td>` +
      `<td>${fmtMoney(c.difference)}</td><td>${fmtMoney(c.tolerance)}</td>` +
      `<td>${c.comparison_status}</td><td>${c.note || ""}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadChallengerRun(runId) {
  const result = await callApi("GET", `/v1/challenger-runs/${encodeURIComponent(runId)}`);
  showResult(document.querySelector("#result-challenger-run"), result);
  if (!result.ok) {
    document.querySelector("#challenger-status-grid").hidden = true;
    document.querySelector("#table-challenger-comparisons").hidden = true;
    return;
  }
  renderStatusGrid("challenger", result.body);
  renderChallengerComparisons(result.body.comparisons);
}

document.querySelector("#form-challenger-run").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  let irsTrades, fxTrades;
  try {
    irsTrades = tryParseJsonArray(f.get("irs_trades_json"));
    fxTrades = tryParseJsonArray(f.get("fx_forward_trades_json"));
  } catch (err) {
    showResult(document.querySelector("#result-challenger-run"), { ok: false, status: 0, body: { error: `invalid JSON: ${err.message}` } });
    return;
  }
  const body = {
    run_id: f.get("run_id"),
    netting_set_id: f.get("netting_set_id"),
    market_snapshot_id: f.get("market_snapshot_id"),
    as_of: f.get("as_of"),
    portfolio_snapshot_id: f.get("portfolio_snapshot_id") || null,
    irs_trades: irsTrades,
    fx_forward_trades: fxTrades,
  };
  const result = await callApi("POST", "/v1/challenger-runs", body);
  showResult(document.querySelector("#result-challenger-run"), result);
  if (result.ok) {
    renderStatusGrid("challenger", result.body);
    renderChallengerComparisons(result.body.comparisons);
  }
});

document.querySelector("#form-challenger-lookup").addEventListener("submit", async (e) => {
  e.preventDefault();
  await loadChallengerRun(new FormData(e.target).get("run_id"));
});

// ---------------------------------------------------------------------------
// Evidence export
// ---------------------------------------------------------------------------

document.querySelector("#form-export").addEventListener("submit", async (e) => {
  e.preventDefault();
  const runId = new FormData(e.target).get("run_id");
  const result = await callApi("POST", "/v1/result-packages:export", { run_id: runId });
  showResult(document.querySelector("#result-export"), result);
});
