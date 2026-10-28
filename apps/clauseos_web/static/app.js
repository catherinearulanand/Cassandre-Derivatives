"use strict";

/*
 * ClauseOS control-workflow UI (plan Section 10.8).
 *
 * Plain vanilla JS, no build step, no framework. Talks directly to the
 * clauseos_api FastAPI app via fetch() against relative /v1/... paths --
 * this file is served by that same app (mounted at /ui via
 * fastapi.staticfiles.StaticFiles), so no CORS/base-URL configuration is
 * needed.
 *
 * Design rule enforced throughout (plan Section 10.8): never present a bare
 * scalar likelihood number as proof of anything. Every status is a
 * discrete, named badge (VERIFIED / MISMATCH / COMPLETE / INCOMPLETE /
 * CONFLICT / AGREE / DISAGREE / MODEL-ONLY / RULE-ONLY / required action),
 * never a percentage.
 */

const state = {
  bundles: [],
  selectedBundleId: null,
};

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    data = null;
  }
  if (!resp.ok) {
    const detail = (data && (data.detail || data.error)) || resp.statusText;
    const err = new Error(`${resp.status} ${detail}`);
    err.body = data;
    err.status = resp.status;
    throw err;
  }
  return data;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function badge(text, kind) {
  return `<span class="badge ${kind}">${escapeHtml(text)}</span>`;
}

function bundleStatusBadge(status) {
  const ok = ["PUBLISHED"];
  const bad = ["BUNDLE_INCOMPLETE", "ABSTAIN", "NOT_FOUND", "CONFLICT", "OCR_UNCERTAIN",
    "UNSUPPORTED_DOCUMENT", "EXTERNAL_STATE_REQUIRED", "FAILED", "REVOKED"];
  const kind = ok.includes(status) ? "ok" : bad.includes(status) ? "bad" : "neutral";
  return badge(status, kind);
}

function formatValue(v) {
  if (!v) return "";
  if (v.kind === "MONEY") return `${v.currency} ${v.amount}`;
  if (v.kind === "NOTIFICATION_DEADLINE") return `${v.days} ${v.basis}`;
  return JSON.stringify(v);
}

function errorBox(msg) {
  return `<div class="error-box">${escapeHtml(msg)}</div>`;
}

// ---------------------------------------------------------------- routing

const PAGES = [
  ["inventory", "Bundle Inventory"],
  ["graph", "Authority Graph"],
  ["evidence", "Evidence Review"],
  ["maker", "Maker Queue"],
  ["checker", "Checker Queue"],
  ["conflicts", "Unresolved / Conflicts"],
  ["diff", "Diff & Impact"],
  ["releases", "Releases & Revocations"],
  ["audit", "Export & Audit"],
];

const RENDERERS = {}; // filled in below, one entry per page id

function showPage(id) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === id));
  document.querySelectorAll("section.page").forEach((s) => s.classList.toggle("active", s.id === `page-${id}`));
  location.hash = id;
  const renderer = RENDERERS[id];
  if (renderer) renderer();
}

function buildShell() {
  const nav = document.getElementById("nav");
  PAGES.forEach(([id, label]) => {
    const btn = el(`<button data-page="${id}">${escapeHtml(label)}</button>`);
    btn.addEventListener("click", () => showPage(id));
    nav.appendChild(btn);
  });
  const main = document.getElementById("main");
  PAGES.forEach(([id]) => {
    main.appendChild(el(`<section class="page" id="page-${id}"></section>`));
  });
}

// ------------------------------------------------------------ bundle picker

async function refreshBundles() {
  const data = await api("GET", "/v1/bundles");
  state.bundles = data.bundles;
  const picker = document.getElementById("bundlePicker");
  const prev = state.selectedBundleId;
  picker.innerHTML = "";
  if (state.bundles.length === 0) {
    picker.appendChild(el(`<option value="">(no bundles yet)</option>`));
  }
  state.bundles.forEach((b) => {
    picker.appendChild(el(`<option value="${b.bundle_id}">${escapeHtml(b.bundle_id)} - ${escapeHtml(b.status)}</option>`));
  });
  if (prev && state.bundles.some((b) => b.bundle_id === prev)) {
    picker.value = prev;
  } else if (state.bundles.length > 0) {
    state.selectedBundleId = state.bundles[0].bundle_id;
    picker.value = state.selectedBundleId;
  } else {
    state.selectedBundleId = null;
  }
}

function currentBundle() {
  return state.bundles.find((b) => b.bundle_id === state.selectedBundleId) || null;
}

// ------------------------------------------------------------- page: inventory

