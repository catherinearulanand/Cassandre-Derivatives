"""Post-quantum cryptography (SEC-11/13): ML-KEM-768 key establishment and
ML-DSA-65 signatures, via `quantcrypt` -- a maintained third-party library
wrapping the FIPS 203/204 reference constructions. This module never
implements KEM/signature math itself (spec section 17: "Do not write your
own KEM, signature primitive, threshold cryptography... Use reviewed
maintained implementations").

Byte sizes below are asserted against FIPS 203/204's own published tables
(spec Appendix B) as a cheap "is this actually the standardized parameter
set" sanity check -- not a substitute for real known-answer-test vectors,
which `quantcrypt` itself is responsible for.

Scope decision 2026-09-15: ML-KEM-768 and ML-DSA-65 (NIST category 3,
the spec's recommended defaults) are implemented. ML-KEM-1024/ML-DSA-87
("high-margin"/"highest-margin" candidates per spec 8.1) are not wired up
this pass -- `quantcrypt` also exposes `MLKEM_1024`/`MLDSA_87` if a future
pass needs them; the pattern here would be identical.
"""

from __future__ import annotations

import base64
import platform
import ssl
from dataclasses import dataclass

from quantcrypt.dss import MLDSA_65
from quantcrypt.kem import MLKEM_768
from quantcrypt.errors import DSSVerifyFailedError

# FIPS 203 Table 2 (spec Appendix B): ML-KEM-768 raw byte sizes.
MLKEM_768_PUBLIC_KEY_BYTES = 1184
MLKEM_768_PRIVATE_KEY_BYTES = 2400
MLKEM_768_CIPHERTEXT_BYTES = 1088
MLKEM_768_SHARED_SECRET_BYTES = 32

# FIPS 204 Table (spec Appendix B): ML-DSA-65 raw byte sizes.
MLDSA_65_PUBLIC_KEY_BYTES = 1952
MLDSA_65_SIGNATURE_BYTES = 3309


class PqcError(ValueError):
    pass


class PqcSignatureVerificationError(PqcError):
    pass


@dataclass(frozen=True)
class MlKemKeyPair:
    key_id: str
    public_key: bytes
    private_key: bytes

    def __post_init__(self) -> None:
        if len(self.public_key) != MLKEM_768_PUBLIC_KEY_BYTES:
            raise PqcError(f"ML-KEM-768 public key must be {MLKEM_768_PUBLIC_KEY_BYTES} bytes, got {len(self.public_key)}")
        if len(self.private_key) != MLKEM_768_PRIVATE_KEY_BYTES:
            raise PqcError(f"ML-KEM-768 private key must be {MLKEM_768_PRIVATE_KEY_BYTES} bytes, got {len(self.private_key)}")

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key).decode("ascii")


def generate_mlkem_keypair(key_id: str) -> MlKemKeyPair:
    kem = MLKEM_768()
    public_key, private_key = kem.keygen()
    return MlKemKeyPair(key_id=key_id, public_key=public_key, private_key=private_key)


@dataclass(frozen=True)
class KemEncapsulation:
    ciphertext_b64: str
    shared_secret: bytes  # never serialized/logged -- caller's responsibility to treat as KEY_MATERIAL


