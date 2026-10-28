"""Export idempotency (plan Section 12.8: "Export requires an idempotency
key"; Section 8.4's 409-on-mismatch pattern, mirrored here even in this
in-memory slice)."""

from __future__ import annotations


class IdempotencyConflictError(Exception):
    """Same idempotency key reused with a different request body. Maps to
    HTTP 409 at the API layer -- never silently re-executes with new
    inputs, never silently ignores the second call."""

    def __init__(self, idempotency_key: str):
        self.idempotency_key = idempotency_key
        super().__init__(f"idempotency key {idempotency_key!r} already used with a different request body")


class PreflightFailedError(Exception):
    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__(f"preflight failed: {', '.join(reasons)}")