RENDERERS.inventory = async function renderInventory() {
  const page = document.getElementById("page-inventory");
  page.innerHTML = `<div class="muted">Loading...</div>`;
  await refreshBundles();

  const rows = state.bundles.map((b) => `
    <tr>
      <td class="mono">${escapeHtml(b.bundle_id)}</td>
      <td class="mono">${escapeHtml(b.scenario_id)}</td>
      <td>${bundleStatusBadge(b.status)}</td>
      <td>${b.manifest_approved ? badge("MANIFEST APPROVED", "ok") : badge("NO MANIFEST", "warn")}</td>
      <td>${b.documents.length}</td>
      <td>${b.incompleteness_reason ? escapeHtml(b.incompleteness_reason) : "<span class=\"muted\">-</span>"}</td>
    </tr>`).join("");

  page.innerHTML = `
    <div class="panel">
      <h2>Bundle inventory and completeness</h2>
      <p class="hint">Every bundle and whether its manifest-declared incorporated documents are all present. A bundle can never become analyzable without an approved manifest (plan Section 10.1); an incomplete bundle is a fail-closed non-publishable state, not a partial pass.</p>
      <table>
        <thead><tr><th>Bundle</th><th>Scenario</th><th>Status</th><th>Manifest</th><th># Docs</th><th>Incompleteness reason</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="muted">No bundles yet - create one below.</td></tr>'}</tbody>
      </table>
    </div>

    <div class="panel">
      <h3>Create bundle</h3>
      <div id="create-bundle-msg"></div>
      <div class="row">
        <div class="field"><label>Scenario ID</label><input id="cb-scenario" value="SIM-SCEN-CLAUSEOS-0001"></div>
        <div class="field"><label>Actor ID</label><input id="cb-actor" value="maker.alice@sim.cassandre.test"></div>
        <button class="action" id="cb-submit">Create bundle</button>
      </div>
    </div>

    <div class="panel">
      <h3>Ingest a document</h3>
      <p class="hint">Plain text/Markdown is pasted directly. A PDF file is read and base64-encoded client-side, then sent as <code>content_base64</code> -- the API detects the MIME type from the bytes themselves (Gap 1: born-digital PDF extraction via pdfplumber/pypdf, with OCR-quarantine for image-only scans).</p>
      <div id="ingest-msg"></div>
      <div class="row">
        <div class="field"><label>Bundle</label><select id="ing-bundle"></select></div>
        <div class="field"><label>Document name</label><input id="ing-name" value="CSA"></div>
        <div class="field"><label>Actor ID</label><input id="ing-actor" value="maker.alice@sim.cassandre.test"></div>
      </div>
      <div class="field"><label>Paste text (Markdown/plain text)</label><textarea id="ing-text" placeholder="Paste document text here..."></textarea></div>
      <div class="field"><label>...or choose a PDF file</label><input type="file" id="ing-file" accept="application/pdf"></div>
      <button class="action" id="ing-submit">Ingest document</button>
    </div>

    <div class="panel">
      <h3>Approve manifest</h3>
      <p class="hint">Required before analysis. Declaring a document under "incorporated by reference" that was never ingested moves the bundle to BUNDLE_INCOMPLETE (fail closed).</p>
      <div id="manifest-msg"></div>
      <div class="row">
        <div class="field"><label>Bundle</label><select id="mf-bundle"></select></div>
        <div class="field"><label>Document type</label><input id="mf-doctype" value="CSA"></div>
        <div class="field"><label>Party A</label><input id="mf-partya" value="Cassandre Simulation Bank N.A."></div>
        <div class="field"><label>Party B</label><input id="mf-partyb" value="SIM-COUNTERPARTY-ALPHA"></div>
        <div class="field"><label>Execution date</label><input type="date" id="mf-exec" value="2026-01-15"></div>
        <div class="field"><label>Effective date</label><input type="date" id="mf-eff" value="2026-01-15"></div>
        <div class="field"><label>Incorporated by reference (comma-separated names)</label><input id="mf-incorp" placeholder="Confirmation Annex A"></div>
        <div class="field"><label>Actor ID</label><input id="mf-actor" value="maker.alice@sim.cassandre.test"></div>
        <button class="action" id="mf-submit">Approve manifest</button>
      </div>
    </div>

    <div class="panel">
      <h3>Analyze bundle</h3>
      <p class="hint">Runs the deterministic rule proposer (always) and, optionally, the local Ollama model adapter as an additive third proposer (Gap CLO-X01). The model can never be required for publication and its candidates go through the identical structural-verification + maker/checker path.</p>
      <div id="analyze-msg"></div>
      <div class="row">
        <div class="field"><label>Bundle</label><select id="an-bundle"></select></div>
        <div class="field"><label>Actor ID</label><input id="an-actor" value="maker.alice@sim.cassandre.test"></div>
        <div class="field"><label><input type="checkbox" id="an-model"> include model proposer (Ollama)</label></div>
        <button class="action" id="an-submit">Analyze</button>
      </div>
    </div>
  `;

  ["ing-bundle", "mf-bundle", "an-bundle"].forEach((id) => {
    const sel = document.getElementById(id);
    state.bundles.forEach((b) => sel.appendChild(el(`<option value="${b.bundle_id}">${escapeHtml(b.bundle_id)}</option>`)));
  });

  document.getElementById("cb-submit").addEventListener("click", async () => {
    const msg = document.getElementById("create-bundle-msg");
    try {
      const b = await api("POST", "/v1/bundles", {
        scenario_id: document.getElementById("cb-scenario").value,
        actor_id: document.getElementById("cb-actor").value,
      });
      msg.innerHTML = `<div class="ok-box">Created bundle ${escapeHtml(b.bundle_id)}</div>`;
      await RENDERERS.inventory();
    } catch (e) { msg.innerHTML = errorBox(e.message); }
  });

  document.getElementById("ing-submit").addEventListener("click", async () => {
    const msg = document.getElementById("ingest-msg");
    const bundleId = document.getElementById("ing-bundle").value;
    const documentName = document.getElementById("ing-name").value;
    const actorId = document.getElementById("ing-actor").value;
    const text = document.getElementById("ing-text").value;
    const fileInput = document.getElementById("ing-file");
    try {
      let body;
      if (fileInput.files && fileInput.files.length > 0) {
        const file = fileInput.files[0];
        const buf = await file.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let binary = "";
        bytes.forEach((b) => { binary += String.fromCharCode(b); });
        body = { document_name: documentName, actor_id: actorId, content_base64: btoa(binary) };
      } else {
        body = { document_name: documentName, actor_id: actorId, content: text };
      }
      const bundle = await api("POST", `/v1/bundles/${encodeURIComponent(bundleId)}/documents`, body);
      msg.innerHTML = `<div class="ok-box">Ingested. Bundle now has ${bundle.documents.length} document(s).</div>`;
      await refreshBundles();
    } catch (e) {
      const status = e.body && e.body.ingestion_status;
      msg.innerHTML = errorBox(status ? `${e.message} (ingestion_status=${status})` : e.message);
    }
  });

  document.getElementById("mf-submit").addEventListener("click", async () => {
    const msg = document.getElementById("manifest-msg");
    const bundleId = document.getElementById("mf-bundle").value;
    const incorp = document.getElementById("mf-incorp").value.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      const bundle = await api("POST", `/v1/bundles/${encodeURIComponent(bundleId)}/manifest:approve`, {
        document_type: document.getElementById("mf-doctype").value,
        parties: [document.getElementById("mf-partya").value, document.getElementById("mf-partyb").value],
        execution_date: document.getElementById("mf-exec").value,
        effective_date: document.getElementById("mf-eff").value,
        incorporated_by_reference: incorp,
        actor_id: document.getElementById("mf-actor").value,
      });
      msg.innerHTML = `<div class="ok-box">Bundle status: ${bundleStatusBadge(bundle.status)}</div>`;
      await refreshBundles();
    } catch (e) { msg.innerHTML = errorBox(e.message); }
  });

  document.getElementById("an-submit").addEventListener("click", async () => {
    const msg = document.getElementById("analyze-msg");
    const bundleId = document.getElementById("an-bundle").value;
    try {
      const result = await api("POST", `/v1/bundles/${encodeURIComponent(bundleId)}:analyze`, {
        actor_id: document.getElementById("an-actor").value,
        include_model_proposer: document.getElementById("an-model").checked,
      });
      msg.innerHTML = `<div class="ok-box">Produced ${result.assertions.length} assertion(s). See Evidence Review / Maker Queue.</div>`;
    } catch (e) { msg.innerHTML = errorBox(e.message); }
  });
};

