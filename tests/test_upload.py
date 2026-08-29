"""Reading a model somebody uploaded through the browser.

This is the only input to the whole system that an operator did not choose.
Everywhere else a path is typed by the person running the tool; here bytes
arrive from whoever found the URL, so the tests are shaped around the two
questions that follow from that.

*Can a malicious file reach outside the upload?* Path traversal, absolute
paths, symlinks and decompression bombs each get a test that asserts a refusal
rather than a sanitised extraction -- the distinction matters, because
``ZipFile.extractall`` silently rewrites a traversing entry and carries on,
which turns a hostile archive into one that merely looks odd afterwards.

*Can one visitor see another's model?* Every route, the chat, the document
download and the forget endpoint are checked from a second browser session,
because a demo server that leaked somebody's proprietary .pbix to the next
visitor would be a worse failure than not having the feature at all.

The happy path gets its own attention for a reason found while writing it: a
zipped model arrives in three genuinely different shapes, and a check that
passed on two of them was silently refusing the third.
"""

from __future__ import annotations

import http.client
import io
import json
import stat
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

from concordance.adapters.base import SourceError
from concordance.adapters.tmdl import TmdlAdapter
from concordance.graph.csg import SemanticGraph
from concordance.model import SemanticModel
from concordance.llm.fake import FakeProvider, says
from concordance.web import api, upload
from concordance.web.server import UploadStore, make_handler

MODEL = Path("data/models/QualityControl.SemanticModel")
OTHER = Path("data/models/ClinicalTrialSafety.SemanticModel")
PBIX = Path("data/models/AdventureWorks_Sales.pbix")


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


