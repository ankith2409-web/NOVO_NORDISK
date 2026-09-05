"""Opening a model from a link rather than from a file on the reader's machine.

Two kinds of link, and they are not the same problem.

A **direct download** -- a raw GitHub URL, an S3 link, a SharePoint
direct-download, anything that answers a GET with the bytes of a `.pbix` --
works today and needs no credentials. That is what most of this module is
about.

A **Power BI Service link** (`app.powerbi.com/groups/.../reports/...`) does
not, and cannot be made to by trying harder: those bytes live behind Azure AD,
and getting them means an app registration, a tenant that permits export, and
a token. None of that exists here yet. So the Service is *recognised* rather
than attempted -- the URL is parsed, the workspace and report identifiers are
pulled out of it, and the reader is told exactly what is missing instead of
being shown a network error. When credentials do arrive, `SERVICE_HINT` is the
only thing that has to change.

Most of the code below, though, is neither of those. It is the fact that
fetching a URL the caller supplied is a server making a request on a stranger's
behalf, which is the shape of every SSRF hole there has ever been. A link to
`http://169.254.169.254/` is a request for the cloud metadata service and the
credentials it hands out; a link to `http://127.0.0.1:8080/` is a request to
whatever else is running on this host. So the destination is resolved and
checked against the private, loopback, link-local and reserved ranges before
anything is sent -- and checked *again* after every redirect, because a public
hostname that 302s to `169.254.169.254` defeats a check done only once.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import tempfile
from dataclasses import dataclass
from typing import BinaryIO
from urllib import error, request
from urllib.parse import unquote, urlparse

#: How long the whole fetch may take, and how much may arrive. The size limit
#: is the upload limit: a model is a model however it got here.
TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 5

#: Hosts that mean "the Power BI Service", where the bytes are behind Azure AD.
_SERVICE_HOSTS = ("powerbi.com", "analysis.windows.net", "fabric.microsoft.com")

#: What a reader is told when they paste a Service link. Written as the next
#: action rather than as a refusal, because there is a real thing they can do.
SERVICE_HINT = (
    "That is a Power BI Service link, and the file behind it is not public — "
    "downloading it needs an Azure AD app registration and a token, which this "
    "server has not been given. Two ways forward: in Power BI Service open the "
    "report, choose File → Download this file, and upload the .pbix here; or "
    "give this server Power BI credentials and paste the same link again."
)


class LinkRefused(Exception):
    """The link will not be fetched, and the message says why.

    Deliberately not the same exception as a failed download. This means "we
    are not going to ask for that", which is a decision; a download failure
    means "we asked and it did not work", which is a circumstance. A reader
    given one when the other happened will look in the wrong place.
    """


@dataclass(frozen=True)
class Destination:
    """One link, classified."""

    url: str
    kind: str  # "download" | "service"
    #: Only for a Service link, and only what the URL itself states.
    workspace: str = ""
    report: str = ""


_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_WORKSPACE = re.compile(rf"/groups/({_GUID}|me)\b")
_REPORT = re.compile(rf"/(?:reports|datasets)/({_GUID})")


def classify(url: str) -> Destination:
    """Which kind of link this is, without fetching anything."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold()
    if any(host == h or host.endswith("." + h) for h in _SERVICE_HOSTS):
        workspace = _WORKSPACE.search(parsed.path)
        report = _REPORT.search(parsed.path)
        return Destination(
            url=url,
            kind="service",
            workspace=workspace.group(1) if workspace else "",
            report=report.group(1) if report else "",
        )
    return Destination(url=url, kind="download")


def _reachable(host: str) -> list[str]:
    """Every address a hostname resolves to, or a refusal saying it does not."""
    try:
        found = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise LinkRefused(f"that host could not be looked up: {exc.strerror or exc}")
    return sorted({info[4][0] for info in found})