// --------------------------------------------------------------- page: graph

RENDERERS.graph = async function renderGraph() {
  const page = document.getElementById("page-graph");
  await refreshBundles();
  const bundle = currentBundle();
  if (!bundle) { page.innerHTML = `<div class="panel"><h2>Document authority graph</h2><p class="muted">No bundle selected.</p></div>`; return; }

  const manifest = bundle.manifest;
  const nodes = bundle.documents;
  let edgesHtml = "<p class=\"muted\">No amends/supplements/supersedes edges declared on this manifest.</p>";
  let incorpHtml = "<p class=\"muted\">No incorporated-by-reference declarations.</p>";

  if (manifest) {
    if (manifest.related_documents && manifest.related_documents.length > 0) {
      edgesHtml = "<ul>" + manifest.related_documents.map((r) =>
        `<li><span class="chip">${escapeHtml(r.relationship || "RELATED")}</span> -> ${escapeHtml(r.document_name)}</li>`).join("") + "</ul>";
    }
    if (manifest.incorporated_by_reference && manifest.incorporated_by_reference.length > 0) {
      incorpHtml = "<ul>" + manifest.incorporated_by_reference.map((name) => {
        const present = nodes.includes(name);
        return `<li>${escapeHtml(name)} ${present ? badge("PRESENT", "ok") : badge("MISSING", "bad")}</li>`;
      }).join("") + "</ul>";
    }
  }

  page.innerHTML = `
    <div class="panel">
      <h2>Document authority graph</h2>
      <p class="hint">Full <code>AuthorityEdge</code>/legal-precedence traversal (plan Section 10.1) is not implemented in this product yet -- this shows the flat <code>amends/supplements/supersedes</code> declarations and incorporated-by-reference completeness that the domain layer currently models.</p>
      <h3>Documents in bundle</h3>
      <div class="chip-row">${nodes.map((n) => `<span class="chip">${escapeHtml(n)}</span>`).join("") || '<span class="muted">none</span>'}</div>
      <h3>Amends / supplements / supersedes edges</h3>
      ${edgesHtml}
      <h3>Incorporated by reference</h3>
      ${incorpHtml}
    </div>
  `;
};

