# ADR-004: REST/JSON public APIs, versioned schemas, transactional outbox

## Decision
Every product exposes REST/JSON at `/v1/...`. Long-running work uses job
resources (`status: PENDING|RUNNING|SUCCEEDED|FAILED`), never a bare
synchronous call for anything that can exceed a request timeout. Every state
change that must be observed by another component is written to an `outbox`
table in the same DB transaction as the domain mutation.

## S1 implementation
FastAPI + Pydantic v2 for schema generation and validation. Outbox rows are
polled with `SELECT ... FOR UPDATE SKIP LOCKED`; consumers dedupe by the
outbox row's immutable `event_id`. No message broker in S1 (plan explicitly
allows this).
