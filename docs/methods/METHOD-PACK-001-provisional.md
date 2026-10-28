# Method pack `SIM-CSA-SYMMETRIC-USD-001` (provisional, DEC-RSK-01)

Status: **PROVISIONAL** — placeholder convention for the initial synthetic
slice only. Not an approved production margin methodology. See
`docs/DECISION_REGISTER.md` entry `DEC-RSK-01`.

- Calculation-party / counterparty perspectives: bilateral, symmetric.
- Exposure definition: sum of trade-level MTM within the one approved netting
  set, calculation-party perspective (positive = calculation party is owed).
- Threshold: USD 0 for both parties (zero-threshold CSA), per party.
- Independent Amount: bilateral, `0` for both parties in this slice.
- Credit Support Balance: net collateral currently held minus posted, from the
  calculation party's perspective, USD.
- MTA: USD 10,000, applies per party, same for both directions.
- MTA/rounding order: **MTA-before-rounding** (compare raw delivery/return
  amount to MTA first, then round).
- Rounding: round-half-up to whole USD.
- Delivery Amount = `max(0, Exposure - Threshold - CreditSupportBalance)`,
  return only if the resulting amount is below `-MTA`.
- Transfer/call currency: USD only in this slice.
- Zero/negative behavior: an amount that nets to exactly zero or is below MTA
  produces no call (`NO_TRANSFER`), not a zero-dollar call.
- Dispute/prior-acknowledgement: not modeled in this slice beyond the
  reconciliation break classification in Section 11.3; undisputed-amount
  determination uses only the "both parties agree on direction and amount"
  case — every other case returns `UNDISPUTED_NOT_DETERMINED` per plan
  Section 11.5, never a computed guess.
- Supported agreement family: the single synthetic zero-threshold bilateral
  CSA bundle built for this slice. Any other bundle/election returns
  `UNSUPPORTED_AGREEMENT`.