// ------------------------------------------------------------ page: evidence

async function fetchAssertionsForCurrentBundle() {
  const bundleId = state.selectedBundleId;
  if (!bundleId) return [];
  const data = await api("GET", `/v1/assertions?bundle_id=${encodeURIComponent(bundleId)}`);
  return data.assertions;
}

RENDERERS.evidence = async function renderEvidence() {
  const page = document.getElementById("page-evidence");
  await refreshBundles();
  const bundle = currentBundle();
  if (!bundle) { page.innerHTML = `<div class="panel"><h2>Side-by-side evidence review</h2><p class="muted">No bundle selected.</p></div>`; return; }

  page.innerHTML = `<div class="panel"><h2>Side-by-side evidence review</h2><div class="muted">Loading assertions...</div></div>`;
  const assertions = await fetchAssertionsForCurrentBundle();

  const docCache = {};
  async function getDocText(documentId) {
    if (docCache[documentId]) return docCache[documentId];
    for (const name of bundle.documents) {
      try {
        const doc = await api("GET", `/v1/bundles/${encodeURIComponent(bundle.bundle_id)}/documents/${encodeURIComponent(name)}`);
        docCache[doc.document_id] = doc;
      } catch (e) { /* ignore */ }
    }
    return docCache[documentId];
  }

  const blocks = [];
  for (const a of assertions) {
    const doc = await getDocText(a.evidence.document_id);
    let docHtml = '<span class="muted">document text unavailable</span>';
    let integrity = badge("UNKNOWN", "neutral");
    if (doc && typeof doc.normalized_text === "string") {
      const text = doc.normalized_text;
      const start = a.evidence.start_offset, end = a.evidence.end_offset;
      const actualSlice = text.slice(start, end);
      const matches = actualSlice === a.evidence.snippet;
      integrity = matches ? badge("EVIDENCE VERIFIED", "ok") : badge("EVIDENCE MISMATCH", "bad");
      const before = escapeHtml(text.slice(Math.max(0, start - 300), start));
      const mark = escapeHtml(text.slice(start, end));
      const after = escapeHtml(text.slice(end, end + 300));
      docHtml = `<div class="evidence-doc">...${before}<mark class="${matches ? '' : 'mismatch'}">${mark}</mark>${after}...</div>`;
    }
    blocks.push(`
      <div class="panel two-col">
        <div>
          <h3>${escapeHtml(doc ? doc.document_name : a.evidence.document_id)}</h3>
          ${docHtml}
        </div>
        <div>
          <h3>Assertion</h3>
          <table>
            <tr><th>Concept</th><td>${escapeHtml(a.concept)}</td></tr>
            <tr><th>Proposed value</th><td>${escapeHtml(formatValue(a.proposed_value))}</td></tr>
            <tr><th>Proposer</th><td>${badge(a.proposer, a.proposer === "MODEL" ? "warn" : "neutral")}</td></tr>
            <tr><th>Status</th><td>${badge(a.status, a.status === "CONFLICT" ? "bad" : "neutral")}</td></tr>
            <tr><th>Structural integrity</th><td>${integrity}</td></tr>
            <tr><th>Evidence hash</th><td class="mono">${escapeHtml(a.evidence_content_hash)}</td></tr>
            ${a.conflict_reason ? `<tr><th>Conflict reason</th><td>${escapeHtml(a.conflict_reason)}</td></tr>` : ""}
          </table>
        </div>
      </div>
    `);
  }

  page.innerHTML = `
    <div class="panel">
      <h2>Side-by-side evidence review</h2>
      <p class="hint">Left: source document text with the claimed evidence span highlighted. Right: the assertion it supports. "Structural integrity" is a pass/fail check (the highlighted text either exactly matches the assertion's stored snippet, or it does not) -- never a confidence score.</p>
    </div>
    ${blocks.join("") || '<div class="panel muted">No assertions yet for this bundle. Run Analyze from the Bundle Inventory page.</div>'}
  `;
};

