# Eligibility policy bundle `SIM-POLICY-001` (provisional, DEC-COL-01)

Status: **PROVISIONAL** — placeholder convention for the initial synthetic
slice only. See `docs/DECISION_REGISTER.md` entry `DEC-COL-01`.

- Eligible asset classes: `CASH_USD`, `CASH_EUR`, `US_TREASURY`.
- Ineligible always: any position flagged `wrong_way`, `self_issued`,
  `affiliate_issued`, `encumbered`, `recalled`, or with a stale price
  (`stale_after` in the past relative to snapshot `as_of`).
- Haircuts (adjusted value = market value x (1 - haircut)):
  - `CASH_USD`: 0%
  - `CASH_EUR`: 2% (FX haircut)
  - `US_TREASURY`: 2% flat, no maturity buckets in this slice.
- Concentration: single-issuer group post-transfer pool cap of 40% of total
  delivered adjusted value; numerator/denominator computed post-haircut,
  post-transfer (existing held collateral + proposed delivery), per plan
  Section 12.3.
- Wrong-way: deterministic mapping table
  `docs/controls/wrong-way-map-001.json` (counterparty -> excluded issuer
  groups); no correlation-based inference.
- Segregation/reuse/substitution: not modeled in this slice (single account,
  title-transfer assumed); flagged as scope gap, not silently assumed safe.
- Minimum lot: USD 1,000 cash increments; UST in $1,000 face increments.
- Coverage tolerance (DEC-OPS-02): adjusted delivered value must cover
  obligation amount within 0.01%, conservative (round required coverage up,
  delivered value down) per plan Section 12.5.
