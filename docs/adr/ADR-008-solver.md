# ADR-008: HiGHS LP/MIP with independent domain verification

## Decision
CollateralOS planning uses HiGHS for LP preview and MIP allocation. Every
solver result is independently re-verified in exact `Decimal` arithmetic
against the full hard-constraint set before it may be reserved. A solver
"optimal"/"infeasible" status is never surfaced to the API without that
independent verification passing.

## S1 implementation
Attempted via `highspy`; if it fails to install cleanly on this machine within
this session, fall back to `scipy.optimize.linprog` (HiGHS backend is
`scipy`'s default LP method since 1.6) for the LP preview, and a small
brute-force/greedy MIP for the tiny 20-position slice, with the same
independent verifier applied regardless of which solver produced the
candidate. Whichever path is used is recorded in the release manifest,
never silently substituted.
