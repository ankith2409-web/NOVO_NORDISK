"""Who signed a decision off, and whether that name means anything.

The review log always stored an author and was always honest that it was a
claim -- with one shared token there is nobody to distinguish. An audit trail
whose every entry says "whoever had the link" is not much of a trail, and in a
regulated setting it is none.

The property that matters is narrow and testable: with a user directory
configured, the name written to the log comes from the token the request
presented and *never* from its body. A reviewer who can name the author can
sign off as a colleague, and that single hole would make the whole record
worthless. The HTTP half is exercised against a real server for the same
reason the rest of `test_web.py` is -- headers and status codes are where
this either holds or does not.
"""

from __future__ import annotations

import http.client
import json
import stat
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.graph.csg import SemanticGraph
from concordance.llm.fake import FakeProvider
from concordance.review.decisions import DecisionLog, Verdict
from concordance.review.identity import Directory, DirectoryError
from concordance.web import api
from concordance.web.server import make_handler

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")
ANNA = "anna-token-long-enough-to-pass"
BO = "bo-token-also-long-enough-here"


def _users(tmp_path: Path, people: dict[str, str] | None = None) -> Path:
    path = tmp_path / "users.json"
    path.write_text(
        json.dumps(people if people is not None else {"Anna": ANNA, "Bo": BO})
    )
    path.chmod(0o600)
    return path


# -- the directory itself -----------------------------------------------------

def test_a_token_resolves_to_its_person(tmp_path: Path) -> None:
    directory = Directory.load(_users(tmp_path))
    assert directory.resolve(ANNA) == "Anna"
    assert directory.resolve(BO) == "Bo"
    assert directory.names == ["Anna", "Bo"]


def test_an_unknown_or_empty_token_resolves_to_nobody(tmp_path: Path) -> None:
    directory = Directory.load(_users(tmp_path))
    assert directory.resolve("not-a-token") is None
    assert directory.resolve("") is None
    # A prefix of a real token must not resolve; `compare_digest` is what makes
    # guessing one a character at a time useless.
    assert directory.resolve(ANNA[:-1]) is None


def test_a_world_readable_file_is_refused(tmp_path: Path) -> None:
    """It holds every reviewer's token. Loading it anyway would give the
    operator an audit trail they believe is authenticated and is not."""
    path = _users(tmp_path)
    path.chmod(0o644)
    with pytest.raises(DirectoryError, match="readable by other accounts"):
        Directory.load(path)


def test_a_short_token_is_refused_at_load(tmp_path: Path) -> None:
    with pytest.raises(DirectoryError, match="under 16 characters"):
        Directory.load(_users(tmp_path, {"Anna": "short"}))


def test_two_people_sharing_a_token_is_refused(tmp_path: Path) -> None:
    """Which name the log recorded would be arbitrary, which is exactly the
    thing this file exists to prevent."""
    with pytest.raises(DirectoryError, match="share a token"):
        Directory.load(_users(tmp_path, {"Anna": ANNA, "Bo": ANNA}))


def test_a_missing_or_malformed_file_says_which(tmp_path: Path) -> None:
    with pytest.raises(DirectoryError, match="no user file"):
        Directory.load(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    bad.chmod(0o600)
    with pytest.raises(DirectoryError, match="not valid JSON"):
        Directory.load(bad)


# -- what actually gets written -----------------------------------------------

def test_the_log_records_whether_the_author_was_verified(tmp_path: Path) -> None:
    log = DecisionLog.open(tmp_path / "log.jsonl")
    log.record("REQ-1", Verdict.ACCEPTED, ("fp",), author="Anna", author_verified=True)
    log.record("REQ-2", Verdict.ACCEPTED, ("fp",), author="typed in", author_verified=False)

    reread = DecisionLog.open(tmp_path / "log.jsonl")
    by_id = {d.requirement_id: d for d in reread.history_for("REQ-1") + reread.history_for("REQ-2")}
    assert by_id["REQ-1"].author_verified is True
    assert by_id["REQ-2"].author_verified is False


def test_an_older_log_has_no_verified_entries(tmp_path: Path) -> None:
    """Read by a newer build, a log written before identity existed must report
    every entry as unverified -- because nothing in it ever was."""
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps(
            {
                "requirement_id": "REQ-1",
                "verdict": "accepted",
                "bound_fingerprints": ["fp"],
                "note": "",
                "author_claimed": "someone",
                "at": "2026-01-01T00:00:00+00:00",
            }
        )
        + "\n"
    )
    assert DecisionLog.open(path).history_for("REQ-1")[0].author_verified is False


# -- and the hole it all turns on ---------------------------------------------

class _Server:
    def __init__(self, graph: SemanticGraph, decisions: Path, users) -> None:
        handler, _ = make_handler(
            graph,
            FakeProvider(script=[]),
            api.ApiContext(graph=graph, decisions=decisions),
            users=users,
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def request(self, method: str, path: str, token: str = "", body: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        raw = json.dumps(body).encode() if body is not None else None
        if raw:
            headers["Content-Length"] = str(len(raw))
        conn.request(method, path, body=raw, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


@pytest.fixture
def server(graph: SemanticGraph, tmp_path: Path):
    running = _Server(graph, tmp_path / "log.jsonl", Directory.load(_users(tmp_path)))
    yield running, tmp_path / "log.jsonl"
    running.close()


def test_a_request_with_no_token_is_refused(server) -> None:
    running, _ = server
    assert running.request("GET", "/api/overview")[0] == 401


def test_whoami_names_the_person_behind_the_token(server) -> None:
    running, _ = server
    status, payload = running.request("GET", "/api/whoami", token=ANNA)
    assert status == 200
    assert payload == {
        "person": "Anna",
        "identified": True,
        "identifies_reviewers": True,
        # This server identifies by personal token, not by Auth0. The flag
        # tells the sign-in page which control to draw.
        "auth0": False,
    }


def test_a_reviewer_cannot_sign_off_as_a_colleague(server, graph) -> None:
    """The whole point. Anna presents her token and asks for the decision to be
    recorded against Bo; the log must say Anna."""
    running, log_path = server
    requirement = next(
        r for kind in api.Kind
        for r in api.ApiContext(graph=graph).requirements(kind)
        if r.needs_review
    )

    status, payload = running.request(
        "POST",
        "/api/decide",
        token=ANNA,
        body={
            "requirement_id": requirement.id,
            "verdict": "accepted",
            "author": "Bo",
        },
    )
    assert status == 200
    assert payload["standing"]["author_claimed"] == "Anna"
    assert payload["standing"]["author_verified"] is True

    written = json.loads(log_path.read_text().splitlines()[0])
    assert written["author_claimed"] == "Anna"
    assert written["author_verified"] is True


def test_without_a_directory_the_author_is_recorded_as_the_claim_it_is(
    graph: SemanticGraph, tmp_path: Path
) -> None:
    log_path = tmp_path / "log.jsonl"
    running = _Server(graph, log_path, users=None)
    try:
        requirement = next(
            r for kind in api.Kind
            for r in api.ApiContext(graph=graph).requirements(kind)
            if r.needs_review
        )
        status, payload = running.request(
            "POST",
            "/api/decide",
            body={
                "requirement_id": requirement.id,
                "verdict": "accepted",
                "author": "whoever",
            },
        )
        assert status == 200
        assert payload["standing"]["author_claimed"] == "whoever"
        assert payload["standing"]["author_verified"] is False
        assert running.request("GET", "/api/whoami")[1]["identifies_reviewers"] is False
    finally:
        running.close()