def kem_encapsulate(public_key: bytes) -> KemEncapsulation:
    """Establishes a fresh shared secret against `public_key`. The
    ciphertext is safe to transmit/store; `shared_secret` is KEY_MATERIAL
    (spec section 6) and must never be logged, cached long-term, or
    returned in an API response."""
    kem = MLKEM_768()
    ciphertext, shared_secret = kem.encaps(public_key)
    if len(ciphertext) != MLKEM_768_CIPHERTEXT_BYTES:
        raise PqcError(f"unexpected ML-KEM-768 ciphertext length {len(ciphertext)}")
    if len(shared_secret) != MLKEM_768_SHARED_SECRET_BYTES:
        raise PqcError(f"unexpected ML-KEM-768 shared secret length {len(shared_secret)}")
    return KemEncapsulation(ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"), shared_secret=shared_secret)


def kem_decapsulate(private_key: bytes, ciphertext_b64: str) -> bytes:
    kem = MLKEM_768()
    try:
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise PqcError("malformed base64 ciphertext") from exc
    if len(ciphertext) != MLKEM_768_CIPHERTEXT_BYTES:
        raise PqcError(f"ML-KEM-768 ciphertext must be {MLKEM_768_CIPHERTEXT_BYTES} bytes, got {len(ciphertext)}")
    # `quantcrypt` preserves the underlying implicit-rejection behavior
    # (spec 8.1: "Preserve the provider's implicit-rejection behavior; do
    # not expose internal decapsulation checks as a remote oracle") --
    # decaps() never raises on a malformed/foreign ciphertext, it returns
    # a pseudorandom-looking but deterministic "rejection" secret instead,
    # exactly per FIPS 203. Callers relying on this shared secret to
    # authenticate something downstream (e.g. an AEAD tag check) will
    # naturally fail closed on that secret being wrong -- there is no
    # separate "was this ciphertext valid?" boolean to leak.
    return kem.decaps(private_key, ciphertext)


@dataclass(frozen=True)
class MlDsaKeyPair:
    key_id: str
    public_key: bytes
    private_key: bytes

    def __post_init__(self) -> None:
        if len(self.public_key) != MLDSA_65_PUBLIC_KEY_BYTES:
            raise PqcError(f"ML-DSA-65 public key must be {MLDSA_65_PUBLIC_KEY_BYTES} bytes, got {len(self.public_key)}")

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key).decode("ascii")


def generate_mldsa_keypair(key_id: str) -> MlDsaKeyPair:
    dss = MLDSA_65()
    public_key, private_key = dss.keygen()
    return MlDsaKeyPair(key_id=key_id, public_key=public_key, private_key=private_key)


DOMAIN_TAG_MLDSA = b"CASSANDRE-MLDSA-V1\n"


def mldsa_sign(keypair: MlDsaKeyPair, message: bytes) -> str:
    """Domain-separated (spec: a signature over this protocol's messages
    must not be replayable as a signature over an unrelated one -- same
    rationale as `audit_protocol.keys`'s Ed25519 domain tag)."""
    dss = MLDSA_65()
    signature = dss.sign(keypair.private_key, DOMAIN_TAG_MLDSA + message)
    if len(signature) != MLDSA_65_SIGNATURE_BYTES:
        raise PqcError(f"unexpected ML-DSA-65 signature length {len(signature)}")
    return base64.b64encode(signature).decode("ascii")


def mldsa_verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    dss = MLDSA_65()
    try:
        public_key = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:  # noqa: BLE001
        return False
    try:
        dss.verify(public_key, DOMAIN_TAG_MLDSA + message, signature)
        return True
    except DSSVerifyFailedError:
        return False


@dataclass(frozen=True)
class CryptoCapabilityReport:
    """Spec SEC-11's evidence gate: "exact binary/provider/version/
    operating-mode inventory". This is a runtime self-report, not a
    substitute for the spec's required known-answer-test/interoperability
    evidence -- it answers "what is actually loaded on this machine right
    now", which is the necessary precondition for those tests."""

    python_version: str
    platform: str
    openssl_version: str
    mlkem_variant: str
    mldsa_variant: str
    quantcrypt_backend: str


def crypto_capability_report() -> CryptoCapabilityReport:
    return CryptoCapabilityReport(
        python_version=platform.python_version(),
        platform=platform.platform(),
        openssl_version=ssl.OPENSSL_VERSION,
        mlkem_variant="ML-KEM-768 (FIPS 203, category 3)",
        mldsa_variant="ML-DSA-65 (FIPS 204, category 3)",
        quantcrypt_backend="quantcrypt (PyPI) wrapping liboqs-derived reference implementations",
    )
