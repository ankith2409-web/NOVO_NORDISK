"""Verifying an Auth0 access token, so a decision can name a real person.

The personal-token directory in `identity.py` answers "who is this" well
enough for a team that can hand each other a token. It does not scale past
that: nobody is offboarded when they leave, there is no password policy, and
"is this person still employed" is not a question a JSON file can answer.
Auth0 exists to answer exactly those, and it brings signup and Google sign-in
with it at no extra code -- both are Universal Login features, not screens
this project has to build.

What this module is careful about is the part that is easy to get wrong and
catastrophic when you do: **a token is not an identity until its signature has
been checked.** Decoding a JWT is trivial and proves nothing -- the claims are
base64, not encryption, and anyone can mint a token that *says* they are the
compliance lead. So:

  * Only RS256 is accepted. A verifier that honours the token's own `alg`
    header can be handed `alg: none`, or tricked into verifying an HS256
    signature using the public key as the shared secret. Both are textbook,
    and both are defeated by never letting the token choose.
  * The signing key comes from the tenant's JWKS over HTTPS, selected by the
    token's `kid` and never from anything the token itself supplies.
  * Issuer, audience and expiry are all required. An unverified audience means
    a token minted for a different API of the same tenant opens this one.
  * Every failure is a refusal. There is no path here that returns an identity
    it could not prove.

Deliberately does not replace `identity.py`. Concordance's normal deployment
is `concordance serve` on someone's machine inside a regulated network, and
making an internet round-trip mandatory to read a local model would lock that
person out of their own tool. Auth0 when a tenant is configured, personal
tokens when it is not, and the decision log records which was used.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

#: Auth0 signs tenant tokens with RS256. Fixed here rather than read from the
#: token, which is the whole point -- see the module docstring.
_ALGORITHMS = ["RS256"]

#: JWKS is fetched once and reused. Auth0 rotates signing keys rarely, and a
#: fetch per request would put an internet round-trip in front of every API
#: call. An unknown `kid` forces a refresh regardless, so rotation is picked up
#: as soon as it is actually used rather than on a timer.
_CACHE_SECONDS = 600
_FETCH_TIMEOUT = 5


class Auth0Error(Exception):
    """Configuration or verification failed, with a message worth printing."""


@dataclass(frozen=True)
class Identity:
    """A verified caller.

    `subject` is Auth0's stable id and is what the decision log should key on.
    `name` is for humans and may be absent -- an access token carries profile
    claims only if the tenant was configured to add them, so this must never
    be *required* for the identity to be usable.
    """

    subject: str
    name: str = ""
    email: str = ""

    @property
    def label(self) -> str:
        """What to write against a decision. Never empty."""
        return self.email or self.name or self.subject


@dataclass
class Auth0Verifier:
    """Checks tokens against one tenant and one API audience."""

    domain: str
    audience: str
    #: The SPA client id. Not a secret -- it is published to every browser that
    #: loads the sign-in page, which is why a browser app must never hold the
    #: client *secret*. Held here only so the page can be rendered with it.
    client_id: str = ""
    #: Overridable so tests can supply a JWKS without a network, and so an
    #: unreachable tenant is a visible failure rather than a hang.
    _keys: dict[str, Any] = field(default_factory=dict)
    _fetched_at: float = 0.0

    def __post_init__(self) -> None:
        self.domain = self.domain.strip().removeprefix("https://").strip("/")
        if not self.domain:
            raise Auth0Error("an Auth0 domain is required, e.g. your-tenant.eu.auth0.com")
        if not self.audience.strip():
            raise Auth0Error(
                "an Auth0 API audience is required. Without one, Auth0 issues an "
                "opaque token rather than a JWT and nothing here can verify it"
            )
        self.audience = self.audience.strip()

    @property
    def issuer(self) -> str:
        return f"https://{self.domain}/"

    @property
    def jwks_url(self) -> str:
        return f"https://{self.domain}/.well-known/jwks.json"

    # -- key material ---------------------------------------------------------

    def _load_keys(self, force: bool = False) -> dict[str, Any]:
        fresh = time.monotonic() - self._fetched_at < _CACHE_SECONDS
        if self._keys and fresh and not force:
            return self._keys

        try:
            with urllib.request.urlopen(self.jwks_url, timeout=_FETCH_TIMEOUT) as response:
                document = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # Deliberately not falling back to a stale cache on a network
            # failure *when there is none*: no keys means no verification means
            # no access, which is the correct direction to fail in.
            if self._keys:
                return self._keys
            raise Auth0Error(
                f"cannot reach the Auth0 tenant at {self.jwks_url}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise Auth0Error(f"{self.jwks_url} did not return JSON: {error}") from error

        keys = {
            key["kid"]: key
            for key in document.get("keys", [])
            if key.get("kid") and key.get("kty") == "RSA"
        }
        if not keys:
            raise Auth0Error(f"{self.jwks_url} published no usable RSA signing keys")
        self._keys = keys
        self._fetched_at = time.monotonic()
        return keys

    def _key_for(self, token: str):
        import jwt

        try:
            header = jwt.get_unverified_header(token)
        except Exception as error:  # noqa: BLE001 -- any malformed header is a refusal
            raise Auth0Error(f"not a readable JWT: {error}") from error

        # Checked before the signature, because a token naming an algorithm we
        # do not accept must be refused outright rather than handed to a
        # verifier that might be talked into honouring it.
        if header.get("alg") not in _ALGORITHMS:
            raise Auth0Error(
                f"token is signed with {header.get('alg')!r}; only RS256 is accepted"
            )

        kid = header.get("kid")
        if not kid:
            raise Auth0Error("token header carries no key id")

        keys = self._load_keys()
        if kid not in keys:
            # Unknown key id is the signal that the tenant rotated. Refetch
            # once; if it is still unknown, the token was not signed by this
            # tenant at all.
            keys = self._load_keys(force=True)
        if kid not in keys:
            raise Auth0Error("token was not signed by a key this tenant publishes")
        return jwt.PyJWK(keys[kid]).key

    # -- the check ------------------------------------------------------------

    def verify(self, token: str) -> Identity:
        """The identity behind ``token``, or raise. Never returns unverified."""
        import jwt

        if not token or not token.strip():
            raise Auth0Error("no token supplied")

        key = self._key_for(token)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                options={
                    # Spelled out rather than left to the library's defaults, so
                    # that a future default flipping to False cannot silently
                    # turn this into a decoder.
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "require": ["exp", "iss", "aud", "sub"],
                },
            )
        except jwt.PyJWTError as error:
            raise Auth0Error(f"token rejected: {error}") from error

        subject = str(claims.get("sub", "")).strip()
        if not subject:
            raise Auth0Error("token carries no subject")

        # Profile claims are optional on an access token: present only if the
        # tenant adds them through an Action. Read from the standard names and
        # from a namespaced pair, because Auth0 requires custom claims to be
        # namespaced and a bare `email` would otherwise be dropped.
        return Identity(
            subject=subject,
            name=_claim(claims, "name"),
            email=_claim(claims, "email"),
        )


def _claim(claims: dict[str, Any], name: str) -> str:
    direct = claims.get(name)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for key, value in claims.items():
        # Auth0 Actions add custom claims under a namespace URL, e.g.
        # `https://concordance.example/email`. Matched by suffix so the
        # namespace can be anything the tenant chose.
        if key.endswith(f"/{name}") and isinstance(value, str) and value.strip():
            return value.strip()
    return ""
