"""Opening a model from a link.

Most of this file is about one thing: fetching a URL that somebody else chose
means this server makes a request on a stranger's behalf, which is the shape of
every SSRF hole there has ever been. A link to `http://169.254.169.254/` is a
request for the cloud metadata service and the credentials it hands out. A link
to `http://127.0.0.1:9000/` is a request to whatever else happens to be running
on this host, reached from inside the trust boundary rather than outside it.

So the destination is resolved and checked before any request is sent, and
checked *again* on every hop of a redirect chain -- a public hostname that
answers 302 with a metadata address defeats a check that only runs once.

The rest is the Power BI Service, which is recognised rather than attempted.
Those bytes are behind Azure AD and no amount of trying gets them, so the
reader is told what is missing instead of being shown a network error.
"""

from __future__ import annotations

import pytest

from concordance.web import fetch
from concordance.web.fetch import LinkRefused, check_address, check_url, classify

# -- what will not be fetched --------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",          # this machine
        "::1",
        "169.254.169.254",    # the cloud metadata service, and the whole point
        "169.254.1.1",
        "10.0.0.5",           # private ranges
        "172.16.4.4",
        "192.168.1.1",
        "0.0.0.0",
        "224.0.0.1",          # multicast
        "fe80::1",            # link-local v6
        "fc00::1",            # unique-local v6
    ],
)
def test_an_address_off_the_public_internet_is_refused(address: str) -> None:
    with pytest.raises(LinkRefused):
        check_address(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:2800:220:1::"])
def test_a_public_address_is_allowed(address: str) -> None:
    check_address(address)


def test_the_metadata_service_is_named_as_link_local_not_merely_private() -> None:
    """Python counts link-local as private, so checking private first would
    report the one address that matters as "a private network address" -- true,
    and useless to whoever is reading the log."""
    with pytest.raises(LinkRefused) as raised:
        check_address("169.254.169.254")
    assert "link-local" in str(raised.value)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/model.pbix",
        "gopher://example.com/",
        "data:text/plain,hello",
        "javascript:alert(1)",
    ],
)
def test_only_http_and_https_are_fetched(url: str) -> None:
    with pytest.raises(LinkRefused):
        check_url(url)


def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(LinkRefused):
        check_url("http:///model.pbix")


def test_localhost_by_name_is_refused_as_well_as_by_address() -> None:
    """The check resolves the name rather than pattern-matching it, or
    `localhost`, `127.0.0.2` and `0x7f000001` would each need their own rule."""
    with pytest.raises(LinkRefused):
        check_url("http://localhost:8080/model.pbix")


def test_a_redirect_is_checked_again(monkeypatch) -> None:
    """A destination validated once is validated for the first request only."""
    guard = fetch._Guarded()
    with pytest.raises(LinkRefused):
        guard.redirect_request(
            None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/"
        )


# -- the Power BI Service ------------------------------------------------------


def test_a_service_link_is_recognised_not_fetched() -> None:
    found = classify(
        "https://app.powerbi.com/groups/11111111-2222-3333-4444-555555555555"
        "/reports/66666666-7777-8888-9999-000000000000/ReportSection"
    )
    assert found.kind == "service"
    assert found.workspace == "11111111-2222-3333-4444-555555555555"
    assert found.report == "66666666-7777-8888-9999-000000000000"


@pytest.mark.parametrize(
    "url",
    [
        "https://app.powerbi.com/groups/me/reports/abc",
        "https://msit.powerbi.com/groups/me/list",
        "https://app.fabric.microsoft.com/groups/me/reports/x",
        "https://wabi-uk-south-b-primary-redirect.analysis.windows.net/x",
    ],
)
def test_every_service_host_is_recognised(url: str) -> None:
    assert classify(url).kind == "service"


def test_a_lookalike_host_is_not_mistaken_for_the_service() -> None:
    """`powerbi.com.evil.test` ends in neither `powerbi.com` nor a dot before
    it, and must not be waved through as a Service link."""
    assert classify("https://powerbi.com.evil.test/x.pbix").kind == "download"


def test_an_ordinary_link_is_a_download() -> None:
    assert classify("https://raw.githubusercontent.com/a/b/main/m.pbix").kind == "download"


def test_the_service_message_says_both_ways_forward() -> None:
    """A refusal that does not say what to do instead is a dead end, and the
    reader has two real options."""
    assert "Download this file" in fetch.SERVICE_HINT
    assert "credentials" in fetch.SERVICE_HINT


