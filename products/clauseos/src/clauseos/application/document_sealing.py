"""Additive capability: envelope-encrypt (SEC-08) restricted document bytes.

This does NOT replace ClauseOS's existing plain-text `DocumentNode` storage
(`raw_bytes_sha256`/`normalized_text`), which every existing test and the
in-memory/SQLAlchemy repositories still use unchanged. It is a new,
opt-in capability a caller reaches for when it needs to hold a document's
original bytes at rest under envelope encryption rather than in the clear
-- e.g. a future encrypted-blob-store adapter, or an operator wanting to
seal an already-ingested document's bytes for archival.

Uses `cassandre_security.envelope.seal`/`open_` unmodified, with a
`LocalSoftwareKeyCustodyAdapter` -- per that class's own docstring, this is
an **S1 REFERENCE IMPLEMENTATION ONLY**: it holds the KEK in process
memory and is explicitly NOT HSM-backed. One adapter instance is owned by
`DocumentSealingService` (in turn owned by `BundleService`), matching the
package's guidance that a real deployment must swap in a
`KeyCustodyAdapter` backed by a customer-controlled KMS/HSM before this
touches non-synthetic data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cassandre_security.classification import DataClassification
from cassandre_security.envelope import (
    EnvelopeContext,
    KeyCustodyAdapter,
    LocalSoftwareKeyCustodyAdapter,
    SecureEnvelope,
    open_,
    seal,
)

DEFAULT_ORGANIZATION_ID = "SIM-ORG-CLAUSEOS"


def build_document_envelope_context(
    *,
    organization_id: str = DEFAULT_ORGANIZATION_ID,
    legal_entity_id: str | None = None,
    document_id: str,
    object_version: str = "1",
) -> EnvelopeContext:
    """`object_version` defaults to `"1"`: ClauseOS documents are currently
    immutable once ingested (there is no re-ingestion/versioning path for
    an existing `document_id` -- see `clauseos.domain.bundle.AgreementBundle
    .add_document`), so every sealed document is its own first and only
    version. A future multi-version document model would pass the real
    version here instead."""
    return EnvelopeContext(
        organization_id=organization_id,
        product_id="clauseos",
        legal_entity_id=legal_entity_id,
        object_id=document_id,
        object_version=object_version,
        data_class=DataClassification.RESTRICTED,
    )


@dataclass
class DocumentSealingService:
    """Owns the S1-only `LocalSoftwareKeyCustodyAdapter` used to seal/open
    restricted document bytes. Additive: nothing in `BundleService`'s
    existing ingest/read path is required to call this."""

    custody: KeyCustodyAdapter = field(default_factory=LocalSoftwareKeyCustodyAdapter.generate)

    def seal_document_bytes(
        self,
        *,
        raw_bytes: bytes,
        document_id: str,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        legal_entity_id: str | None = None,
        object_version: str = "1",
    ) -> SecureEnvelope:
        context = build_document_envelope_context(
            organization_id=organization_id,
            legal_entity_id=legal_entity_id,
            document_id=document_id,
            object_version=object_version,
        )
        return seal(raw_bytes, context=context, custody=self.custody)

    def open_document_bytes(
        self,
        envelope: SecureEnvelope,
        *,
        document_id: str,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        legal_entity_id: str | None = None,
        object_version: str = "1",
    ) -> bytes:
        """Read path: the context is independently rebuilt from the
        caller's own known document metadata, never trusted from fields
        embedded in the envelope itself (see `envelope.open_`'s docstring)
        -- a swapped envelope with stale/forged labels fails to decrypt
        rather than being silently accepted."""
        context = build_document_envelope_context(
            organization_id=organization_id,
            legal_entity_id=legal_entity_id,
            document_id=document_id,
            object_version=object_version,
        )
        return open_(envelope, context=context, custody=self.custody)