// -------------------------------------------------------- maker/checker queues

function alternativesFor(assertions, a) {
  return assertions.filter((x) => x.concept === a.concept && x.assertion_id !== a.assertion_id);
}

function agreementBadge(assertions, a) {
  const alts = alternativesFor(assertions, a);
  if (alts.length === 0) return badge(a.proposer === "MODEL" ? "MODEL-ONLY" : "RULE-ONLY", "neutral");
  const sameValue = alts.some((x) => JSON.stringify(x.proposed_value) === JSON.stringify(a.proposed_value));
  return sameValue ? badge("PROPOSERS AGREE", "ok") : badge("PROPOSERS DISAGREE", "bad");
}

RENDERERS.maker = async function renderMaker() {
  const page = document.getElementById("page-maker");
  await refreshBundles();
  const bundle = currentBundle();
  if (!bundle) { page.innerHTML = `<div class="panel"><h2>Material-term maker queue</h2><p class="muted">No bundle selected.</p></div>`; return; }

  const [reviews, assertions] = await Promise.all([
    api("GET", `/v1/bundles/${encodeURIComponent(bundle.bundle_id)}/reviews`).then((r) => r.reviews),
    fetchAssertionsForCurrentBundle(),
  ]);
  const byAssertion = Object.fromEntries(assertions.map((a) => [a.assertion_id, a]));
  const open = reviews.filter((r) => r.status === "OPEN");

  const rows = open.map((r) => {
    const a = byAssertion[r.assertion_id];
    return `
      <tr>
        <td class="mono">${escapeHtml(r.review_id)}</td>
        <td>${a ? escapeHtml(a.concept) : "-"}</td>
        <td>${a ? escapeHtml(formatValue(a.proposed_value)) : "-"}</td>
        <td>${a ? badge(a.proposer, a.proposer === "MODEL" ? "warn" : "neutral") : "-"}</td>
        <td>${a ? agreementBadge(assertions, a) : "-"}</td>
        <td>${badge("REQUIRED: MATERIAL TERM", "warn")}</td>
        <td>
          <input class="mono" style="width:170px" placeholder="actor id" id="maker-actor-${r.review_id}" value="maker.alice@sim.cassandre.test">
          <button class="action" data-review="${r.review_id}" data-decision="APPROVE">Approve</button>
          <button class="action secondary" data-review="${r.review_id}" data-decision="REJECT">Reject</button>
        </td>
      </tr>`;
  }).join("");

  page.innerHTML = `
    <div class="panel">
      <h2>Material-term maker queue</h2>
      <p class="hint">Every material term requires maker-then-checker approval before it can appear in a published policy release (plan Section 10.5). "Proposers" shows whether the rule proposer and the optional model proposer agree, disagree, or only one of them fired -- never a confidence percentage.</p>
      <table>
        <thead><tr><th>Review</th><th>Concept</th><th>Proposed value</th><th>Proposer</th><th>Agreement</th><th>Materiality</th><th>Action</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="7" class="muted">No open maker decisions.</td></tr>'}</tbody>
      </table>
      <div id="maker-msg"></div>
    </div>
  `;

  page.querySelectorAll("button[data-review]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const reviewId = btn.dataset.review;
      const decision = btn.dataset.decision;
      const actorId = document.getElementById(`maker-actor-${reviewId}`).value;
      const msg = document.getElementById("maker-msg");
      try {
        await api("POST", `/v1/reviews/${encodeURIComponent(reviewId)}/maker-decision`, { actor_id: actorId, decision });
        msg.innerHTML = `<div class="ok-box">Recorded maker ${decision} for ${escapeHtml(reviewId)}.</div>`;
        await RENDERERS.maker();
      } catch (e) { msg.innerHTML = errorBox(e.message); }
    });
  });
};

