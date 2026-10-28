# simulation_factory

The S1 simulation factory for the Cassandre Platform: a reusable, larger
synthetic corpus generator, per plan Section 9 ("Simulation factory") of
`AETHER-Controlled-Pilot-Implementation-Plan.md` (the plan still says
"AETHER" throughout; the fictional institution is now `Cassandre Simulation
Bank` -- see `docs/INTERPRETATION.md`'s naming note).

This package builds work items **SIM-002 through SIM-009**, which
`docs/INTERPRETATION.md` explicitly left as backlog ("full corpus SIM-002
through SIM-009 ... is left as backlog"). SIM-001 (the synthetic
institution and `SIM-*` namespace) was already built and is unchanged here
(`institution.py`, `identifiers.py`).

## What's built, work item by work item

| Work item | Module | What it produces |
| --- | --- | --- |
| SIM-002 | `corpus/users.py` | 8 synthetic users covering all 16 plan-Section-13 roles (2 roles/user, zero maker/checker collisions), 6 custody accounts, 6 SSIs, 5 settlement calendars |
| SIM-003 | `corpus/agreements.py` | 12 synthetic agreement bundles (one per plan Section 9.2 coverage item), 5 designated `RISKOS_CALCULABLE_S1`, each with an independently authored ground-truth manifest, content-hashed with `audit_protocol.canonical.content_hash` |
| SIM-004 | `corpus/trades.py` | 300 synthetic trades (IRS + FX forwards), every lifecycle state, exact netting-set membership, and duplicate/wrong-entity/wrong-sign/unrecognized-counterparty/unsupported-product traps plus a "missing trade" control case |
| SIM-005 | `corpus/market.py` | 30 immutable daily market snapshots (10 quiet, 10 rate-shock, 5 FX-stress, 5 combined-liquidity-stress) with FX quotes, a discount-factor curve, and deliberate stale/missing/crossed-data negative cases |
| SIM-006 | `corpus/inventory.py` | 600 synthetic inventory positions across cash (USD/EUR/INR), UST, synthetic sovereigns, corporate bonds, equities, and gold, with all nine plan-Section-9.5 trap categories |
| SIM-007 | `scenario_catalogue.py` | The exact plan Section 9.6 scenario table (16 rows) as data, plus a pure-Python fixture-selection runner |
| SIM-008 | `independent_reference/trade_population_reference.py` | The independently implemented reference script that computes SIM-004's source-control manifest -- see "Independence of SIM-008" below |
| SIM-009 | `adversarial_index.py` | One consolidated, `SIM-*`-namespaced index of every deliberately broken fixture across SIM-003 through SIM-006 |

Supporting modules:

- `provenance.py` -- the `SimulationProvenance` dataclass (`is_simulated`,
  `scenario_id`, `generator_version`), mirroring
  `cassandre_contracts.envelope.SimulationProvenance`'s shape without
  importing `cassandre_contracts` for it, per the work item spec.
- `serialization.py` -- the deterministic JSON serialization boundary.
  Enforces "never a JSON float for money": a bare Python `float` anywhere
  in a generated object graph raises `SerializationError` at
  serialization time.
- `corpus/generate_all.py` -- the entry point that writes every corpus to
  real files under `schemas/fixtures/`.

## How to regenerate the corpus

From the repository root:

```
uv run python -m simulation_factory.corpus.generate_all
```

or call `simulation_factory.corpus.generate_all.generate_all(output_root)`
from another script (it defaults to `schemas/fixtures/` at the repo root
when `output_root` is omitted). Both paths are deterministic: every
generator seeds its own `random.Random(seed)` (never bare `random`), and
`tests/test_determinism.py::test_full_generation_round_trip_is_byte_identical`
asserts two independent runs produce byte-identical files.

## Generated output layout (`schemas/fixtures/`)

```
schemas/fixtures/
  users/static_data.json          -- 8 users, 6 custody accounts, 6 SSIs, 5 calendars
  agreements/
    index.json                    -- one row per bundle: id, title, coverage item,
                                      family, riskos_calculable, ground_truth_content_hash
    sim-agr-bundle-0NN/
      *.md                        -- the bundle's synthetic document(s)
      ground_truth.json           -- independently authored expected material terms
                                      + their content_hash
  trades.json                     -- counterparties, netting sets, 300 trades,
                                      and the SIM-008-computed source-control manifest
  market_snapshots/
    index.json
    sim-mkt-snap-0NN.json         -- one file per daily snapshot (30 files)
  inventory.json                  -- 600 positions
  scenario_catalogue.json         -- the 16-row Section 9.6 table
  adversarial_index.json          -- the consolidated SIM-009 index
```

## Exact counts as generated in this session

- Users: **8** (covering all 16 Section 13 roles)
- Custody accounts: **6**; SSIs: **6**; settlement calendars: **5**
- Agreement bundles: **12** (`RISKOS_CALCULABLE_S1`: **5**)
- Trades: **300** (250 ACCEPTED, 15 MATURED, 10 CANCELLED, 10 AMENDED,
  5 DUPLICATE, 8 REJECTED [3 WRONG_ENTITY, 3 WRONG_SIGN,
  2 UNRECOGNIZED_COUNTERPARTY], 2 UNSUPPORTED, plus 2 expected-but-missing
  trade IDs recorded only in the manifest)
- Market snapshots: **30** (10 QUIET, 10 RATE_SHOCK, 5 FX_STRESS,
  5 COMBINED_LIQUIDITY_STRESS)
- Inventory positions: **600** (90 CASH_USD, 60 CASH_EUR, 50 CASH_INR,
  120 US_TREASURY, 70 SYNTHETIC_SOVEREIGN, 90 CORPORATE_BOND, 70 EQUITY,
  50 GOLD)
- Scenario catalogue rows: **16**
- Adversarial index cases: **815** (run `uv run python -m
  simulation_factory.corpus.generate_all` and inspect
  `schemas/fixtures/adversarial_index.json` for the current exact count --
  it is a derived count, not a hand-picked target, and will change if the
  generators change)

Re-running the generator reproduces every one of these counts exactly
(see the determinism tests).

## Independence of SIM-008 (why it's a separate module)

Plan Section 9.3, verbatim: *"Never generate expected RiskOS totals using
the same implementation under test. Use hand-derived small cases and an
independently implemented reference script for larger synthetic
portfolios."*

`independent_reference/trade_population_reference.py`:

- is never imported by `riskos`, and never imports `riskos`,
  `clauseos`, or `collateralos` (enforced by
  `tests/test_independent_reference.py::test_reference_module_never_imports_riskos_or_any_product`,
  which walks the module's AST rather than trusting a docstring claim);
- does not import `simulation_factory.corpus.trades` for its bucket-size
  constants -- it recomputes every total by walking the actual trade
  sequence field-by-field (`tests/test_independent_reference.py::test_reference_module_does_not_import_the_generator_module`);
- is a single flat loop over plain attribute reads and a running `Decimal`
  total -- deliberately not a shared "aggregate()" helper any product
  might also come to depend on.

`riskos_fixtures.py`'s five-trade scenario (already in
`products/riskos/tests/`) is the plan's "hand-derived small case" half of
the same sentence; this package's reference script is the "independently
implemented reference script for larger synthetic portfolios" half, sized
for SIM-004's 300-trade population.

## Determinism and provenance, concretely

- Every non-trivial generator seeds `random.Random(<fixed default seed>)`;
  `build_trade_corpus`, `build_market_scenarios`, and
  `build_inventory_corpus` all accept an explicit `seed` parameter for
  tests that want to vary it.
- Every identifier is validated through
  `simulation_factory.identifiers.require_sim_identifier` at construction
  time (a dataclass `__post_init__`), not just checked after the fact.
- Every generated object that plan Section 9/ADR-013 requires to carry
  simulation provenance carries a `SimulationProvenance` field
  (`is_simulated=True`, `scenario_id`, `generator_version`); `is_simulated`
  cannot be constructed as anything other than `True`.
- Every money/quantity field is `money_types.Money` / `money_types.Quantity`
  (never a float), and the serialization boundary
  (`serialization.to_jsonable`) raises on a bare Python `float` anywhere in
  the object graph.
- No real company/LEI/BIC/ISIN/account name appears anywhere; every
  synthetic name reads as obviously synthetic ("SIM Counterparty Alpha",
  "SIM Corporate Issuer" style issuer-group ids, etc.) and
  `tests/test_namespace_compliance.py::test_no_real_identifier_traps_anywhere`
  greps the full serialized corpus for a short list of real-institution
  name/CUSIP traps as a belt-and-suspenders check.

## Test suite

`uv run pytest packages/simulation_factory -q` -- **89 tests**, covering:

- determinism (same seed -> byte-identical output, twice-run
  `generate_all()` -> byte-identical files on disk);
- `SIM-*` namespace compliance for every identifier in every corpus;
- corpus size targets (300 trades, 600 positions, 8-12 bundles with 4-6
  calculable, 30 market snapshots, 6-10 users covering all 16 roles);
- no real-identifier collisions;
- SIM-008's independence from both `riskos` and this package's own
  generator module;
- per-corpus domain invariants (maker/checker distinctness, reservable-
  quantity formula, wrong-way/stale/missing-quote negative cases, etc.).

## What is explicitly NOT done here (integration remains someone else's job)

This package generates and validates the corpus. It does **not**:

- wire any of this data into `products/clauseos`, `products/riskos`, or
  `products/collateralos`'s own test suites or fixtures -- those three
  products currently each carry their own small hand-authored fixture
  (1 CSA bundle / 5 trades / 20 inventory positions) and continue to do so;
  swapping or extending those fixtures with this larger corpus is a
  separate, future integration task, not attempted in this session;
- expose any API, persist anything to Postgres, or touch `apps/` in any
  way;
- validate that a product's own aggregation/reconciliation logic actually
  reconciles correctly against this corpus (that is exactly the kind of
  test a future integration step would write, using this corpus as its
  input, in each product's own `tests/` directory);
- assert anything about `products/` or `apps/` import boundaries beyond
  what `scripts/check_import_graph.py` (unmodified by this work) already
  enforces.

This package's own `pyproject.toml` depends only on `money_types`,
`cassandre_contracts` (for the sibling-shape reference, not required at
runtime by any module here), and `audit_protocol` (for
`canonical.content_hash`) -- no product package is a dependency in either
direction.
