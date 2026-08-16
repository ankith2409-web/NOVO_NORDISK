"""Auth0 token verification.

No network and no tenant: an RSA keypair is generated here, a JWKS is built
from its public half, and tokens are signed with the private half. That is not
a shortcut around testing the real thing -- it *is* the real thing. Auth0's
half of the exchange is "publish a JWKS and sign RS256 tokens with the
matching key", and a locally-generated key exercises exactly the same code
path as a tenant's would. What cannot be tested here is whether a real tenant
is reachable, which is a deployment fact rather than a property of this code.

The tests that matter are the refusals. A verifier that accepts good tokens
but also accepts one forged with a different key, or one that names its own
algorithm, is worse than no verifier at all -- it produces an audit trail
everyone believes and nobody should.
"""

from __future__ import annotations

import json
import time

import pytest

jwt = pytest.importorskip("jwt", reason="Auth0 support needs pyjwt[crypto]")
pytest.importorskip("cryptography", reason="Auth0 support needs pyjwt[crypto]")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from concordance.review.auth0 import Auth0Error, Auth0Verifier  # noqa: E402

DOMAIN = "tenant.eu.auth0.com"
AUDIENCE = "https://concordance.example/api"
ISSUER = f"https://{DOMAIN}/"


def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_for(key, kid: str = "key-1") -> dict:
    """The public half, in the shape Auth0 publishes it."""
    numbers = key.public_key().public_numbers()
    to_b64 = jwt.utils.to_base64url_uint
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
                "n": to_b64(numbers.n).decode(),
                "e": to_b64(numbers.e).decode(),
            }
        ]
    }


def token_for(key, kid: str = "key-1", **overrides) -> str:
    claims = {
        "sub": "auth0|abc123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        **overrides,
    }
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def verifier(key, kid: str = "key-1") -> Auth0Verifier:
    """A verifier with its keys pre-loaded, so nothing reaches the network."""
    made = Auth0Verifier(domain=DOMAIN, audience=AUDIENCE, client_id="spa-client")
    made._keys = {k["kid"]: k for k in jwks_for(key, kid)["keys"]}
    made._fetched_at = time.monotonic()
    return made


# -- configuration ------------------------------------------------------------

def test_a_domain_is_normalised_and_required() -> None:
    made = Auth0Verifier(domain="https://tenant.eu.auth0.com/", audience=AUDIENCE)
    assert made.domain == "tenant.eu.auth0.com"
    assert made.issuer == ISSUER
    assert made.jwks_url == f"{ISSUER}.well-known/jwks.json"

    with pytest.raises(Auth0Error, match="domain is required"):
        Auth0Verifier(domain="  ", audience=AUDIENCE)


def test_an_audience_is_required() -> None:
    """Without one Auth0 issues an opaque token, not a JWT. Refusing at
    construction beats failing on every sign-in with a confusing message."""
    with pytest.raises(Auth0Error, match="audience is required"):
        Auth0Verifier(domain=DOMAIN, audience="")


# -- the happy path -----------------------------------------------------------

def test_a_properly_signed_token_yields_its_subject() -> None:
    key = keypair()
    identity = verifier(key).verify(token_for(key))
    assert identity.subject == "auth0|abc123"
    assert identity.label == "auth0|abc123"


def test_profile_claims_are_used_for_the_label_when_present() -> None:
    key = keypair()
    identity = verifier(key).verify(
        token_for(key, email="anna@example.com", name="Anna Poulsen")
    )
    assert identity.email == "anna@example.com"
    # Email wins: it is the thing a person recognises in an audit trail.
    assert identity.label == "anna@example.com"


def test_a_namespaced_custom_claim_is_found() -> None:
    """Auth0 requires custom claims to be namespaced, so a bare `email` never
    arrives on an access token that was populated by an Action."""
    key = keypair()
    identity = verifier(key).verify(
        token_for(key, **{"https://concordance.example/email": "bo@example.com"})
    )
    assert identity.label == "bo@example.com"


def test_an_access_token_without_profile_claims_still_works() -> None:
    """The common case: no Action configured. Falling over here would make
    Auth0 unusable out of the box."""
    key = keypair()
    assert verifier(key).verify(token_for(key)).label == "auth0|abc123"


# -- the refusals, which are the point ----------------------------------------

