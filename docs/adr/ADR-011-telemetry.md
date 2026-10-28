# ADR-011: OpenTelemetry instrumentation, vendor-neutral metrics

## Decision
OpenTelemetry traces/metrics; no vendor lock-in. Never export document text,
trade details, positions, account data, prompts, or money values as a metric
label (Section 17.1).

## S1 implementation and known gap
Structured JSON logging with a redaction filter (`packages/audit_protocol/redaction.py`)
is implemented and tested this session. Full OpenTelemetry SDK wiring,
Prometheus/Grafana dashboards, and alert rules (Section 17.2) are **not**
built this session — left as explicit backlog (`FND-010` partial).
