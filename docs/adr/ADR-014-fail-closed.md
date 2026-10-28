# ADR-014: Unsupported scope fails explicitly; no best-effort fallback

## Decision
Missing documents, unresolved policy elections, stale snapshots, solver
uncertainty, and approval conflicts return an explicit non-actionable status
(`ABSTAIN`, `BUNDLE_INCOMPLETE`, `METHOD_UNRESOLVED`, `UNDISPUTED_NOT_DETERMINED`,
`DATA_INCOMPLETE`, etc. — plan Sections 10.2, 11.4, 11.5, 12.6). Never a
best-effort guess, never a silent default to zero.

## Enforcement
Each product's domain layer has an explicit closed set of terminal/exception
statuses (see each product's `domain/status.py`); the type system make an
"unhandled" fallthrough a compile-time/type-check error (exhaustive `match`
over a `Literal`/`Enum`), not a runtime `else: return default`.