def test_a_token_signed_by_a_different_key_is_refused() -> None:
    """The forgery this whole module exists to stop."""
    ours, theirs = keypair(), keypair()
    with pytest.raises(Auth0Error, match="rejected|not signed"):
        verifier(ours).verify(token_for(theirs))


def test_an_unsigned_token_is_refused() -> None:
    """`alg: none` is the oldest JWT attack there is, and it works against any
    verifier that honours the algorithm the token names."""
    key = keypair()
    forged = jwt.encode(
        {"sub": "auth0|attacker", "iss": ISSUER, "aud": AUDIENCE,
         "exp": int(time.time()) + 3600},
        key=None,
        algorithm="none",
        headers={"kid": "key-1"},
    )
    with pytest.raises(Auth0Error, match="only RS256"):
        verifier(key).verify(forged)


def test_an_hs256_token_is_refused() -> None:
    """The other classic: an attacker re-signs with HS256 and hopes the
    verifier honours the header. The real version uses the tenant's public key
    as the HMAC secret; PyJWT refuses to *encode* that way, so the secret here
    is arbitrary -- which does not weaken the test, because the refusal must
    happen on the algorithm alone, before any key is chosen."""
    key = keypair()
    forged = jwt.encode(
        {"sub": "auth0|attacker", "iss": ISSUER, "aud": AUDIENCE,
         "exp": int(time.time()) + 3600},
        "any-shared-secret",
        algorithm="HS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(Auth0Error, match="only RS256"):
        verifier(key).verify(forged)


def test_an_expired_token_is_refused() -> None:
    key = keypair()
    with pytest.raises(Auth0Error, match="rejected"):
        verifier(key).verify(token_for(key, exp=int(time.time()) - 60))


def test_a_token_for_another_audience_is_refused() -> None:
    """One tenant can front several APIs. Without this check, a token minted
    for any of them opens this one."""
    key = keypair()
    with pytest.raises(Auth0Error, match="rejected"):
        verifier(key).verify(token_for(key, aud="https://someone-elses/api"))


def test_a_token_from_another_issuer_is_refused() -> None:
    key = keypair()
    with pytest.raises(Auth0Error, match="rejected"):
        verifier(key).verify(token_for(key, iss="https://evil.example/"))


def test_a_token_with_no_subject_is_refused() -> None:
    """An identity with no id is not an identity."""
    key = keypair()
    made = verifier(key)
    with pytest.raises(Auth0Error, match="rejected|no subject"):
        made.verify(token_for(key, sub=""))


def test_a_token_whose_key_this_tenant_never_published_is_refused() -> None:
    key = keypair()
    made = verifier(key, kid="key-1")
    # Signed with the right key but announcing a kid the JWKS does not carry.
    # The refetch is attempted and fails closed rather than falling back to
    # "well, try every key we have".
    made._fetched_at = time.monotonic()  # keep the cache warm so no network call
    with pytest.raises(Auth0Error):
        made.verify(token_for(key, kid="rotated-key"))


def test_garbage_is_refused_without_raising_something_unhelpful() -> None:
    key = keypair()
    made = verifier(key)
    for rubbish in ("", "   ", "not-a-jwt", "a.b.c"):
        with pytest.raises(Auth0Error):
            made.verify(rubbish)


# -- key rotation -------------------------------------------------------------

def test_a_rotated_key_is_picked_up_by_refetching(monkeypatch) -> None:
    """Auth0 rotates signing keys. A cache that never refreshes would lock
    every user out until the server restarted."""
    old, new = keypair(), keypair()
    made = verifier(old, kid="old-key")

    calls = []

    def fresh_keys(force: bool = False):
        calls.append(force)
        made._keys = {k["kid"]: k for k in jwks_for(new, "new-key")["keys"]}
        return made._keys

    monkeypatch.setattr(made, "_load_keys", fresh_keys)
    identity = made.verify(token_for(new, kid="new-key"))
    assert identity.subject == "auth0|abc123"
    assert calls, "an unknown kid must trigger a refetch"


def test_an_unreachable_tenant_is_a_readable_error() -> None:
    """No network here, which is exactly the case a regulated site hits: the
    message has to name the URL rather than surfacing a socket error.

    A properly-formed token on purpose -- the header is parsed before any
    fetch is attempted, so rubbish would fail earlier and never exercise this.
    """
    key = keypair()
    made = Auth0Verifier(domain="nonexistent.invalid", audience=AUDIENCE)
    with pytest.raises(Auth0Error, match="cannot reach"):
        made.verify(token_for(key))
