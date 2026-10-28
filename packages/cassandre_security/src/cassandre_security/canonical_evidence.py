"""Canonical evidence v2 and strict signature verification (SEC-10/13).

Spec section 8.4: "New Cassandre PQ evidence uses a versioned SHA-384
content-commitment profile by default... The proposed application profile
requires BOTH independent signatures on the same canonical signed body...
The earlier SHA-256 AETHER V1 format remains a labeled historical
verification profile and cannot satisfy creation of new PQ-profile
evidence... Merely adding ML-DSA over an old SHA-256 commitment does not
upgrade the commitment."

The container's `signatures` map is keyed by algorithm name and always
supported both `"ed25519"` and `"ml-dsa-65"` slots by design -- as of
2026-09-15, ML-DSA-65 signing (`pqc.py`, backed by the reviewed
`quantcrypt` library) is implemented, so `seal_evidence` now accepts an
OPTIONAL `ml_dsa_keypair` parameter: omit it and behavior is byte-for-byte
identical to before (ed25519-only, fully backward compatible with every
existing caller in ClauseOS/RiskOS/CollateralOS); supply it and the
container carries a genuine second, independently-verifiable ML-DSA-65
signature over the same canonical body. `REQUIRED_SIGNATURE_ALGORITHMS_V2`
(the classical-only baseline) is UNCHANGED to avoid breaking existing
callers; `REQUIRED_SIGNATURE_ALGORITHMS_V2_HYBRID` is the new opt-in
"strict profile" spec 8.4 describes, requiring both signatures.

The existing `audit_protocol` V1 format (SHA-256, single Ed25519, domain
tag "CASSANDRE-ENVELOPE-V1") is untouched and remains the historical
verification profile for objects already signed under it.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
from dataclasses import dataclass, field

import nacl.exceptions
import nacl.signing

from audit_protocol import canonicalize
from audit_protocol.keys import Ed25519KeyPair

from cassandre_security.pqc import MlDsaKeyPair, mldsa_sign, mldsa_verify

EVIDENCE_SCHEMA_VERSION = "cassandre.evidence.v2"
DOMAIN_TAG_V2 = b"CASSANDRE-EVIDENCE-V2\n"
DIGEST_ALGORITHM_DEFAULT = "sha384"
DIGEST_ALGORITHM_STRONG = "sha512"  # opt-in only, per spec 8.4

# The verifier's required-algorithm set is LOCAL TRUSTED POLICY, never
# read from the untrusted payload itself (spec: "Verify required
# algorithms from local trusted policy, not untrusted payload
# negotiation.").
REQUIRED_SIGNATURE_ALGORITHMS_V2: frozenset[str] = frozenset({"ed25519"})

# Opt-in "strict profile" (spec 8.4): BOTH signatures required. Existing
# callers that don't ask for this keep working unchanged.
REQUIRED_SIGNATURE_ALGORITHMS_V2_HYBRID: frozenset[str] = frozenset({"ed25519", "ml-dsa-65"})


class EvidenceError(ValueError):
    pass


class EvidenceVerificationError(EvidenceError):
    pass


def _digest(data: bytes, *, algorithm: str) -> bytes:
    if algorithm == "sha384":
        return hashlib.sha384(data).digest()
    if algorithm == "sha512":
        return hashlib.sha512(data).digest()
    raise EvidenceError(f"unsupported digest algorithm {algorithm!r}")


@dataclass(frozen=True)
class ProtectedBody:
    """The canonical signed body (spec 8.4): "Bind the digest algorithm and
    value, protocol version, signature suite, tenant, signer key-set
    identity, object/purpose, validity, and policy epoch in that body."
    Does NOT include the signature bytes themselves -- those live in the
    outer container.
    """

    schema_version: str
    digest_algorithm: str
    content_digest_hex: str
    signature_suite: tuple[str, ...]  # e.g. ("ed25519",) -- sorted, no duplicates
    tenant_id: str
    signer_key_set_id: str
    object_id: str
    object_version: str
    purpose: str
    policy_epoch: int
    valid_from: dt.datetime
    valid_until: dt.datetime

    def canonical_bytes(self) -> bytes:
        return canonicalize(
            {
                "schema_version": self.schema_version,
                "digest_algorithm": self.digest_algorithm,
                "content_digest_hex": self.content_digest_hex,
                "signature_suite": list(self.signature_suite),
                "tenant_id": self.tenant_id,
                "signer_key_set_id": self.signer_key_set_id,
                "object_id": self.object_id,
                "object_version": self.object_version,
                "purpose": self.purpose,
                "policy_epoch": self.policy_epoch,
                "valid_from": self.valid_from.isoformat(),
                "valid_until": self.valid_until.isoformat(),
            }
        )


@dataclass(frozen=True)
class EvidenceContainer:
    """Outer container: one uniquely identified signature per required
    algorithm. `signatures` keys must exactly equal
    `body.signature_suite` -- no duplicate algorithms, no unexpected
    extras (spec: "Duplicate algorithms or key identifiers, unexpected
    signatures... fail closed.")."""

    body: ProtectedBody
    signatures: dict[str, str] = field(default_factory=dict)  # algorithm -> base64 signature


def build_protected_body(
    *,
    payload: dict,
    tenant_id: str,
    signer_key_set_id: str,
    object_id: str,
    object_version: str,
    purpose: str,
    policy_epoch: int,
    valid_from: dt.datetime,
    valid_until: dt.datetime,
    digest_algorithm: str = DIGEST_ALGORITHM_DEFAULT,
    signature_suite: tuple[str, ...] = ("ed25519",),
) -> ProtectedBody:
    if digest_algorithm not in (DIGEST_ALGORITHM_DEFAULT, DIGEST_ALGORITHM_STRONG):
        raise EvidenceError(f"unsupported digest_algorithm {digest_algorithm!r}")
    if len(set(signature_suite)) != len(signature_suite):
        raise EvidenceError("signature_suite must not contain duplicate algorithms")

    content_digest = _digest(canonicalize(payload), algorithm=digest_algorithm)
    return ProtectedBody(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        digest_algorithm=digest_algorithm,
        content_digest_hex=content_digest.hex(),
        signature_suite=tuple(sorted(signature_suite)),
        tenant_id=tenant_id,
        signer_key_set_id=signer_key_set_id,
        object_id=object_id,
        object_version=object_version,
        purpose=purpose,
        policy_epoch=policy_epoch,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def _sign_body_ed25519(keypair: Ed25519KeyPair, body: ProtectedBody) -> str:
    message = DOMAIN_TAG_V2 + body.canonical_bytes()
    signed = keypair.private_key.sign(message)
    return base64.b64encode(signed.signature).decode("ascii")


def _verify_body_ed25519(public_key_b64: str, body: ProtectedBody, signature_b64: str) -> bool:
    try:
        vk = nacl.signing.VerifyKey(base64.b64decode(public_key_b64))
        message = DOMAIN_TAG_V2 + body.canonical_bytes()
        vk.verify(message, base64.b64decode(signature_b64))
        return True
    except (nacl.exceptions.BadSignatureError, ValueError):
        return False


def seal_evidence(
    body: ProtectedBody,
    *,
    ed25519_keypair: Ed25519KeyPair,
    ml_dsa_keypair: MlDsaKeyPair | None = None,
) -> EvidenceContainer:
    """`ml_dsa_keypair` is optional and defaults to `None`, preserving the
    exact ed25519-only behavior every existing caller (ClauseOS, RiskOS,
    CollateralOS) already integrated against. Supplying it produces a
    genuine dual-signature container per spec 8.4 -- both signatures cover
    the identical canonical body; `body.signature_suite` must list
    whichever algorithms are actually being supplied here, or this raises.
    """
    if "ed25519" not in body.signature_suite:
        raise EvidenceError("'ed25519' must always be in signature_suite")
    if ml_dsa_keypair is not None and "ml-dsa-65" not in body.signature_suite:
        raise EvidenceError("an ml_dsa_keypair was supplied but 'ml-dsa-65' is not in body.signature_suite")
    if ml_dsa_keypair is None and "ml-dsa-65" in body.signature_suite:
        raise EvidenceError("body.signature_suite declares 'ml-dsa-65' but no ml_dsa_keypair was supplied")

    signatures = {"ed25519": _sign_body_ed25519(ed25519_keypair, body)}
    if ml_dsa_keypair is not None:
        signatures["ml-dsa-65"] = mldsa_sign(ml_dsa_keypair, body.canonical_bytes())
    return EvidenceContainer(body=body, signatures=signatures)


def verify_evidence(
    container: EvidenceContainer,
    *,
    trusted_ed25519_public_key_b64: str,
    now: dt.datetime,
    required_algorithms: frozenset[str] = REQUIRED_SIGNATURE_ALGORITHMS_V2,
    payload: dict | None = None,
    trusted_ml_dsa_public_key_b64: str | None = None,
) -> None:
    """Fails closed on every deviation. Raises `EvidenceVerificationError`
    rather than returning a boolean, so a caller cannot accidentally ignore
    a False return value.

    IMPORTANT: signature verification alone proves the protected BODY
    (including its embedded `content_digest_hex`) has not been tampered
    with. It does NOT by itself prove `content_digest_hex` actually
    corresponds to any particular payload bytes a caller has in hand --
    that requires recomputing the digest from the real payload and
    comparing. Pass `payload` (the exact same dict originally given to
    `build_protected_body`) to get that cross-check enforced here; omitting
    it means the caller is responsible for doing that comparison itself
    before trusting that this evidence is "about" a given payload. This
    was a real gap found during CollateralOS's integration of this package
    -- see `COMPONENTS.md`.
    """

    body = container.body
    if body.schema_version != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceVerificationError(f"unsupported schema_version {body.schema_version!r}")

    if payload is not None:
        recomputed = _digest(canonicalize(payload), algorithm=body.digest_algorithm).hex()
        if recomputed != body.content_digest_hex:
            raise EvidenceVerificationError(
                "payload digest does not match the signed content_digest_hex -- this evidence is not about "
                "the payload supplied to verify_evidence"
            )

    declared_suite = set(body.signature_suite)
    if declared_suite != set(container.signatures.keys()):
        raise EvidenceVerificationError("signature map does not exactly match the declared signature_suite")
    if len(body.signature_suite) != len(set(body.signature_suite)):
        raise EvidenceVerificationError("duplicate algorithm in signature_suite")

    # Required algorithms come from LOCAL TRUSTED POLICY (the caller's
    # `required_algorithms`), never solely from the payload's own
    # declared suite -- a payload that omits a required algorithm, or
    # substitutes a weaker one, must fail here even if internally
    # "consistent".
    if not required_algorithms.issubset(declared_suite):
        raise EvidenceVerificationError(
            f"missing required signature algorithm(s): {required_algorithms - declared_suite}"
        )

    if now < body.valid_from or now >= body.valid_until:
        raise EvidenceVerificationError("evidence is outside its validity interval")

    if "ed25519" in required_algorithms:
        signature_b64 = container.signatures.get("ed25519")
        if signature_b64 is None or not _verify_body_ed25519(trusted_ed25519_public_key_b64, body, signature_b64):
            raise EvidenceVerificationError("ed25519 signature verification failed")

    if "ml-dsa-65" in required_algorithms:
        if trusted_ml_dsa_public_key_b64 is None:
            raise EvidenceVerificationError("ml-dsa-65 is required but no trusted_ml_dsa_public_key_b64 was supplied")
        signature_b64 = container.signatures.get("ml-dsa-65")
        if signature_b64 is None or not mldsa_verify(trusted_ml_dsa_public_key_b64, body.canonical_bytes(), signature_b64):
            raise EvidenceVerificationError("ml-dsa-65 signature verification failed")

    # Any other declared-but-not-required algorithm still must be a
    # syntactically well-formed non-empty signature string -- an empty
    # placeholder is not an "unused optional field", it's a stripped
    # signature.
    for algorithm, signature_b64 in container.signatures.items():
        if algorithm != "ed25519" and not signature_b64:
            raise EvidenceVerificationError(f"empty signature for declared algorithm {algorithm!r}")