# -- naming what arrives -------------------------------------------------------


class _Headers(dict):
    def get(self, key, default=""):
        return dict.get(self, key, default)


def test_a_filename_comes_from_the_header_before_the_path() -> None:
    """A download URL is often `…/download?id=3`, which names nothing."""
    assert (
        fetch._name_from(
            "https://x/download?id=3",
            _Headers({"Content-Disposition": 'attachment; filename="Sales.pbix"'}),
        )
        == "Sales.pbix"
    )


def test_a_filename_falls_back_to_the_path() -> None:
    assert fetch._name_from("https://x/y/Model.pbix", _Headers()) == "Model.pbix"


def test_a_link_naming_nothing_still_gets_a_name() -> None:
    """`read_model` reads the format off the extension, so an empty name would
    be refused for the wrong reason."""
    assert fetch._name_from("https://x/", _Headers()).endswith(".pbix")


# -- the download itself -------------------------------------------------------
#
# Exercised against a real HTTP server on this machine, with the address check
# patched for the duration. Patching the guard in order to test the code behind
# it is worth being explicit about: the guard is tested above, thoroughly and
# on its own, and leaving it in place here would mean the fetch itself -- the
# streaming, the size cap, the naming, the handoff to the parser -- had no test
# at all, because every address this machine can reach is one the guard
# correctly refuses.


import functools
import http.server
import threading
from pathlib import Path

MODELS = Path("data/models")


@pytest.fixture
def serving(tmp_path, monkeypatch):
    """A local HTTP server, with the address guard stood down for this test."""
    source = MODELS / "StoreSales.pbix"
    if not source.exists():
        pytest.skip(f"model not present: {source}")

    root = tmp_path
    (root / "Store Sales.pbix").write_bytes(source.read_bytes())
    (root / "notamodel.txt").write_text("this is not a model")

    # `directory` is a constructor argument, not a class attribute: setting it
    # on the class looks like it works and serves the working directory.
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(root)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)

    def serve():
        server.serve_forever(poll_interval=0.05)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(fetch, "check_address", lambda address: None)
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_a_direct_link_is_fetched_and_reads_as_a_model(serving, monkeypatch) -> None:
    from concordance.web import upload

    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    body, name, size = fetch.download(
        f"{serving}/Store%20Sales.pbix", upload.MAX_UPLOAD_BYTES
    )
    try:
        assert name == "Store Sales.pbix"
        assert size > 0
        graph = upload.read_model(body, name, size)
    finally:
        body.close()
    assert graph.model.measures, "the fetched file parsed as a real model"


def test_a_download_past_the_limit_is_stopped_rather_than_finished(serving) -> None:
    """A server that will download whatever a link points at is a server
    anybody can fill a disk with."""
    with pytest.raises(LinkRefused) as raised:
        fetch.download(f"{serving}/Store%20Sales.pbix", 1024)
    assert "limit" in str(raised.value)


def test_a_link_to_something_that_is_not_a_model_is_refused_by_the_parser(
    serving,
) -> None:
    """The fetcher's job ends at the bytes. What they are is the parser's
    question, and it must be asked with the same answer as for an upload."""
    from concordance.web import upload

    body, name, size = fetch.download(f"{serving}/notamodel.txt", upload.MAX_UPLOAD_BYTES)
    try:
        with pytest.raises(upload.UploadRefused):
            upload.read_model(body, name, size)
    finally:
        body.close()


def test_a_missing_file_says_what_the_server_answered(serving) -> None:
    with pytest.raises(LinkRefused) as raised:
        fetch.download(f"{serving}/nothing-here.pbix", 64 * 1024 * 1024)
    assert "404" in str(raised.value)


def test_a_percent_encoded_name_is_decoded() -> None:
    """Caught by testing. A URL path is percent-encoded, so a link ending
    `Store%20Sales.pbix` names a file called "Store Sales.pbix" -- and left
    encoded that becomes the model's name on screen and in every document
    generated from it."""
    assert fetch._name_from("https://x/Store%20Sales.pbix", _Headers()) == "Store Sales.pbix"


def test_a_decoded_name_cannot_smuggle_a_path() -> None:
    """Decoding is what makes this worth checking: `%2F` becomes a separator."""
    from concordance.web import upload

    smuggled = fetch._name_from("https://x/%2e%2e%2f%2e%2e%2fetc%2fpasswd.pbix", _Headers())
    assert "/" not in upload.safe_stem(smuggled)
    assert ".." not in upload.safe_stem(smuggled)