def check_address(address: str) -> None:
    """Refuse an address that is not somewhere on the public internet.

    Every range here is one a link could name to make this server fetch
    something on the caller's behalf that the caller could not reach itself:
    the loopback interface, this machine's own network, and -- the one that
    matters most in a cloud -- `169.254.169.254`, the metadata service, which
    hands out credentials to anything that asks from inside the instance.
    """
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        raise LinkRefused(f"{address} is not an address this can check") from None

    # Link-local before private: Python counts link-local as private, so
    # checking private first would report 169.254.169.254 -- the cloud
    # metadata service, the whole reason this function exists -- as merely "a
    # private network address", which is true and unhelpful.
    for kind, bad in (
        ("a loopback address", found.is_loopback),
        ("a link-local address", found.is_link_local),
        ("a private network address", found.is_private),
        ("a reserved address", found.is_reserved),
        ("an unspecified address", found.is_unspecified),
        ("a multicast address", found.is_multicast),
    ):
        if bad:
            raise LinkRefused(
                f"that link points at {kind} ({address}). Links are fetched by this "
                "server, so it will only fetch from the public internet — otherwise a "
                "link could reach whatever else is running on this machine."
            )


def check_url(url: str) -> str:
    """Refuse a URL before any request is made. Returns the hostname."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise LinkRefused(
            f"{parsed.scheme or 'that'} is not a scheme this fetches. "
            "Paste an http or https link."
        )
    if not parsed.hostname:
        raise LinkRefused("that link has no host in it.")
    for address in _reachable(parsed.hostname):
        check_address(address)
    return parsed.hostname


class _Guarded(request.HTTPRedirectHandler):
    """Re-checks every hop of a redirect chain.

    A destination validated once is a destination validated for the first
    request only. A public hostname that answers 302 with
    `http://169.254.169.254/` reaches the metadata service through a check that
    passed -- so the check runs again here, on the URL being redirected *to*,
    before the opener is allowed to follow it.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _name_from(url: str, headers) -> str:
    """What to call the file, from the header first and the path second."""
    disposition = headers.get("Content-Disposition", "") if headers else ""
    found = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    if found:
        return found.group(1).strip()
    # Unquoted: a URL path is percent-encoded, so `Store%20Sales.pbix` is a
    # file called "Store Sales.pbix" and not one called "Store%20Sales.pbix".
    # Left encoded it becomes the model's name, on screen and in every document
    # generated from it. `safe_stem` downstream strips anything a decoded name
    # could smuggle in, including a separator.
    tail = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return tail or "model.pbix"


def download(url: str, limit: int) -> tuple[BinaryIO, str, int]:
    """Fetch a model from a direct link. ``(body, filename, bytes)``.

    Streamed into a temporary file rather than held in memory, and stopped the
    moment it exceeds ``limit`` -- a server that will download whatever a link
    points at is a server anybody can fill a disk with. The declared
    `Content-Length` is checked first as a cheap refusal and then the real
    length is checked as it arrives, because the header is something the far
    end chose.
    """
    check_url(url)
    opener = request.build_opener(_Guarded)
    asked = request.Request(url, headers={"User-Agent": "Concordance"})

    try:
        with opener.open(asked, timeout=TIMEOUT_SECONDS) as answer:
            declared = int(answer.headers.get("Content-Length") or 0)
            if declared > limit:
                raise LinkRefused(
                    f"that file is {declared // (1024 * 1024)}MB and the limit is "
                    f"{limit // (1024 * 1024)}MB."
                )
            name = _name_from(answer.geturl(), answer.headers)
            body = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
            size = 0
            while True:
                chunk = answer.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    body.close()
                    raise LinkRefused(
                        f"that download passed the {limit // (1024 * 1024)}MB limit "
                        "and was stopped."
                    )
                body.write(chunk)
    except LinkRefused:
        raise
    except error.HTTPError as exc:
        raise LinkRefused(
            f"that link answered {exc.code} {exc.reason}. If it needs a sign-in, the "
            "server cannot follow it — download the file and upload it instead."
        ) from None
    except error.URLError as exc:
        raise LinkRefused(f"that link could not be reached: {exc.reason}") from None
    except (TimeoutError, socket.timeout):
        raise LinkRefused(
            f"that link did not answer within {TIMEOUT_SECONDS} seconds."
        ) from None

    if size == 0:
        raise LinkRefused("that link returned an empty file.")
    body.seek(0)
    return body, name, size
