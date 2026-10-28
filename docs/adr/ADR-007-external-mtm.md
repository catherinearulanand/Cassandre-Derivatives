# ADR-007: External trade-level MTMs are authoritative in S1

## Decision
RiskOS never derives MTM itself in S1; MTMs are ingested from a synthetic
"incumbent system" file (JSON/CSV). Native valuation is an optional post-S1
`CHALLENGER` purpose that can never replace or gate the external-MTM path.

## S1 implementation
`riskos` ingestion validates the external MTM file against a source-control
manifest (accepted/rejected/duplicate/matured/cancelled/amended/unsupported
counts, Section 9.3) before any aggregation runs. No pricing library is
implemented this session.
