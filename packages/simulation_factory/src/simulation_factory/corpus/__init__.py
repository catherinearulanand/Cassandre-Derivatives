"""Corpus generators for the S1 simulation factory (plan Section 9,
work items SIM-002 through SIM-006). Each submodule is deterministic from a
fixed `random.Random(seed)` and produces plain dataclasses that
`simulation_factory.serialization` can write to `schemas/fixtures/`.
"""
