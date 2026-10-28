"""Structured-log redaction, per plan Section 17.1.

Never export document text, trade details, positions, account data, model
prompts, or money values as log/metric content. This filter redacts a
declared set of sensitive keys from any dict-shaped log record before it
leaves the process.
"""

from __future__ import annotations

import logging
from typing import Any

DEFAULT_SENSITIVE_KEYS = frozenset({
    "document_text", "extracted_text", "prompt", "model_input", "model_output",
    "trade_details", "position", "account_number", "amount", "money", "mtm",
    "ssn", "password", "secret", "private_key", "signing_key",
})

REDACTED_MARKER = "[REDACTED]"


def redact(record: dict[str, Any], sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS) -> dict[str, Any]:
    def _redact_value(key: str, value: Any) -> Any:
        if key.lower() in sensitive_keys:
            return REDACTED_MARKER
        if isinstance(value, dict):
            return {k: _redact_value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact_value(key, v) for v in value]
        return value

    return {k: _redact_value(k, v) for k, v in record.items()}


class RedactingFilter(logging.Filter):
    def __init__(self, sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS) -> None:
        super().__init__()
        self._sensitive_keys = sensitive_keys

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "structured") and isinstance(record.structured, dict):
            record.structured = redact(record.structured, self._sensitive_keys)
        return True
