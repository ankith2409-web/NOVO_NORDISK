"""Semantic fingerprinting.

A fingerprint is the SHA-256 of an expression's canonical form. Because
canonicalisation erases formatting but preserves meaning, the hash answers a
narrow question precisely: *has this object's behaviour changed?* -- which is
what a bound requirement actually needs to know, and what a text diff cannot
tell you.
"""

from __future__ import annotations

import hashlib

from janus.normalize.dax import canonicalise

#: Characters of hex shown in reports and diffs. The full digest is what gets
#: stored and compared; this is only for humans reading a drift table.
DISPLAY_LENGTH = 12


def fingerprint_dax(expr: str) -> str:
    """Fingerprint a DAX expression by its meaning rather than its text."""
    return _sha256(canonicalise(expr))


def fingerprint_text(text: str) -> str:
    """Fingerprint a plain string (table names, M queries -- anything not DAX)."""
    return _sha256(text.strip())


def fingerprint_parts(*parts: str) -> str:
    """Fingerprint an ordered set of already-canonical parts.

    Used for composite objects such as relationships, where identity is the
    combination of endpoints and cardinality rather than a single expression.
    The separator is a control character that cannot occur in a DAX identifier,
    so ``("ab", "c")`` and ``("a", "bc")`` cannot collide.
    """
    return _sha256("\x1f".join(parts))


def short(digest: str) -> str:
    """Shorten a digest for display."""
    return digest[:DISPLAY_LENGTH]


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
