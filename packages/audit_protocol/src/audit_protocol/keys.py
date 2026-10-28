"""Ed25519 signing with domain separation, per plan Section 8.1.

Signs UTF8("CASSANDRE-ENVELOPE-V1\\n") || raw_sha256_digest, never the
digest alone, so a signature over this protocol's envelopes cannot be
replayed as a signature over some other unrelated digest.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

import nacl.exceptions
import nacl.signing

DOMAIN_TAG = b"CASSANDRE-ENVELOPE-V1\n"


class SignatureVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class Ed25519KeyPair:
    key_id: str
    private_key: nacl.signing.SigningKey
    public_key: nacl.signing.VerifyKey

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(bytes(self.public_key)).decode("ascii")


def generate_keypair(key_id: str) -> Ed25519KeyPair:
    sk = nacl.signing.SigningKey.generate()
    return Ed25519KeyPair(key_id=key_id, private_key=sk, public_key=sk.verify_key)


def _domain_separated_message(raw_sha256_digest: bytes) -> bytes:
    if len(raw_sha256_digest) != 32:
        raise ValueError("raw_sha256_digest must be exactly 32 bytes")
    return DOMAIN_TAG + raw_sha256_digest


def sign_digest(keypair: Ed25519KeyPair, raw_sha256_digest: bytes) -> str:
    """Sign a raw (binary) sha256 digest; returns base64 signature."""
    message = _domain_separated_message(raw_sha256_digest)
    signed = keypair.private_key.sign(message)
    return base64.b64encode(signed.signature).decode("ascii")


def verify_digest(public_key_b64: str, raw_sha256_digest: bytes, signature_b64: str) -> bool:
    try:
        vk = nacl.signing.VerifyKey(base64.b64decode(public_key_b64))
        message = _domain_separated_message(raw_sha256_digest)
        vk.verify(message, base64.b64decode(signature_b64))
        return True
    except (nacl.exceptions.BadSignatureError, ValueError):
        return False


def sha256_hex_to_raw(hex_digest: str) -> bytes:
    if len(hex_digest) != 64:
        raise ValueError("expected a 64-hex-char sha256 digest")
    return bytes.fromhex(hex_digest)


def sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()
