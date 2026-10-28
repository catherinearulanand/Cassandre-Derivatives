# RSK-X01 native valuation challenger (provisional, `DEC-RSK-02`)

Status: **PROVISIONAL / ADDITIVE** — a post-S1 stretch capability (plan
Section 11.6), built as an explicitly **additive**, `CHALLENGER`-purpose-only
capability. It never replaces, gates, or blocks the S1 authoritative
external-MTM `MARGIN_CALL` workflow (`riskos.core.workflow.
execute_margin_call`), and it is not an approved production valuation
methodology. See `docs/DECISION_REGISTER.md` entry `DEC-RSK-02`.

## Scope (plan 11.6, verbatim limitations)

- Supported trade families: a single-currency fixed-for-floating interest
  rate swap (IRS), and one deliverable FX forward. Nothing else.
- IRS: **explicit single-curve limitation** — one flat/bootstrapped discount
  curve per currency, used for both discounting and floating-rate
  projection. No multi-curve stack, no OIS discounting, no basis spreads.
- No options, exotic payoffs, credit derivatives, or cross-currency swaps.
- Every pricing run is pinned to exactly one `MarketSnapshot`
  (`riskos.domain.entities.MarketSnapshot`): an `as_of` date, a set of
  `calendars` (simulation-only labels; no real holiday-calendar business-day
  adjustment is implemented), one `DiscountCurve` per currency needed, and
  the `FxSpotRate`s needed for any FX forward being priced.

## Discounting convention

`riskos.core.challenger_pricing` uses simple-interest annualized zero rates,
linearly interpolated by tenor between a curve's defined points and flat-
extrapolated beyond the first/last point:

```
discount_factor(t) = 1 / (1 + z(t) * t)
```

Chosen over compounded/continuous discounting because (a) it is the simplest
convention that still hand-verifies exactly for this module's golden-case
tests, and (b) it is exactly the formula plan 11.6 gives verbatim for the FX
forward's own discounting step, so the "one curve utility shared by both
pricers" claim is literally true rather than aspirational. Day-count
convention throughout: ACT/365F (`year_fraction`).

## IRS pricing

Fixed leg PV = sum over each remaining payment date `t_i` of
`notional * fixed_rate * accrual_i * DF(t_i)`.

Floating leg: **this module assumes the floating leg reprices to par at
every reset** (the simpler of the two approaches the brief allows, chosen
over full forward-rate projection because it is sufficient to prove the
pricing math at this scope and keeps the golden cases hand-verifiable).
Under the single-curve convention above (same curve for discounting and
projection, no spread), that assumption collapses the floating leg's PV to
the closed form `PV_float = notional * (1 - DF(maturity))`. This holds
exactly when the valuation date coincides with a reset date with no accrued
stub — true for every fixture and golden case in this slice.

Sign: calculation party **receives fixed** → `PV = PV_fixed - PV_float`;
calculation party **pays fixed** → `PV = PV_float - PV_fixed`.

## FX forward pricing

Covered interest rate parity, plan 11.6's stated formula, used verbatim:

```
forward_rate = spot * (1 + r_quote * t) / (1 + r_base * t)
PV = notional_base * (forward_rate - contract_rate) * DF_quote(t)
```

`r_base`/`r_quote` are each currency's own discount-curve zero rate at
`t = year_fraction(as_of, value_date)`. `calculation_party_buys_base=True`
(long the base currency forward) takes the PV as computed; `False` negates
it.

## Money/rounding discipline

`riskos.core.challenger_pricing` operates on `Decimal` only — it never
constructs a `money_types.Money`. The one explicit, documented rounding
step from `Decimal` to `Money` happens in `riskos.core.challenger_workflow`:
round-half-up to 2 decimal places (`ROUND_HALF_UP`, whole cents), tagged
with the `riskos.challenger.RSK-X01-provisional` rounding context.

## Tolerance convention (`DEC-RSK-02`)

Comparing a challenger PV to the synthetic incumbent (external-MTM)
`TradeMTM.mtm` for the same `trade_id` uses a stated, provisional tolerance
— **the greater of**:

- an absolute floor, `CHALLENGER_TOLERANCE_ABS_FLOOR = 25.00` (in the
  incumbent trade's own currency), so small trades are not flagged on noise;
- a relative component, `CHALLENGER_TOLERANCE_RELATIVE_BPS = 10 basis
  points (0.0010)` of `abs(incumbent_pv)`, so large trades get a
  proportionally wider band.

Classification (`riskos.core.challenger_workflow._classify`):

- exact match (same amount, same currency) → `MATCHED`;
- within the tolerance above → `WITHIN_TOLERANCE`;
- otherwise, or a currency mismatch → `BREAK` (a currency mismatch is never
  treated as a rounding artifact worth tolerating away);
- no incumbent `TradeMTM` found for the `trade_id` → `NOT_AVAILABLE`, never
  silently dropped from the comparison set;
- the challenger itself could not price the trade (e.g. no discount curve
  for its currency in the pinned snapshot) → `NOT_AVAILABLE` with an
  explicit `note`, never a crash or a silently-skipped row.

A `ChallengerRun`'s run-level `reconciliation_status` takes the worst
status across its comparisons, in priority order `BREAK` >
`WITHIN_TOLERANCE` > `NOT_AVAILABLE` > `MATCHED`.

## Never replaces the authoritative workflow

- A `ChallengerRun` is a structurally separate aggregate from
  `CalculationRun`/`MarginWaterfall`, stored under its own repository key
  (`save_challenger_run`/`get_challenger_run`, its own `challenger_run`
  table in the SQL adapter) — it cannot overwrite or be confused with a
  `MARGIN_CALL` run.
- `riskos.core.challenger_pricing` / `riskos.core.challenger_workflow` do
  not import, and are not imported by, `riskos.core.workflow` /
  `riskos.core.method_pack` / `riskos.core.exposure` / `riskos.core.gates` /
  `riskos.core.netting` / `riskos.core.population`.
- `ChallengerRun.actionability` is **always** `INDICATIVE_ONLY` regardless
  of comparison outcome: a challenger result is a comparison signal, never
  an actionable margin call. `validation_status` is always
  `RESTRICTED_USE`, consistent with this whole module's provisional status.
- See `products/riskos/tests/test_challenger.py::
  test_challenger_run_does_not_affect_margin_call_run` for the test that
  runs a `MARGIN_CALL` calculation, then a `CHALLENGER` valuation against
  the same netting set/portfolio, and asserts the original `MARGIN_CALL`
  `CalculationRun` (status, waterfall, reconciliation) is byte-for-byte
  unchanged afterward.
