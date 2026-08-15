"""A record of what a person decided, bound to what they decided it about.

The Review queue lists statements the deriver was not confident enough to
assert. Until now there was no way to answer any of them: a queue with no
Accept and no Reject is a list of work nobody can finish, and it stayed that
way because a button that looked like it filed an approval while storing
nothing would be worse than no button.

What makes this more than a status field is what a decision is bound to. Every
requirement carries the fingerprints of the objects its statement was derived
from, and a decision records those fingerprints as they were at the moment it
was made. When the DAX behind a measure changes, its fingerprint changes, and
the decision stops applying *on its own* -- nobody has to remember to revoke
it. A sign-off that silently carried over onto logic it was never shown is the
exact failure this project exists to prevent, and it is the one an ordinary
approved/not-approved column produces by design.

Append-only, one JSON object per line. A later decision supersedes an earlier
one without erasing it, which is what separates an audit trail from a mutable
field: "this was accepted in March, rejected in April, accepted again in May
after the measure was rewritten" is the history someone in a regulated industry
is actually required to produce.

A file, not a database, and deliberately so while there is no database to use.
JSON Lines survives being read by anything, appends without rewriting, and
tolerates a truncated final line -- which is what a crash mid-write leaves
behind. Moving this to a real store later changes where ``DecisionLog`` reads
and writes and nothing above it.

On identity: a decision carries ``author`` and ``author_verified``, and they
have to be read together. Started with ``--users``, the server resolves the
token on the request back to a name and records it as verified -- a caller
cannot sign off as someone else, because the name never comes from the request
body. Without ``--users`` there is nothing to resolve, so ``author`` is
whatever the caller states and is recorded as the claim it is. Both are kept in
one log deliberately; what would be dishonest is storing them in a way a reader
cannot tell apart.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Verdict(Enum):
    """What was decided about one requirement."""

    #: The statement describes the model correctly and may enter the document.
    ACCEPTED = "accepted"
    #: The statement is wrong. It stays in the queue and out of the document.
    REJECTED = "rejected"
    #: Correct as far as it goes, but the note carries what the document should
    #: say instead. Kept distinct from accepted so a reader can find the ones
    #: whose wording a person actually supplied.
    CORRECTED = "corrected"


@dataclass(frozen=True)
class Decision:
    """One person's answer about one requirement, at one moment."""

    requirement_id: str
    verdict: Verdict
    #: The fingerprints of the objects the statement rested on, as they were
    #: when this was decided. The whole mechanism turns on this field.
    bound_fingerprints: tuple[str, ...]
    note: str = ""
    #: Who decided. Whether that is a fact or a claim is ``author_verified``,
    #: and the two must always be read together.
    author: str = "unattributed"
    #: True when the server resolved the author from the token the request
    #: presented, rather than from the request body. A log that mixed the two
    #: without distinguishing them would be worse than one that authenticated
    #: nobody: every entry would have to be treated as a claim anyway.
    author_verified: bool = False
    at: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "requirement_id": self.requirement_id,
                "verdict": self.verdict.value,
                "bound_fingerprints": list(self.bound_fingerprints),
                "note": self.note,
                # The key still says "claimed", and still means it. An older
                # log read by a newer build has no verified entries, which is
                # exactly right -- nothing in it was ever authenticated.
                "author_claimed": self.author,
                "author_verified": self.author_verified,
                "at": self.at,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, line: str) -> "Decision | None":
        """Parse one line, or None if it is not a decision.

        A truncated final line is what a crash mid-append leaves behind, and
        losing the whole history to it would be a poor trade for strictness.
        """
        try:
            raw = json.loads(line)
            return cls(
                requirement_id=str(raw["requirement_id"]),
                verdict=Verdict(raw["verdict"]),
                bound_fingerprints=tuple(raw.get("bound_fingerprints") or ()),
                note=str(raw.get("note", "")),
                author=str(raw.get("author_claimed", "unattributed")),
                author_verified=bool(raw.get("author_verified", False)),
                at=str(raw.get("at", "")),
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None


class Status(Enum):
    """Where one requirement stands now."""

    #: Nobody has answered it.
    OPEN = "open"
    #: Decided, and the objects it rests on are exactly as they were then.
    DECIDED = "decided"
    #: Decided, but what it rests on has changed since. The decision is not
    #: revoked -- it is reported as no longer covering the current definition,
    #: which is a different and more useful statement.
    STALE = "stale"


@dataclass(frozen=True)
class Standing:
    """A requirement's current position, and the decision that produced it."""

    status: Status
    latest: Decision | None = None
    #: Every decision ever recorded for it, oldest first. This is the trail.
    history: tuple[Decision, ...] = ()

    @property
    def verdict(self) -> str:
        return self.latest.verdict.value if self.latest else ""


@dataclass
class DecisionLog:
    """Append-only decisions for one model, on disk."""

    path: Path
    _entries: list[Decision] = field(default_factory=list)

    @classmethod
    def open(cls, path: str | Path) -> "DecisionLog":
        log = cls(path=Path(path))
        log.reload()
        return log

    def reload(self) -> None:
        self._entries = []
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            decision = Decision.from_json(line)
            if decision is not None:
                self._entries.append(decision)

    def record(
        self,
        requirement_id: str,
        verdict: Verdict,
        bound_fingerprints: tuple[str, ...],
        note: str = "",
        author: str = "unattributed",
        author_verified: bool = False,
    ) -> Decision:
        """Append one decision and return it, timestamped."""
        decision = Decision(
            requirement_id=requirement_id,
            verdict=verdict,
            bound_fingerprints=tuple(bound_fingerprints),
            note=note.strip(),
            author=author.strip() or "unattributed",
            author_verified=author_verified,
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Opened per append rather than held: a long-running server holding a
        # write handle would lose whatever was buffered if it were killed, and
        # this file is the part that must not be lost.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(decision.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._entries.append(decision)
        return decision

    def history_for(self, requirement_id: str) -> tuple[Decision, ...]:
        return tuple(d for d in self._entries if d.requirement_id == requirement_id)

    def standing(self, requirement_id: str, current: tuple[str, ...]) -> Standing:
        """Where a requirement stands, given what it rests on *now*.

        ``current`` is the requirement's fingerprints as just derived. Comparing
        against them rather than against anything stored is what makes staleness
        automatic: nothing has to notice a change and go mark decisions, because
        the comparison happens every time the question is asked.
        """
        history = self.history_for(requirement_id)
        if not history:
            return Standing(status=Status.OPEN)
        latest = history[-1]
        # Compared as sets: evidence order is an artefact of derivation, and a
        # reordering is not a change to what the statement rests on.
        same = set(latest.bound_fingerprints) == set(current)
        return Standing(
            status=Status.DECIDED if same else Status.STALE,
            latest=latest,
            history=history,
        )

    def counts(self) -> dict[str, int]:
        return {
            "decisions": len(self._entries),
            "requirements_touched": len({d.requirement_id for d in self._entries}),
        }
