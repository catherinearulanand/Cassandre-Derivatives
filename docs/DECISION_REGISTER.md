# Decision register

Per plan Section 26: defaults must not be invented silently. Each entry states
owner, deadline, blocking work IDs, and state. `PROVISIONAL` means a synthetic
placeholder value was used so the initial slice has something concrete to
compute against — it is not presented as an approved production convention and
every usage in code links back to the entry ID below.

| ID | Decision | Blocks | State | Chosen placeholder (if provisional) |
| --- | --- | --- | --- | --- |
| DEC-ENV-01 | Target pilot hardware / OS (plan assumes Linux workstation; this session runs Windows + Docker Desktop) | FND-011 hardening claims | PROVISIONAL | Docker Compose (Linux containers) on the developer's Windows host; host-level Linux hardening (AppArmor/seccomp, rootless) not asserted until run on the declared Linux target |
| DEC-SIM-01 | Fictional institution name / identifier namespace | SIM-001 | APPROVED (plan already names it; rebranded from AETHER to Cassandre) | `CASSANDRE_SIM_BANK_NA` / `CASSANDRE_SIM_BANK_IFSC`, `SIM-*` prefix |
| DEC-CLO-01 | Whether local-model inference is included in S1, or replay/rules-only | CLO-X01 | APPROVED (plan Section 10.4/25.2 already directs this) | S1 session: replay + deterministic rules only, no model adapter. Later session: `CLO-X01` model adapter added (`core/model_adapter.py`, Ollama `llama3.2:1b` over HTTP) as a strictly additive third proposer per ADR-006 -- never required for publication, abstains (proposes nothing) on any failure, output still gated by the unchanged maker/checker workflow |
| DEC-RSK-01 | Exact CSA sign/threshold/MTA/rounding convention for the one method pack built in this slice | RSK-006, RSK-008 | PROVISIONAL | See `docs/methods/METHOD-PACK-001-provisional.md`: symmetric USD threshold, MTA-before-rounding, round-half-up to whole USD, bilateral IA=0 |
| DEC-COL-01 | Collateral eligibility / concentration / wrong-way rule set for the one policy bundle built in this slice | COL-004, COL-005 | PROVISIONAL | See `docs/controls/POLICY-BUNDLE-001-provisional.md`: cash + UST only eligible, 40% single-issuer concentration cap, self/affiliate-issued excluded |
| DEC-COL-02 | Solver time limit / acceptable optimality gap for simulation | COL-007 | PROVISIONAL | 5s wall-clock, 1% relative gap accepted as `FEASIBLE_BOUNDED_GAP` |
| DEC-OPS-01 | Retention and backup policy | FND-011 | OPEN | not built this session (backup/restore drills are Phase 7 scope) |
| DEC-OPS-02 | Reconciliation tolerances (RiskOS break classification, CollateralOS coverage tolerance) | RSK-009, COL-005 | PROVISIONAL | RiskOS: exact-match only, any difference is a BREAK; CollateralOS: 0.01% conservative coverage tolerance |
| DEC-OPS-03 | Freshness windows for market/inventory/collateral snapshots | RSK-005, COL-002/003 | PROVISIONAL | 24h for this slice's fixtures |
| DEC-GOV-01 | Maker/checker role matrix beyond the roles table in Section 13 | FND-005 | APPROVED (plan Section 13 table adopted verbatim) | n/a |
| DEC-S2-01 | Criteria for seeking a real design partner | out of scope | OPEN | Not addressed; S2 is out of this plan's scope per Section 1.4 |
| DEC-RSK-02 | RSK-X01 native valuation challenger's challenger-vs-incumbent tolerance convention | RSK-X01 | PROVISIONAL | See `docs/methods/RSK-X01-challenger-provisional.md`: greater of USD 25.00 absolute or 10 bps of `abs(incumbent_pv)`; additive `CHALLENGER`-purpose-only, never gates or replaces the `MARGIN_CALL` workflow |

Anything marked `OPEN` blocks the corresponding work item from claiming
completion beyond this session's provisional slice. Anything marked
`PROVISIONAL` is safe only because every consumer of this system in S1 is
synthetic; none of these values may carry forward to S2 without the real
project-owner approval process in Section 26.
