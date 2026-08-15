"""Named access tokens, so a decision can record who actually made it.

The review log has always stored an ``author``, and has always been honest
that it was a claim: with one shared access token there is no way to tell two
people apart, and a field labelled as authenticated when it is not would be
worse than no field. But an audit trail whose every entry says "whoever had
the link" is not much of an audit trail, and in a regulated setting it is
none at all.

This is the smallest thing that fixes that without inventing an accounts
system. Each person gets their own token; the server resolves the token
presented on a request back to a name, and the log records *that* name rather
than whatever the request body asked for. A caller can no longer sign off as
somebody else, because the name is never something a caller supplies.

Deliberately not a login form, and not a password database. Passwords need
hashing, rotation, reset flows and a place to store them, and every one of
those is a thing to get wrong. A per-person bearer token is the same trust
model as a personal access token, is revoked by deleting a line, and is
honest about what it is.

The file's permissions are checked rather than assumed. A token file readable
by every account on the machine gives away every identity in it, and refusing
to load one is the same call `ssh` makes about a private key -- an error the
person can fix in one command beats a server that quietly runs insecure.
"""

from __future__ import annotations

import json
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path


class DirectoryError(Exception):
    """The token file could not be used, with a message worth printing."""


@dataclass(frozen=True)
class Directory:
    """Who may use this server, by token.

    Held token -> name rather than name -> token, because resolving a
    presented token is the only lookup ever performed.
    """

    #: token -> person's name.
    people: dict[str, str]
    source: Path | None = None

    def __len__(self) -> int:
        return len(self.people)

    @property
    def names(self) -> list[str]:
        return sorted(set(self.people.values()))

    def resolve(self, token: str) -> str | None:
        """The name behind ``token``, or None if it is not one of ours.

        Every entry is compared even after a match is found, and each with
        ``compare_digest``. Returning early on the first hit would make the
        response time depend on a token's position in the file, and comparing
        with ``==`` would leak the matching prefix -- both are how a token
        gets guessed a character at a time rather than brute-forced.
        """
        found: str | None = None
        if not token:
            return None
        for candidate, name in self.people.items():
            if secrets.compare_digest(candidate, token):
                found = name
        return found

    @classmethod
    def load(cls, path: str | Path) -> "Directory":
        """Read a ``{"name": "token"}`` JSON file.

        Written name-first because that is the way a person authors it; it is
        inverted here, once, into the direction lookups actually go.
        """
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise DirectoryError(f"no user file at {source}") from None
        except json.JSONDecodeError as error:
            raise DirectoryError(f"{source} is not valid JSON: {error}") from None

        if not isinstance(raw, dict) or not raw:
            raise DirectoryError(
                f'{source} must be a JSON object of {{"name": "token"}}, and not empty'
            )

        _refuse_if_world_readable(source)

        people: dict[str, str] = {}
        for name, token in raw.items():
            if not isinstance(token, str) or not token.strip():
                raise DirectoryError(f"the token for {name!r} in {source} is empty")
            if len(token) < 16:
                # Short enough to guess is short enough to refuse. Said at load
                # rather than left to be discovered, because nothing later in
                # the system can tell a weak token from a strong one.
                raise DirectoryError(
                    f"the token for {name!r} in {source} is under 16 characters; "
                    f"generate one with: python -c "
                    f"\"import secrets; print(secrets.token_urlsafe(24))\""
                )
            if token in people:
                # Two people sharing a token makes the log's name arbitrary,
                # which is precisely the thing this file exists to prevent.
                raise DirectoryError(
                    f"{name!r} and {people[token]!r} in {source} share a token, so a "
                    f"decision could not be attributed to either"
                )
            people[token] = str(name)

        return cls(people=people, source=source)


def _refuse_if_world_readable(source: Path) -> None:
    """Reject a token file any other account on the machine can read."""
    try:
        mode = source.stat().st_mode
    except OSError:  # pragma: no cover -- it was just read successfully
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        raise DirectoryError(
            f"{source} is readable by other accounts on this machine, and it "
            f"contains every reviewer's token. Run: chmod 600 {source}"
        )
