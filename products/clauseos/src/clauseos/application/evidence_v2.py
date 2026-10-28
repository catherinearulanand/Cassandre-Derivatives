"""Additive: canonical evidence v2 (SEC-10) for ONE new ClauseOS evidence
artifact type -- policy-release-publication evidence.

This is strictly additive. ClauseOS's EXISTING audit chain
(`audit_protocol.chain`, called from `BundleService._record_audit`) and its
existing signed-envelope format (`clauseos.application.envelope_service`,
built on `cassandre_contracts.build_envelope`/`sign_envelope`) are both
UNTOUCHED by this module -- they remain on their original SHA-256 V1-style
formats and are the objects ClauseOS already treats as authoritative.
Nothing already signed under those formats is migrated or re-verified
here.

`cassandre_security.canonical_evidence` is a distinct, new SHA-384,
multi-signature-ready container (`cassandre.evidence.v2`) this module uses
to produce a SECOND, independent evidence artifact for the same
publication event -- a new artifact type, not a replacement for the
existing audit event or envelope.
"""

from __future__ import annotations

import datetime as dt

from audit_protocol.keys import Ed25519KeyPair
from cassandre_security.canonical_evidence import (
    EvidenceContainer,
    build_protected_body,
    seal_evidence,
    verify_evidence,
)

from clauseos.domain.policy import PolicyRelease

EVIDENCE_PURPOSE_POLICY_RELEASE_PUBLICATION = "clauseos.policy_release_publication"


def seal_policy_release_publication_evidence(
    *,
    release: PolicyRelease,
    tenant_id: str,
    signer_key_set_id: str,
    keypair: Ed25519KeyPair,
    policy_epoch: int,
    valid_from: dt.datetime,
    valid_until: dt.datetime,
) -> EvidenceContainer:
    """Builds and signs a `cassandre.evidence.v2` container for one
    `PolicyRelease` publication -- an additional, new-format evidence
    artifact alongside (not instead of) the existing signed `Envelope`
    that `BundleService.publish_policy_release` already produces."""
    body = build_protected_body(
        payload=release.as_canonical_dict(),
        tenant_id=tenant_id,
        signer_key_set_id=signer_key_set_id,
        object_id=release.release_id,
        object_version="v1",
        purpose=EVIDENCE_PURPOSE_POLICY_RELEASE_PUBLICATION,
        policy_epoch=policy_epoch,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    return seal_evidence(body, ed25519_keypair=keypair)


def verify_policy_release_publication_evidence(
    container: EvidenceContainer,
    *,
    trusted_ed25519_public_key_b64: str,
    now: dt.datetime,
) -> None:
    """Raises `cassandre_security.canonical_evidence.EvidenceVerificationError`
    on any deviation (fails closed) -- see that module for the full list of
    checks (schema version, exact signature-suite match, validity window,
    signature verification)."""
    verify_evidence(container, trusted_ed25519_public_key_b64=trusted_ed25519_public_key_b64, now=now)
