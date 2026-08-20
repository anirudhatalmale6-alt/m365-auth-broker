import hashlib
import re

from m365broker import pkce

UNRESERVED = re.compile(r"^[A-Za-z0-9\-._~]+$")


def test_verifier_matches_rfc7636_length_and_charset():
    verifier = pkce.generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert UNRESERVED.match(verifier)


def test_verifier_is_unique_per_call():
    values = {pkce.generate_code_verifier() for _ in range(200)}
    assert len(values) == 200


def test_challenge_is_unpadded_base64url_sha256_of_verifier():
    verifier = "a" * 43
    expected_digest = hashlib.sha256(verifier.encode()).digest()

    challenge = pkce.code_challenge(verifier)

    assert "=" not in challenge
    assert "+" not in challenge and "/" not in challenge
    assert challenge == pkce._b64url(expected_digest)


def test_challenge_matches_rfc7636_appendix_b_vector():
    """The worked example from RFC 7636 - proves interop, not just self-consistency."""
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce.code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_challenge_is_not_reversible_to_verifier():
    verifier = pkce.generate_code_verifier()
    assert pkce.code_challenge(verifier) != verifier


def test_state_and_nonce_are_distinct_and_random():
    assert pkce.generate_state() != pkce.generate_state()
    assert pkce.generate_nonce() != pkce.generate_nonce()
    assert len(pkce.generate_state()) >= 32


def test_constant_time_equals():
    assert pkce.constant_time_equals("abc", "abc")
    assert not pkce.constant_time_equals("abc", "abd")
    assert not pkce.constant_time_equals("abc", "")
    # Must not raise on non-string input from a malformed redirect.
    assert not pkce.constant_time_equals(None, "abc")
