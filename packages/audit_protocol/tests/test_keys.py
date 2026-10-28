from audit_protocol.keys import generate_keypair, sha256_bytes, sign_digest, verify_digest


def test_sign_and_verify_round_trip():
    kp = generate_keypair("test-key-1")
    digest = sha256_bytes(b"some canonical envelope bytes")
    sig = sign_digest(kp, digest)
    assert verify_digest(kp.public_key_b64, digest, sig) is True


def test_verify_rejects_tampered_digest():
    kp = generate_keypair("test-key-1")
    digest = sha256_bytes(b"some canonical envelope bytes")
    sig = sign_digest(kp, digest)
    other_digest = sha256_bytes(b"a different payload")
    assert verify_digest(kp.public_key_b64, other_digest, sig) is False


def test_verify_rejects_wrong_key():
    kp1 = generate_keypair("k1")
    kp2 = generate_keypair("k2")
    digest = sha256_bytes(b"payload")
    sig = sign_digest(kp1, digest)
    assert verify_digest(kp2.public_key_b64, digest, sig) is False


def test_domain_separation_prevents_cross_protocol_replay():
    # A signature made directly over the raw digest (no domain tag) must not
    # verify against this protocol's verifier.
    import base64
    kp = generate_keypair("k1")
    digest = sha256_bytes(b"payload")
    naive_signature = base64.b64encode(kp.private_key.sign(digest).signature).decode("ascii")
    assert verify_digest(kp.public_key_b64, digest, naive_signature) is False