def _zip_of(source: Path, prefix: str) -> bytes:
    """The model at ``source``, zipped under ``prefix``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, str(Path(prefix) / path.relative_to(source)))
    return buffer.getvalue()


@pytest.fixture(scope="module")
def shapes() -> dict[str, bytes]:
    """The three ways a person actually zips a Power BI model.

    Whichever one somebody happens to right-click, the answer has to be the
    same model. They are not interchangeable to the code: the name is taken
    from a folder, and only one of these three has a folder to take it from.
    """
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return {
        # The .SemanticModel folder itself.
        "named": _zip_of(MODEL, "QualityControl.SemanticModel"),
        # A .pbip project directory containing it.
        "project": _zip_of(MODEL, "MyProject/QualityControl.SemanticModel"),
        # The contents, with no wrapping folder at all.
        "bare": _zip_of(MODEL, "."),
    }


def _read(body: bytes, filename: str) -> SemanticGraph:
    return upload.read_model(io.BytesIO(body), filename, len(body))


def _archive(build) -> bytes:
    # Deflated, not stored. A bomb that does not compress is just a large file,
    # and is refused by the size limit before the bomb check is ever reached --
    # so an uncompressed fixture would pass this file's tests while testing
    # something else entirely.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        build(bundle)
    return buffer.getvalue()


# -- the happy paths -----------------------------------------------------------

@pytest.mark.parametrize("shape", ["named", "project", "bare"])
def test_every_way_of_zipping_a_model_reads_the_same_model(
    shapes: dict[str, bytes], shape: str
) -> None:
    read = _read(shapes[shape], "Quality Control.zip")
    assert read.model.summary()["measures"] == 20
    assert read.model.summary()["user_tables"] == 6


def test_a_named_folder_names_the_model(shapes: dict[str, bytes]) -> None:
    assert _read(shapes["named"], "whatever.zip").model.name == "QualityControl"


def test_a_bare_archive_is_named_after_the_uploaded_file(
    shapes: dict[str, bytes],
) -> None:
    """There is no folder to take a name from, so the filename is all there is.

    The alternative is naming it after the temporary directory the server
    invented, which is what happens if nobody thinks about this case.
    """
    assert _read(shapes["bare"], "Site Metrics.zip").model.name == "Site Metrics"


def test_a_pbix_is_read_and_named_after_its_file() -> None:
    if not PBIX.exists():
        pytest.skip(f"pbix not present: {PBIX}")
    read = _read(PBIX.read_bytes(), "AdventureWorks Sales.pbix")
    assert read.model.name == "AdventureWorks Sales"
    assert read.model.summary()["user_tables"] > 0


def test_a_lone_tmdl_file_is_given_the_folder_it_expects() -> None:
    """Someone exporting one table and dropping it in is a real thing to want."""
    table = MODEL / "definition" / "tables" / "Batch.tmdl"
    if not table.exists():
        pytest.skip("fixture table missing")
    read = _read(table.read_bytes(), "Batch.tmdl")
    assert read.model.name == "Batch"
    assert read.model.summary()["user_tables"] == 1


# -- what must be refused ------------------------------------------------------

def test_a_traversing_entry_is_refused_not_sanitised() -> None:
    """`extractall` would rewrite this path and continue, leaving no signal.

    An archive containing `../../x` is not a Power BI export that needs
    tidying up; it is one that should never be unpacked.
    """
    evil = _archive(lambda z: z.writestr("../../pwned.tmdl", "x"))
    with pytest.raises(upload.UploadRefused, match="points outside"):
        _read(evil, "evil.zip")


def test_a_backslash_traversal_is_refused() -> None:
    evil = _archive(lambda z: z.writestr("..\\..\\pwned.tmdl", "x"))
    with pytest.raises(upload.UploadRefused, match="points outside"):
        _read(evil, "evil.zip")


@pytest.mark.parametrize("name", ["/etc/pwned.tmdl", "C:\\pwned.tmdl"])
def test_an_absolute_path_is_refused(name: str) -> None:
    evil = _archive(lambda z: z.writestr(name, "x"))
    with pytest.raises(upload.UploadRefused, match="absolute path"):
        _read(evil, "evil.zip")


def test_a_symlink_entry_is_refused() -> None:
    """Nothing in a .SemanticModel is a symlink, and following one would read
    a file outside the upload entirely."""

    def build(bundle: zipfile.ZipFile) -> None:
        entry = zipfile.ZipInfo("definition/link.tmdl")
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(entry, "/etc/passwd")

    with pytest.raises(upload.UploadRefused, match="symbolic link"):
        _read(_archive(build), "evil.zip")


def test_a_declared_bomb_is_refused_before_unpacking() -> None:
    def build(bundle: zipfile.ZipFile) -> None:
        for index in range(3):
            bundle.writestr(f"definition/big{index}.tmdl", b"\0" * (200 * 1024 * 1024))

    with pytest.raises(upload.UploadRefused, match="expands to"):
        _read(_archive(build), "bomb.zip")


def test_an_undeclared_bomb_is_caught_while_unpacking(monkeypatch) -> None:
    """The size in the header is a number the archive states about itself.

    Trusting it is the whole mistake: a bomb declares whatever passes the cheap
    check. This drops the ceiling below what the archive really contains and
    asserts the count taken during extraction stops it anyway.
    """
    body = _archive(
        lambda z: z.writestr("definition/model.tmdl", b"x" * (2 * 1024 * 1024))
    )
    monkeypatch.setattr(upload, "MAX_UNPACKED_BYTES", 1024)
    with pytest.raises(upload.UploadRefused, match="expands"):
        _read(body, "bomb.zip")


def test_too_many_entries_is_refused() -> None:
    """A million empty files is not large, and is still an afternoon of rglob."""

    def build(bundle: zipfile.ZipFile) -> None:
        for index in range(upload.MAX_ARCHIVE_ENTRIES + 1):
            bundle.writestr(f"definition/f{index}.txt", "x")

    with pytest.raises(upload.UploadRefused, match="entries"):
        _read(_archive(build), "many.zip")


def test_an_unaccepted_extension_never_reaches_a_parser() -> None:
    with pytest.raises(upload.UploadRefused, match="not a format"):
        _read(b"MZ\x90\x00", "payload.exe")


def test_an_empty_upload_is_refused() -> None:
    with pytest.raises(upload.UploadRefused, match="empty"):
        _read(b"", "model.pbix")


def test_an_oversized_upload_is_refused_from_its_declared_length() -> None:
    with pytest.raises(upload.UploadRefused, match="limit"):
        upload.read_model(io.BytesIO(b"x"), "big.pbix", upload.MAX_UPLOAD_BYTES + 1)


def test_no_more_than_the_declared_length_is_ever_read() -> None:
    """The declared length is the gate *and* the read bound.

    That pairing is what makes the size limit real rather than advisory. A
    client cannot send `Content-Length: 10` and then stream a gigabyte, because
    only ten bytes are ever read from the body -- and it cannot declare a
    gigabyte either, because that is refused from the header before the socket
    is read at all. Reading exactly the declared length is also the only
    HTTP-correct choice: reading past it would consume the next request on the
    connection.
    """
    body = io.BytesIO(b"x" * 4096)
    with pytest.raises(SourceError):
        upload.read_model(body, "under-declared.pbix", 10)
    assert body.tell() == 10, "read past the length the client declared"


def test_an_archive_with_no_model_says_what_to_zip_instead() -> None:
    body = _archive(lambda z: z.writestr("readme.txt", "nothing here"))
    with pytest.raises(SourceError) as raised:
        _read(body, "My Report.zip")
    assert "Zip the folder itself" in raised.value.hint


def test_a_failure_names_the_upload_and_not_a_server_path() -> None:
    """The reader has never heard of `/tmp/concordance-upload-8f3a1c`.

    Every adapter names the path it was handed, which is right when an operator
    typed it and wrong here, where it is both meaningless and a server path in
    a browser.
    """
    body = _archive(lambda z: z.writestr("readme.txt", "x"))
    with pytest.raises(SourceError) as raised:
        _read(body, "My Report.zip")
    rendered = raised.value.render()
    assert "My Report.zip" in rendered
    assert "/tmp/" not in rendered and "concordance-upload" not in rendered


def test_a_corrupt_zip_is_a_readable_failure_not_a_traceback() -> None:
    with pytest.raises(SourceError, match="not a readable zip"):
        _read(b"this is not a zip at all", "model.zip")


# -- the filename is never trusted --------------------------------------------

@pytest.mark.parametrize(
    "given,expected",
    [
        ("../../etc/passwd.pbix", "passwd"),
        ("/absolute/path/Model.pbix", "Model"),
        ("C:\\Users\\me\\Model.pbix", "C-Users-me-Model"),
        ("...pbix", "uploaded-model"),
        ("", "uploaded-model"),
        ("a" * 400 + ".pbix", "a" * 80),
    ],
)
def test_an_uploaded_filename_is_rebuilt_from_a_whitelist(
    given: str, expected: str
) -> None:
    """The stem becomes a real path, and for a .pbix the model's own name.

    So it is rebuilt rather than escaped: nothing that means something to a
    filesystem survives, and the empty result of stripping everything is
    replaced rather than used.
    """
    assert upload.safe_stem(given) == expected


def test_nothing_survives_on_disk(monkeypatch, tmp_path, shapes) -> None:
    """A customer's model must not be left sitting on a demo server."""
    monkeypatch.setattr("tempfile.mkdtemp", lambda **kw: str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    _read(shapes["named"], "QC.zip")
    assert not (tmp_path / "work").exists()


def test_nothing_survives_on_disk_after_a_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("tempfile.mkdtemp", lambda **kw: str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    with pytest.raises(SourceError):
        _read(b"not a zip", "broken.zip")
    assert not (tmp_path / "work").exists()


# -- the store, without a socket ----------------------------------------------

def _uploaded_view(graph: SemanticGraph) -> api.ApiContext:
    """A real model, flagged as uploaded, for the tests about *behaviour*.

    Read-only: unlike `_context` this is never handed to the store, so the
    shared fixture graph it wraps is safe from being renamed.
    """
    return api.ApiContext(graph=graph, uploaded=True)


def _context(name: str = "Uploaded") -> api.ApiContext:
    """A throwaway model for the naming and eviction tests.

    Deliberately not the shared fixture graph. ``UploadStore.add`` takes
    ownership of what it is handed and renames it, so passing one module-scoped
    model into eight store tests renamed it out from under every test that ran
    afterwards -- which is how this fixture came to exist.
    """
    model = SemanticModel(name=name, source_path="(uploaded)", source_type="tmdl")
    return api.ApiContext(graph=SemanticGraph(model), uploaded=True)


def test_an_upload_never_shadows_a_configured_model() -> None:
    """Otherwise `?model=QualityControl` means different things per visitor,
    and the configured one becomes unreachable for whoever uploaded."""
    store = UploadStore(reserved={"QualityControl"})
    name, _ = store.add("session-a", _context(), "QualityControl")
    assert name == "QualityControl (2)"


def test_two_uploads_of_the_same_name_both_stay_reachable() -> None:
    store = UploadStore(reserved=set())
    first, _ = store.add("a", _context(), "Model")
    second, _ = store.add("a", _context(), "Model")
    assert (first, second) == ("Model", "Model (2)")


def test_one_session_cannot_see_another_session_uploads() -> None:
    store = UploadStore(reserved=set())
    store.add("alice", _context(), "Alice Model")
    store.add("bob", _context(), "Bob Model")
    assert list(store.for_session("alice")) == ["Alice Model"]
    assert list(store.for_session("bob")) == ["Bob Model"]


def test_a_session_with_no_cookie_sees_nothing() -> None:
    """Cookieless requests must not collapse into one shared identity."""
    store = UploadStore(reserved=set())
    store.add("alice", _context(), "Alice Model")
    assert store.for_session(None) == {}
    assert store.for_session("") == {}


def test_the_oldest_upload_is_dropped_past_the_per_session_cap() -> None:
    store = UploadStore(reserved=set(), max_per_session=2)
    store.add("alice", _context(), "One")
    store.add("alice", _context(), "Two")
    _, evicted = store.add("alice", _context(), "Three")
    assert evicted == "One"
    assert sorted(store.for_session("alice")) == ["Three", "Two"]


def test_one_session_filling_up_does_not_evict_another() -> None:
    store = UploadStore(reserved=set(), max_per_session=2)
    store.add("bob", _context(), "Bob Model")
    for name in ("One", "Two", "Three"):
        store.add("alice", _context(), name)
    assert list(store.for_session("bob")) == ["Bob Model"]


def test_the_store_is_bounded_across_every_session() -> None:
    store = UploadStore(reserved=set(), max_total=3)
    for index in range(10):
        store.add(f"session-{index}", _context(), f"Model {index}")
    assert len(store) == 3


def test_only_the_owner_can_forget_an_upload() -> None:
    store = UploadStore(reserved=set())
    store.add("alice", _context(), "Alice Model")
    assert store.forget("bob", "Alice Model") is False
    assert store.forget("alice", "Alice Model") is True
    assert store.for_session("alice") == {}


# -- layering onto the registry ------------------------------------------------

def test_an_upload_is_addressable_but_never_the_default(graph: SemanticGraph) -> None:
    """Someone opening the page in another tab still lands where they expect."""
    base = api.ModelRegistry.of(api.ApiContext(graph=graph))
    combined = base.plus({"Mine": _uploaded_view(graph)})
    assert combined.default == "QualityControl"
    assert combined.resolve({"model": ["Mine"]}).uploaded is True
    assert combined.resolve({}).uploaded is False


def test_the_base_registry_is_not_mutated_by_layering(graph: SemanticGraph) -> None:
    """The layer lasts one request. A registry that grew would leak the upload
    to every other visitor, which is the one thing this must never do."""
    base = api.ModelRegistry.of(api.ApiContext(graph=graph))
    base.plus({"Mine": _uploaded_view(graph)})
    assert list(base.contexts) == ["QualityControl"]


def test_every_read_route_answers_for_an_uploaded_model(graph: SemanticGraph) -> None:
    """The point of layering rather than special-casing: no route knows."""
    base = api.ModelRegistry.of(api.ApiContext(graph=graph))
    combined = base.plus({"Mine": _uploaded_view(graph)})
    for route in sorted(api.ROUTES):
        params = {"model": ["Mine"]}
        if route == "/api/requirements":
            params["kind"] = ["functional"]
        if route in ("/api/measure", "/api/impact", "/api/table"):
            continue  # need an object name; covered by the model's own views
        status, _ = api.handle(combined, route, params)
        assert status in (200, 501), f"{route} answered {status} for an upload"


def test_an_uploaded_model_cannot_be_signed_off(graph: SemanticGraph) -> None:
    with pytest.raises(api.ApiError) as raised:
        api.decide(_uploaded_view(graph), {"requirement_id": "x", "verdict": "accepted"})
    assert raised.value.status == 501
    assert "uploaded model" in raised.value.message


def test_the_refusal_does_not_send_someone_to_fix_a_flag_that_is_set(
    graph: SemanticGraph,
) -> None:
    """The two reasons a queue is read-only need different sentences.

    An uploaded model has no log by design; telling its reader to restart with
    `--decisions` sends them to add a flag the server may already have.
    """
    with pytest.raises(api.ApiError) as raised:
        api.decide(_uploaded_view(graph), {"requirement_id": "x", "verdict": "accepted"})
    assert "--decisions" not in raised.value.message


# -- over a real socket --------------------------------------------------------

class Running:
    def __init__(self, registry: api.ModelRegistry, **kwargs) -> None:
        handler, _ = make_handler(
            registry.contexts[registry.default].graph,
            FakeProvider(script=[says("ok")] * 8),
            registry,
            **kwargs,
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class Browser:
    """One browser session: it holds its cookie the way a real one would."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.cookie = ""

    def _headers(self, extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _remember(self, response) -> None:
        raw = response.getheader("Set-Cookie") or ""
        if "concordance_session" in raw:
            self.cookie = raw.split(";")[0]

    def upload(self, body: bytes, filename: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(
            "POST",
            f"/api/upload?filename={quote(filename)}",
            body=body,
            headers=self._headers(
                {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(body)),
                }
            ),
        )
        response = conn.getresponse()
        payload = json.loads(response.read() or b"{}")
        self._remember(response)
        return response.status, payload

    def visit(self) -> "Browser":
        """Load the page, which is what mints a session cookie.

        Used to give a second browser a *real* session before it probes for
        somebody else's model. Probing without one passes for the wrong reason:
        a cookieless request is refused by a guard of its own, so an isolation
        test written that way would still pass with ownership checks removed
        entirely. That is exactly what happened before this existed.
        """
        self.get("/")
        assert self.cookie, "the page did not mint a session cookie"
        return self

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers=self._headers())
        response = conn.getresponse()
        body = response.read()
        self._remember(response)
        try:
            return response.status, json.loads(body)
        except json.JSONDecodeError:
            return response.status, body

    def post(self, path: str, payload: dict):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = json.dumps(payload).encode()
        conn.request(
            "POST",
            path,
            body=body,
            headers=self._headers(
                {"Content-Type": "application/json", "Content-Length": str(len(body))}
            ),
        )
        response = conn.getresponse()
        self._remember(response)
        return response.status, json.loads(response.read() or b"{}")


@pytest.fixture
def server(graph: SemanticGraph):
    registry = api.ModelRegistry.of(api.ApiContext(graph=graph))
    running = Running(registry)
    yield running
    running.close()


def test_an_uploaded_model_joins_the_switcher(server, shapes) -> None:
    alice = Browser(server.port)
    status, body = alice.upload(shapes["named"], "QC.zip")
    assert status == 200
    assert body["name"] == "QualityControl (2)"
    assert body["measures"] == 20

    _, listed = alice.get("/api/models")
    uploaded = [m for m in listed["models"] if m["uploaded"]]
    assert [m["name"] for m in uploaded] == ["QualityControl (2)"]


def test_the_upload_answers_every_view_it_should(server, shapes) -> None:
    alice = Browser(server.port)
    name = quote(alice.upload(shapes["bare"], "Mine.zip")[1]["name"])
    for route in (
        f"/api/overview?model={name}",
        f"/api/tables?model={name}",
        f"/api/measures?model={name}",
        f"/api/requirements?kind=functional&model={name}",
        f"/api/review?model={name}",
        f"/api/dataset?model={name}",
    ):
        status, _ = alice.get(route)
        assert status == 200, route


def test_the_frd_downloads_for_an_uploaded_model_with_its_sql(
    server, shapes
) -> None:
    alice = Browser(server.port)
    name = quote(alice.upload(shapes["bare"], "Mine.zip")[1]["name"])
    _, dataset = alice.get(f"/api/dataset?model={name}")
    grain = quote(dataset["grain_options"][0]["value"])
    status, body = alice.get(
        f"/api/document?kind=functional&model={name}&sql=1&grain={grain}"
    )
    assert status == 200
    assert b"SELECT" in body and b"GROUP BY" in body


def test_another_browser_cannot_see_or_reach_the_upload(server, shapes) -> None:
    """The invariant the whole feature rests on."""
    alice = Browser(server.port)
    name = alice.upload(shapes["named"], "QC.zip")[1]["name"]

    bob = Browser(server.port).visit()
    _, listed = bob.get("/api/models")
    assert [m["name"] for m in listed["models"]] == ["QualityControl"]

    status, payload = bob.get(f"/api/overview?model={quote(name)}")
    assert status == 404
    assert name not in json.dumps(payload)


def test_another_browser_cannot_ask_the_chat_about_the_upload(server, shapes) -> None:
    alice = Browser(server.port)
    name = alice.upload(shapes["named"], "QC.zip")[1]["name"]
    bob = Browser(server.port).visit()
    status, _ = bob.post("/api/ask", {"question": "how many measures?", "model": name})
    assert status == 404


def test_another_browser_cannot_forget_the_upload(server, shapes) -> None:
    alice = Browser(server.port)
    name = alice.upload(shapes["named"], "QC.zip")[1]["name"]

    bob = Browser(server.port).visit()
    assert bob.post("/api/forget", {"model": name})[0] == 404
    assert alice.get(f"/api/overview?model={quote(name)}")[0] == 200


def test_a_browser_with_no_session_at_all_sees_no_uploads(server, shapes) -> None:
    """The other half of the isolation story, covered deliberately rather than
    by accident: a request arriving with no cookie must not fall into a shared
    bucket with every other cookieless request."""
    Browser(server.port).upload(shapes["named"], "QC.zip")
    stranger = Browser(server.port)
    _, listed = stranger.get("/api/models")
    assert [m["name"] for m in listed["models"]] == ["QualityControl"]


def test_an_owner_can_take_their_model_back_off_the_server(server, shapes) -> None:
    alice = Browser(server.port)
    name = alice.upload(shapes["named"], "QC.zip")[1]["name"]
    assert alice.post("/api/forget", {"model": name}) == (200, {"forgotten": name})
    assert alice.get(f"/api/overview?model={quote(name)}")[0] == 404


def test_the_owner_is_told_which_model_an_upload_pushed_out(server, shapes) -> None:
    """Finding out from the switcher rather than from the answer is a surprise."""
    alice = Browser(server.port)
    names = []
    for index in range(4):
        names.append(alice.upload(shapes["bare"], f"Model {index}.zip")[1])
    assert names[-1]["replaced"] == "Model 0"
    assert names[-1]["held"] == 3


def test_an_oversized_upload_is_refused_without_being_read(server) -> None:
    """Reading 200MB in order to reject it is the denial of service, not the
    defence against it -- so the refusal comes from the header alone."""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.putrequest("POST", "/api/upload?filename=big.pbix")
    conn.putheader("Content-Length", str(upload.MAX_UPLOAD_BYTES + 1))
    conn.endheaders()
    response = conn.getresponse()
    assert response.status == 413


def test_an_upload_without_a_filename_is_refused(server) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request(
        "POST",
        "/api/upload",
        body=b"xxxx",
        headers={"Content-Length": "4"},
    )
    assert conn.getresponse().status == 400


def test_a_hostile_archive_is_refused_over_http_too(server) -> None:
    alice = Browser(server.port)
    evil = _archive(lambda z: z.writestr("../../pwned.tmdl", "x"))
    status, payload = alice.upload(evil, "evil.zip")
    assert status == 400
    assert payload["refused"] is True


def test_uploads_are_rate_limited(server, shapes) -> None:
    """The one endpoint that parses a file this server did not choose."""
    alice = Browser(server.port)
    statuses = [
        alice.upload(shapes["named"], f"QC{index}.zip")[0] for index in range(10)
    ]
    assert 429 in statuses


def test_a_server_started_with_no_upload_has_no_such_route(graph) -> None:
    registry = api.ModelRegistry.of(api.ApiContext(graph=graph))
    running = Running(registry, accepts_uploads=False)
    try:
        status, payload = Browser(running.port).upload(b"xxxx", "model.pbix")
        assert status == 501
        assert "--no-upload" in payload["error"]
    finally:
        running.close()


def test_the_upload_does_not_change_which_model_is_default(server, shapes) -> None:
    """Uploading makes a model available, never automatic. Another tab on the
    same server must still open on what the operator loaded."""
    alice = Browser(server.port)
    alice.upload(shapes["named"], "QC.zip")
    _, listed = alice.get("/api/models")
    assert listed["default"] == "QualityControl"
    assert alice.get("/api/overview")[1]["model"] == "QualityControl"
