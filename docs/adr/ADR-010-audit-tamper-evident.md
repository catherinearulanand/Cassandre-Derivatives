# ADR-010: Audit chain is tamper-evident, not immutable

## Decision
Every domain mutation writes a hash-chained audit event
(`previous_event_hash` -> `new_event_hash`) inside the same transaction as the
mutation and its outbox row. Daily signed checkpoints (Ed25519) allow
detecting any retroactive edit. This is never described as "immutable" —
Postgres storage can technically be altered by a sufficiently privileged
actor; the chain makes that alteration *detectable*, not impossible.

## S1 implementation
`packages/audit_protocol/chain.py`: `append_event`, `verify_chain`. The
`audit_chain_head` row is locked with `SELECT ... FOR UPDATE` before
allocating the next sequence number, so concurrent writers cannot fork the
chain. The application's database role can `INSERT` audit events but has no
`UPDATE`/`DELETE` grant on the audit table (enforced by Postgres GRANTs, not
just app code).
