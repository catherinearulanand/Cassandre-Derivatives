"""AgreementBundle, DocumentNode, BundleManifest.

Plan Section 10.1: "AgreementBundle cannot become analyzable until its
user-approved manifest declares document type, parties, execution/effective
date, and amends/supplements/supersedes relationships. ClauseOS does not
infer legal precedence solely from chronology." That rule is enforced here:
`AgreementBundle.approve_manifest` is the only way to attach a manifest, and
`analyze()` raises `ManifestNotApprovedError` if it has not been called.

No clock calls in this module: every timestamp is supplied by the caller
(the application layer), never computed here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

from clauseos.domain.errors import (
    BundleIncompleteError,
    InvalidTransitionError,
    ManifestNotApprovedError,
)
from clauseos.domain.status import BundleStatus

DocumentRelationship = Literal["AMENDS", "SUPPLEMENTS", "SUPERSEDES"]


@dataclass(frozen=True)
class DocumentReference:
    """A reference by name to another document in (or claimed by) the
    bundle -- used both for amends/supplements/supersedes edges and for
    incorporated-by-reference declarations."""

    document_name: str
    relationship: DocumentRelationship | None = None


@dataclass(frozen=True)
class BundleManifest:
    document_type: str
    parties: tuple[str, ...]
    execution_date: dt.date
    effective_date: dt.date
    related_documents: tuple[DocumentReference, ...] = field(default_factory=tuple)
    incorporated_by_reference: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.document_type.strip():
            raise ValueError("document_type is required")
        if len(self.parties) < 2:
            raise ValueError("a bilateral agreement manifest requires at least two parties")


@dataclass(frozen=True)
class DocumentNode:
    document_id: str
    bundle_id: str
    document_name: str
    raw_bytes_sha256: str
    normalized_text: str
    normalized_text_sha256: str


@dataclass
class AgreementBundle:
    bundle_id: str
    scenario_id: str
    status: BundleStatus
    created_at: dt.datetime
    documents: dict[str, DocumentNode] = field(default_factory=dict)
    manifest: BundleManifest | None = None
    manifest_approved: bool = False
    incompleteness_reason: str | None = None
    version: int = 0

    def _require_status(self, *allowed: BundleStatus) -> None:
        if self.status not in allowed:
            raise InvalidTransitionError(
                f"bundle {self.bundle_id} cannot transition from {self.status.value}; "
                f"allowed source states: {[s.value for s in allowed]}"
            )

    def add_document(self, document: DocumentNode) -> None:
        self._require_status(BundleStatus.CREATED, BundleStatus.INGESTING)
        if document.bundle_id != self.bundle_id:
            raise ValueError("document.bundle_id does not match this bundle")
        self.documents[document.document_name] = document
        self.status = BundleStatus.INGESTING
        self.version += 1

    def approve_manifest(self, manifest: BundleManifest) -> None:
        """Attach the user-approved manifest. Required before `analyze()`,
        per plan Section 10.1. Does not itself check document completeness --
        that is `check_completeness()`, run as part of moving to PARSED."""
        self._require_status(BundleStatus.CREATED, BundleStatus.INGESTING)
        self.manifest = manifest
        self.manifest_approved = True
        self.version += 1

    def check_completeness(self) -> None:
        """Plan Section 9.2 item 11 / Section 10.2: a manifest that declares
        an incorporated-by-reference document not present in the bundle
        moves the bundle to the non-publishable `BUNDLE_INCOMPLETE` state,
        not silently forward to PARSED."""
        self._require_status(BundleStatus.INGESTING)
        if not self.manifest_approved or self.manifest is None:
            raise ManifestNotApprovedError(
                f"bundle {self.bundle_id} has no approved manifest"
            )
        missing = [
            name
            for name in self.manifest.incorporated_by_reference
            if name not in self.documents
        ]
        if missing:
            self.status = BundleStatus.BUNDLE_INCOMPLETE
            self.incompleteness_reason = (
                "manifest declares incorporated-by-reference document(s) not present "
                f"in bundle: {missing}"
            )
            self.version += 1
            raise BundleIncompleteError(self.incompleteness_reason)
        self.status = BundleStatus.PARSED
        self.version += 1

    def mark_analyzed(self) -> None:
        self._require_status(BundleStatus.PARSED)
        if not self.manifest_approved:
            raise ManifestNotApprovedError(
                f"bundle {self.bundle_id} cannot be analyzed without an approved manifest"
            )
        self.status = BundleStatus.ANALYZED
        self.version += 1

    def require_review(self) -> None:
        self._require_status(BundleStatus.ANALYZED)
        self.status = BundleStatus.REVIEW_REQUIRED
        self.version += 1

    def mark_validated(self) -> None:
        self._require_status(BundleStatus.REVIEW_REQUIRED)
        self.status = BundleStatus.VALIDATED
        self.version += 1

    def mark_approved(self) -> None:
        self._require_status(BundleStatus.VALIDATED)
        self.status = BundleStatus.APPROVED
        self.version += 1

    def mark_published(self) -> None:
        self._require_status(BundleStatus.APPROVED)
        self.status = BundleStatus.PUBLISHED
        self.version += 1
