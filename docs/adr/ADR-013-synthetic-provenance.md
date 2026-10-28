# ADR-013: Synthetic provenance is mandatory and machine-enforced

## Decision
Every fixture, result, screen, and export carries
`simulation_provenance.is_simulated: true` plus a `scenario_id` and
`generator_version`. This is not a UI label applied late — it is a required
field on the envelope schema (Section 8.1) that fails validation if absent.

## S1 implementation
`packages/cassandre_contracts/envelope.py` makes `simulation_provenance` a
required, non-defaultable field. Any attempt to construct an envelope without
it raises a Pydantic `ValidationError` at construction time, not at display
time.