RENDERERS.checker = async function renderChecker() {
  const page = document.getElementById("page-checker");
  await refreshBundles();
  const bundle = currentBundle();
  if (!bundle) { page.innerHTML = `<div class="panel"><h2>Checker queue</h2><p class="muted">No bundle selected.</p></div>`; return; }

  const [reviews, assertions] = await Promise.all([
    api("GET", `/v1/bundles/${encodeURIComponent(bundle.bundle_id)}/reviews`).then((r) => r.reviews),
    fetchAssertionsForCurrentBundle(),
  ]);
  const byAssertion = Object.fromEntries(assertions.map((a) => [a.assertion_id, a]));
  const pending = reviews.filter((r) => r.status === "MAKER_APPROVED");

  const rows = pending.map((r) => {
    const a = byAssertion[r.assertion_id];
    const alts = a ? alternativesFor(assertions, a) : [];
    return `
      <tr>
        <td class="mono">${escapeHtml(r.review_id)}</td>
        <td>${a ? escapeHtml(a.concept) : "-"}</td>
        <td>${a ? escapeHtml(formatValue(a.proposed_value)) : "-"}</td>
        <td>${escapeHtml(r.maker.actor_id)} &rarr; ${escapeHtml(r.maker.decision)}</td>
        <td>${alts.length === 0 ? '<span class="muted">none</span>' :
          alts.map((x) => `<div class="chip">${escapeHtml(x.proposer)}: ${escapeHtml(formatValue(x.proposed_value))} (${escapeHtml(x.status)})</div>`).join("")}
        </td>
        <td>
          <input class="mono" style="width:170px" placeholder="actor id (must differ from maker)" id="checker-actor-${r.review_id}" value="checker.bob@sim.cassandre.test">
          <button class="action" data-review="${r.review_id}" data-decision="APPROVE">Approve</button>
          <button class="action secondary" data-review="${r.review_id}" data-decision="REJECT">Reject</button>
        </td>
      </tr>`;
  }).join("");

  page.innerHTML = `
    <div class="panel">
      <h2>Checker queue</h2>
      <p class="hint">Shows the maker's decision and any alternative candidates for the same concept (e.g. rule vs. model), so the checker can see disagreement before approving. Maker and checker must be distinct identities (409 SameActorError if not) and approval is bound to the exact evidence hash at review-open time -- a stale review fails closed rather than silently publishing.</p>
      <table>
        <thead><tr><th>Review</th><th>Concept</th><th>Value</th><th>Maker decision</th><th>Alternatives</th><th>Action</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="muted">No reviews pending checker decision.</td></tr>'}</tbody>
      </table>
      <div id="checker-msg"></div>
    </div>
  `;

  page.querySelectorAll("button[data-review]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const reviewId = btn.dataset.review;
      const decision = btn.dataset.decision;
      const actorId = document.getElementById(`checker-actor-${reviewId}`).value;
      const msg = document.getElementById("checker-msg");
      try {
        await api("POST", `/v1/reviews/${encodeURIComponent(reviewId)}/checker-decision`, { actor_id: actorId, decision });
        msg.innerHTML = `<div class="ok-box">Recorded checker ${decision} for ${escapeHtml(reviewId)}.</div>`;
        await RENDERERS.checker();
      } catch (e) { msg.innerHTML = errorBox(e.message); }
    });
  });
};

// ----------------------------------------------------------- page: conflicts

