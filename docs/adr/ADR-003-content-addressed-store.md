# ADR-003: Content-addressed encrypted filesystem for documents and bundles

## Decision
Original documents and immutable input/output bundles are stored by SHA-256
content hash under a per-product root directory, written via
temp-file-fsync-verify-rename (Section 6.5).

## S1 implementation
`packages/audit_protocol/content_store.py`: `put(bytes) -> sha256 hex`,
`get(hash) -> bytes` with hash re-verification on every read. Backed by a
local encrypted-at-rest volume in this slice (host disk encryption assumed,
not application-layer encryption — flagged, not claimed as done). Metadata
(owner product, retention state, quarantine state) lives in the owning
product's Postgres database, never inferred from the filesystem path.

## Known deviation this session
No off-host encrypted backup target is wired up (Phase 7 / FND-011 scope).
