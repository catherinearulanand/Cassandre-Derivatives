"""The shared envelope, per plan Section 8.1.

Every product-to-product or exportable object uses this envelope. This
module intentionally makes `simulation_provenance` non-optional and
non-defaultable (ADR-013): constructing an Envelope without it is a
Pydantic ValidationError, not a runtime check that can be skipped.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from audit_protocol.canonical import content_hash
from audit_protocol.keys import Ed25519KeyPair, sha256_hex_to_raw, sign_digest, verify_digest

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProducerProduct(str, Enum):
    CLAUSEOS = "clauseos"
    RISKOS = "riskos"
    COLLATERALOS = "collateralos"
    CASSANDRE = "cassandre"


class SimulationProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_simulated: Literal[True]
    scenario_id: str
    generator_version: str


class ContentReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_id: str
    schema_name: str
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _hash(cls, v: str) -> str:
        if not _SHA256_RE.match(v):
            raise ValueError("content_hash must be a 64-hex-char sha256 digest")
        return v


class Signature(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: Literal["Ed25519"]
    key_id: str
    value: str


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_name: str
    schema_version: str
    producer_product: ProducerProduct
    producer_version: str
    issuer: str
    audience: list[str]
    object_id: str
    object_version: str
    content_hash: str
    created_at: dt.datetime
    effective_as_of: dt.datetime
    simulation_provenance: SimulationProvenance
    authority_status: str
    purpose_constraints: list[str]
    lineage: list[ContentReference]
    payload: dict[str, Any]
    signature: Signature | None = None

    @field_validator("schema_version", "producer_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"{v!r} is not a semver string")
        return v

    @field_validator("content_hash")
    @classmethod
    def _hash(cls, v: str) -> str:
        if not _SHA256_RE.match(v):
            raise ValueError("content_hash must be a 64-hex-char sha256 digest")
        return v

    @field_validator("created_at", "effective_as_of")
    @classmethod
    def _tz_aware(cls, v: dt.datetime) -> dt.datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (RFC3339 UTC)")
        return v.astimezone(dt.timezone.utc)

    @model_validator(mode="after")
    def _audience_not_empty(self) -> "Envelope":
        if not self.audience:
            raise ValueError("audience must name at least one intended consumer")
        return self

    def unsigned_dict(self) -> dict[str, Any]:
        """The exact field set that is hashed and signed (everything except
        content_hash and signature itself)."""
        data = self.model_dump(mode="json", exclude={"content_hash", "signature"})
        return data

    def compute_content_hash(self) -> str:
        return content_hash(self.unsigned_dict())


_PLACEHOLDER_HASH = "0" * 64


def build_envelope(
    *,
    schema_name: str,
    schema_version: str,
    producer_product: ProducerProduct,
    producer_version: str,
    issuer: str,
    audience: list[str],
    object_id: str,
    object_version: str,
    created_at: dt.datetime,
    effective_as_of: dt.datetime,
    simulation_provenance: SimulationProvenance,
    authority_status: str,
    purpose_constraints: list[str],
    lineage: list[ContentReference],
    payload: dict[str, Any],
) -> Envelope:
    """Construct an unsigned envelope with a correctly computed content_hash.

    content_hash is excluded from its own hash input, so a placeholder value
    can be used for the first construction without affecting the real hash.
    """
    draft = Envelope(
        schema_name=schema_name,
        schema_version=schema_version,
        producer_product=producer_product,
        producer_version=producer_version,
        issuer=issuer,
        audience=audience,
        object_id=object_id,
        object_version=object_version,
        content_hash=_PLACEHOLDER_HASH,
        created_at=created_at,
        effective_as_of=effective_as_of,
        simulation_provenance=simulation_provenance,
        authority_status=authority_status,
        purpose_constraints=purpose_constraints,
        lineage=lineage,
        payload=payload,
        signature=None,
    )
    return draft.model_copy(update={"content_hash": draft.compute_content_hash()})


def sign_envelope(envelope: Envelope, keypair: Ed25519KeyPair) -> Envelope:
    if envelope.compute_content_hash() != envelope.content_hash:
        raise ValueError("refusing to sign an envelope whose content_hash does not match its fields")
    raw_digest = sha256_hex_to_raw(envelope.content_hash)
    signature = Signature(
        algorithm="Ed25519",
        key_id=keypair.key_id,
        value=sign_digest(keypair, raw_digest),
    )
    return envelope.model_copy(update={"signature": signature})


@dataclass(frozen=True)
class TrustBundle:
    """Maps key_id -> base64 Ed25519 public key. Represents the versioned
    public-key trust bundle from plan Section 8.1's key-lifecycle rules."""

    keys_by_id: dict[str, str] = field(default_factory=dict)
    revoked_key_ids: frozenset[str] = frozenset()

    def is_usable(self, key_id: str) -> bool:
        return key_id in self.keys_by_id and key_id not in self.revoked_key_ids


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    failures: list[str]


def verify_envelope(
    envelope: Envelope,
    *,
    trust_bundle: TrustBundle,
    expected_audience: str,
    permitted_purpose: str | None = None,
    max_age: dt.timedelta | None = None,
    now: dt.datetime | None = None,
    allowed_authority_statuses: frozenset[str] | None = None,
) -> VerificationResult:
    """Consumer-side verification per plan Section 8.1: schema, hash,
    signature, simulation flag, every intended audience, every purpose
    constraint, freshness, and authority status. Fails closed: any missing
    prerequisite (no signature, unknown key, revoked key) is a failure, not
    a skipped check."""

    failures: list[str] = []
    now = now or dt.datetime.now(dt.timezone.utc)

    recomputed = envelope.compute_content_hash()
    if recomputed != envelope.content_hash:
        failures.append("CONTENT_HASH_MISMATCH")

    if envelope.signature is None:
        failures.append("MISSING_SIGNATURE")
    else:
        if not trust_bundle.is_usable(envelope.signature.key_id):
            failures.append("UNKNOWN_OR_REVOKED_KEY_ID")
        else:
            public_key_b64 = trust_bundle.keys_by_id[envelope.signature.key_id]
            raw_digest = sha256_hex_to_raw(envelope.content_hash)
            if not verify_digest(public_key_b64, raw_digest, envelope.signature.value):
                failures.append("SIGNATURE_INVALID")

    if envelope.simulation_provenance.is_simulated is not True:
        failures.append("NOT_SIMULATED")  # pragma: no cover - type forbids this already

    if expected_audience not in envelope.audience:
        failures.append("AUDIENCE_MISMATCH")

    if permitted_purpose is not None and envelope.purpose_constraints:
        if permitted_purpose not in envelope.purpose_constraints:
            failures.append("PURPOSE_NOT_PERMITTED")

    if max_age is not None:
        age = now - envelope.effective_as_of
        if age > max_age:
            failures.append("STALE")

    if allowed_authority_statuses is not None:
        if envelope.authority_status not in allowed_authority_statuses:
            failures.append("AUTHORITY_STATUS_NOT_PERMITTED")

    return VerificationResult(ok=len(failures) == 0, failures=failures)