RENDERERS.conflicts = async function renderConflicts() {
  const page = document.getElementById("page-conflicts");
  await refreshBundles();

  const incompleteBundles = state.bundles.filter((b) => b.status === "BUNDLE_INCOMPLETE");
  const bundle = currentBundle();
  let conflictAssertions = [];
  if (bundle) {
    const assertions = await fetchAssertionsForCurrentBundle();
    conflictAssertions = assertions.filter((a) => a.status === "CONFLICT");
  }

  page.innerHTML = `
    <div class="panel">
      <h2>Unresolved / conflict queue</h2>
      <p class="hint">Bundles that failed completeness (a manifest-declared incorporated document is missing) and assertions whose structural verification failed (offsets/snippet/content hash no longer match the source document).</p>
      <h3>Incomplete bundles (all bundles)</h3>
      <table>
        <thead><tr><th>Bundle</th><th>Reason</th></tr></thead>
        <tbody>${incompleteBundles.map((b) => `<tr><td class="mono">${escapeHtml(b.bundle_id)}</td><td>${escapeHtml(b.incompleteness_reason || "")}</td></tr>`).join("") || '<tr><td colspan="2" class="muted">none</td></tr>'}</tbody>
      </table>
      <h3>Conflicting assertions (selected bundle: ${bundle ? escapeHtml(bundle.bundle_id) : "none"})</h3>
      <table>
        <thead><tr><th>Assertion</th><th>Concept</th><th>Proposer</th><th>Reason</th></tr></thead>
        <tbody>${conflictAssertions.map((a) => `<tr><td class="mono">${escapeHtml(a.assertion_id)}</td><td>${escapeHtml(a.concept)}</td><td>${escapeHtml(a.proposer)}</td><td>${escapeHtml(a.conflict_reason || "")}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">none for this bundle</td></tr>'}</tbody>
      </table>
    </div>
  `;
};

// ---------------------------------------------------------------- page: diff

RENDERERS.diff = async function renderDiff() {
  const page = document.getElementById("page-diff");
  page.innerHTML = `
    <div class="panel">
      <h2>Agreement diff and operational-impact report</h2>
      <div class="stub-note">
        <strong>Not implemented in this product yet.</strong>
        <p>Plan Section 10.7 lists <code>GET /v1/bundles/{bundle_id}/diff?against=</code> as a minimum API; <code>clauseos_api</code> does not currently implement it, and the domain layer has no bundle-to-bundle diff or operational-impact model. This page is an intentional placeholder rather than fabricated output -- wire it up once that endpoint exists.</p>
      </div>
    </div>
  `;
};

// ------------------------------------------------------------ page: releases

RENDERERS.releases = async function renderReleases() {
  const page = document.getElementById("page-releases");
  await refreshBundles();
  page.innerHTML = `<div class="panel"><h2>Published releases and revocations</h2><div class="muted">Loading...</div></div>`;

  const data = await api("GET", "/v1/policy-releases");
  const releases = data.policy_releases;

  const rows = releases.map((r) => `
    <tr>
      <td class="mono">${escapeHtml(r.release_id)}</td>
      <td class="mono">${escapeHtml(r.bundle_id)}</td>
      <td>${badge(r.status, r.status === "PUBLISHED" ? "ok" : r.status === "REVOKED" ? "bad" : "neutral")}</td>
      <td>${r.terms.map((t) => `<div class="chip">${escapeHtml(t.concept)}: ${escapeHtml(formatValue(t.value))} (checker: ${escapeHtml(t.checker_actor_id)})</div>`).join("")}</td>
      <td>
        ${r.status === "PUBLISHED" ? `
          <input class="mono" style="width:170px" placeholder="actor id" id="revoke-actor-${r.release_id}" value="checker.bob@sim.cassandre.test">
          <button class="action danger" data-release="${r.release_id}">Revoke</button>` : '<span class="muted">-</span>'}
      </td>
    </tr>`).join("");

  page.innerHTML = `
    <div class="panel">
      <h2>Publish a policy release</h2>
      <p class="hint">Fails closed (<code>IncompletePolicyError</code>, 422) unless every required material term (THRESHOLD, MTA, NOTIFICATION_DEADLINE) has a checker-approved review bound to its current evidence hash.</p>
      <div id="publish-msg"></div>
      <div class="row">
        <div class="field"><label>Bundle</label><select id="pub-bundle"></select></div>
        <div class="field"><label>Actor ID (checker)</label><input id="pub-actor" value="checker.bob@sim.cassandre.test"></div>
        <button class="action" id="pub-submit">Publish policy release</button>
      </div>
    </div>

    <div class="panel">
      <h2>Published releases and revocations</h2>
      <p class="hint">Every published term shown here passed structural verification and a distinct maker + checker approval bound to that exact evidence hash (plan Section 10.5). Revoking sets the release's status to REVOKED; it does not silently disappear from this list.</p>
      <table>
        <thead><tr><th>Release</th><th>Bundle</th><th>Status</th><th>Terms</th><th>Action</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="muted">No policy releases published yet.</td></tr>'}</tbody>
      </table>
      <div id="release-msg"></div>
    </div>
  `;

  state.bundles.forEach((b) => {
    document.getElementById("pub-bundle").appendChild(el(`<option value="${b.bundle_id}">${escapeHtml(b.bundle_id)}</option>`));
  });

  document.getElementById("pub-submit").addEventListener("click", async () => {
    const msg = document.getElementById("publish-msg");
    const bundleId = document.getElementById("pub-bundle").value;
    try {
      const result = await api("POST", "/v1/policy-releases", {
        bundle_id: bundleId,
        actor_id: document.getElementById("pub-actor").value,
      });
      msg.innerHTML = `<div class="ok-box">Published ${escapeHtml(result.policy_release.release_id)} with ${result.policy_release.terms.length} term(s).</div>`;
      await RENDERERS.releases();
    } catch (e) { msg.innerHTML = errorBox(e.message); }
  });

  page.querySelectorAll("button[data-release]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const releaseId = btn.dataset.release;
      const actorId = document.getElementById(`revoke-actor-${releaseId}`).value;
      const msg = document.getElementById("release-msg");
      try {
        await api("POST", `/v1/policy-releases/${encodeURIComponent(releaseId)}:revoke`, { actor_id: actorId });
        msg.innerHTML = `<div class="ok-box">Revoked ${escapeHtml(releaseId)}.</div>`;
        await RENDERERS.releases();
      } catch (e) { msg.innerHTML = errorBox(e.message); }
    });
  });
};

// --------------------------------------------------------------- page: audit

RENDERERS.audit = async function renderAudit() {
  const page = document.getElementById("page-audit");
  await refreshBundles();
  const bundle = currentBundle();
  if (!bundle) { page.innerHTML = `<div class="panel"><h2>Evidence export and audit verification</h2><p class="muted">No bundle selected.</p></div>`; return; }

  page.innerHTML = `<div class="panel"><h2>Evidence export and audit verification</h2><div class="muted">Loading...</div></div>`;

  const [exportData, auditData] = await Promise.all([
    api("POST", "/v1/evidence-bundles:export", { bundle_id: bundle.bundle_id }),
    api("GET", `/v1/bundles/${encodeURIComponent(bundle.bundle_id)}/audit-events`),
  ]);
  const events = auditData.audit_events;

  let chainBreakIndex = -1;
  for (let i = 1; i < events.length; i++) {
    if (events[i].previous_event_hash !== events[i - 1].event_hash) { chainBreakIndex = i; break; }
  }
  const chainBadge = events.length === 0
    ? badge("NO EVENTS", "neutral")
    : chainBreakIndex === -1 ? badge("CHAIN LINKAGE INTACT", "ok") : badge(`CHAIN BREAK AT #${chainBreakIndex}`, "bad");

  const eventRows = events.map((e, i) => `
    <tr>
      <td>${e.sequence}</td>
      <td>${escapeHtml(e.action)}</td>
      <td>${escapeHtml(e.actor_id)}</td>
      <td class="mono">${escapeHtml((e.event_hash || "").slice(0, 16))}...</td>
      <td class="mono">${escapeHtml((e.previous_event_hash || "(none)").slice(0, 16))}${e.previous_event_hash ? "..." : ""}</td>
      <td>${i === chainBreakIndex ? badge("BREAK", "bad") : ""}</td>
    </tr>`).join("");

  page.innerHTML = `
    <div class="panel">
      <h2>Evidence export</h2>
      <p class="hint">The raw payload a downstream party would receive to independently re-verify this bundle's evidence: document content hashes and every assertion with its evidence span.</p>
      <table>
        <thead><tr><th>Document</th><th>raw_bytes_sha256</th><th>normalized_text_sha256</th></tr></thead>
        <tbody>${exportData.documents.map((d) => `<tr><td>${escapeHtml(d.document_name)}</td><td class="mono">${escapeHtml(d.raw_bytes_sha256.slice(0, 20))}...</td><td class="mono">${escapeHtml(d.normalized_text_sha256.slice(0, 20))}...</td></tr>`).join("")}</tbody>
      </table>
      <p>${exportData.assertions.length} assertion(s) included in export.</p>
    </div>
    <div class="panel">
      <h2>Audit chain verification</h2>
      <p class="hint">${chainBadge} -- this checks that each event's <code>previous_event_hash</code> matches the prior event's <code>event_hash</code> (structural chain-linkage only, computed in this page). It does not re-derive each event's hash from its canonical content client-side; use <code>audit_protocol.chain</code> server-side for full cryptographic re-verification.</p>
      <table>
        <thead><tr><th>#</th><th>Action</th><th>Actor</th><th>event_hash</th><th>previous_event_hash</th><th></th></tr></thead>
        <tbody>${eventRows || '<tr><td colspan="6" class="muted">No audit events.</td></tr>'}</tbody>
      </table>
    </div>
  `;
};

// --------------------------------------------------------------------- init

document.addEventListener("DOMContentLoaded", () => {
  buildShell();
  document.getElementById("bundlePicker").addEventListener("change", (e) => {
    state.selectedBundleId = e.target.value || null;
    const active = document.querySelector("nav button.active");
    if (active) RENDERERS[active.dataset.page]();
  });
  document.getElementById("refreshBundles").addEventListener("click", async () => {
    await refreshBundles();
    const active = document.querySelector("nav button.active");
    if (active) RENDERERS[active.dataset.page]();
  });

  const initial = (location.hash || "#inventory").slice(1);
  showPage(PAGES.some(([id]) => id === initial) ? initial : "inventory");
});
