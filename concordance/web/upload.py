"""Read a model out of bytes somebody uploaded, without trusting any of them.

Every other way into this tool names a path an operator typed. This one does
not: the bytes arrive from a browser, and the person sending them is whoever
found the URL. So the rules here are stricter than anywhere else in the
codebase, and they are all in this one module rather than spread through the
request handler -- an archive is unpacked in exactly one place, and that place
is unit-testable without opening a socket.

Three specific attacks shape the code below.

*Path traversal.* A zip entry's name is attacker-chosen text, and nothing stops
it being ``../../../../etc/cron.d/x`` or ``/etc/passwd``. ``ZipFile.extract``
sanitises those, but ``ZipFile.open`` plus a hand-rolled write does not, and the
sanitising is silent -- an entry that escapes is quietly rewritten rather than
refused, so a malicious archive and a merely odd one look identical afterwards.
Here the check is explicit and the answer is refusal, because an archive
containing an absolute path is not a Power BI export that needs fixing up; it is
an archive that should never be unpacked.

*Symlinks.* A zip can carry a symlink, and Python will happily create one
pointing at ``/etc/shadow``. The next read of "a file inside the extract" then
reads that. Nothing legitimate in a ``.SemanticModel`` folder is a symlink, so
they are refused outright rather than resolved and checked.

*Decompression bombs.* A few hundred kilobytes of zip can be many gigabytes of
zeroes, and the process that unpacks it dies rather than answering 400. The
declared uncompressed size is checked before unpacking and the real size is
counted during it, because the declared one is also attacker-chosen.

Nothing survives the call. The upload is written to a temporary directory,
parsed, and the directory is removed in a ``finally`` -- what the caller gets
back is an in-memory graph, and a customer's proprietary model is not left
sitting on a demo server's disk.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO

from concordance.adapters.base import SourceError
from concordance.graph.csg import SemanticGraph

#: The largest upload this server will read, in bytes.
#:
#: A ``.pbix`` carries its data as well as its model, so real ones are tens of
#: megabytes; a TMDL folder is text and is usually under one. 64MB clears the
#: first comfortably without turning "post a big file" into a way to exhaust a
#: small container's disk. The body is streamed to disk rather than buffered, so
#: this bounds storage, not memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

#: Ceilings on what one archive may expand into. The entry count matters as much
#: as the total: a million empty files is not large, and is still an afternoon
#: of ``rglob`` for the adapter that walks them.
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000

#: What an uploaded file may be called. Anything else is refused with the list,
#: rather than attempted and failed further in with a parser's own wording.
ACCEPTED = (".pbix", ".zip", ".tmdl")

#: Characters kept from an uploaded filename. Everything else is replaced.
#:
#: The stem becomes a real path on this server and, for a ``.pbix``, the model's
#: name -- ``PbixAdapter`` takes it from the file. So it is rebuilt from a
#: whitelist rather than escaped or trusted: a name is not permitted to contain
#: a separator, a null, a leading dot, or anything else that means something to
#: a filesystem.
_NAME_SAFE = re.compile(r"[^A-Za-z0-9 ._-]+")


def safe_stem(filename: str) -> str:
    """The uploaded name, reduced to something safe to use as a path segment.

    ``Path(filename).name`` first, so a browser that sends a full path -- some
    do -- contributes only its last segment. Then the whitelist, then a
    non-empty fallback, because "" is a valid result of stripping and a
    catastrophic one to use as a filename.
    """
    stem = Path(Path(filename).name).stem
    cleaned = _NAME_SAFE.sub("-", stem).strip(" .-")
    return cleaned[:80] or "uploaded-model"


def extension(filename: str) -> str:
    return Path(Path(filename).name).suffix.lower()


class UploadRefused(Exception):
    """The upload was rejected before any attempt to parse it.

    Separate from ``SourceError`` on purpose. ``SourceError`` means "this is a
    model and it is broken"; this means "this is not something we will open".
    The interface shows them differently, and conflating them would report a
    refused archive as a corrupt one.
    """


def read_model(body: BinaryIO, filename: str, declared_length: int) -> SemanticGraph:
    """Read one uploaded file into a semantic graph, leaving nothing on disk.

    ``body`` is streamed rather than read whole: ``declared_length`` is a header
    the client chose, so it is checked first as a cheap refusal and then checked
    *again* against what actually arrives. A client that under-declares its
    length gets the same answer as one that declares it honestly.
    """
    suffix = extension(filename)
    if suffix not in ACCEPTED:
        raise UploadRefused(
            f"{suffix or 'that file'} is not a format this reads. "
            f"Upload a .pbix, or a .zip of a .pbip / .SemanticModel folder."
        )
    if declared_length > MAX_UPLOAD_BYTES:
        raise UploadRefused(
            f"That file is {_megabytes(declared_length)}, and the limit is "
            f"{_megabytes(MAX_UPLOAD_BYTES)}."
        )

    stem = safe_stem(filename)
    workspace = Path(tempfile.mkdtemp(prefix="concordance-upload-"))
    try:
        landed = workspace / f"{stem}{suffix}"
        _stream_to(body, landed, declared_length)

        if suffix == ".zip":
            return SemanticGraph(_read_archive(landed, workspace / "unpacked", stem))
        if suffix == ".tmdl":
            return SemanticGraph(_read_loose_tmdl(landed, workspace / "single", stem))
        return SemanticGraph(_read_pbix(landed))
    finally:
        # Unconditional, and ignoring errors: a failure to clean up must not
        # replace the real answer -- including the real error -- with an
        # unlink complaint about a path the caller never saw.
        shutil.rmtree(workspace, ignore_errors=True)


def _megabytes(count: int) -> str:
    return f"{count / (1024 * 1024):.0f}MB"


def _without(message: str, workspace: Path) -> str:
    """Strip this server's temporary paths out of a message bound for a browser.

    Every adapter names the path it was given, which is correct everywhere else
    -- an operator who typed the path wants to see it. Here nobody typed it, so
    it is a directory name the reader has never heard of, sitting in front of
    the part of the sentence that actually matters.
    """
    return message.replace(f"{workspace}/", "").replace(str(workspace), "the upload")


def _stream_to(body: BinaryIO, destination: Path, declared_length: int) -> None:
    """Copy exactly ``declared_length`` bytes from the request into a file.

    Chunked so a 64MB upload costs 64KB of memory rather than 64MB of it. This
    server is threaded, and a handful of concurrent uploads buffered whole is
    the difference between a slow demo and a dead container.

    Exactly the declared length, and no attempt to read past it. That pairing
    is what makes the size limit real rather than advisory: the caller checked
    the declared length against the ceiling before getting here, and this reads
    no more than it, so a client can neither claim a gigabyte (refused from the
    header, before the socket is touched) nor claim ten bytes and then stream
    one (only ten bytes are ever read). It is also the only HTTP-correct
    choice, since reading past ``Content-Length`` would consume whatever the
    client sent next on the same connection.

    A client that under-declares therefore gets a truncated file, which then
    fails to parse and is reported as a broken upload. That is the right
    outcome: the alternative is an unbounded read.
    """
    remaining = declared_length
    written = 0
    with destination.open("wb") as out:
        while remaining > 0:
            chunk = body.read(min(64 * 1024, remaining))
            if not chunk:
                break  # the client stopped early; what arrived is all there is
            written += len(chunk)
            out.write(chunk)
            remaining -= len(chunk)
    if written == 0:
        raise UploadRefused("That file is empty.")


def _read_pbix(path: Path):
    from concordance.adapters.pbix import PbixAdapter

    try:
        return PbixAdapter().extract(str(path))
    except SourceError:
        raise
    except Exception as error:
        # PBIXRay raises whatever it likes at whatever depth it likes. The CLI
        # keeps the exception's type name, because for someone at a terminal it
        # is frequently the only clue to what actually went wrong. This path is
        # only ever reached from a browser, where "RuntimeError:" in front of a
        # sentence reads as a crash rather than as a diagnosis -- and the hint
        # below is the part that person can act on.
        raise SourceError(
            path.name,
            str(error) or type(error).__name__,
            "This usually means the file is not a .pbix, or was truncated in "
            "transit. Try re-saving it from Power BI Desktop.",
        ) from error


def _read_loose_tmdl(path: Path, destination: Path, stem: str):
    """Accept a single ``.tmdl`` file by giving it the folder it expects.

    ``TmdlAdapter`` looks for a ``definition/`` directory, which a lone file
    obviously does not have -- so one is built around it. This is not a
    workaround for a limitation: someone exporting one table from a model and
    dropping it in is a real thing to want to do, and refusing it because the
    surrounding folder is missing would be refusing on a technicality.
    """
    from concordance.adapters.tmdl import TmdlAdapter

    root = destination / f"{stem}.SemanticModel" / "definition"
    root.mkdir(parents=True)
    shutil.copyfile(path, root / path.name)
    try:
        return TmdlAdapter().extract(str(root.parent))
    except SourceError:
        raise
    except (ValueError, KeyError) as error:
        raise SourceError(path.name, str(error)) from error


def _read_archive(archive: Path, destination: Path, stem: str):
    from concordance.adapters.tmdl import TmdlAdapter

    try:
        with zipfile.ZipFile(archive) as bundle:
            _unpack(bundle, destination)
    except zipfile.BadZipFile as error:
        raise SourceError(
            archive.name,
            f"not a readable zip: {error}",
            "Zip the .SemanticModel or .pbip folder itself, not a shortcut to it.",
        ) from error

    # Checked before `_model_root`, which renames the extraction root to give
    # the model a recognisable name -- so afterwards `destination` is a path
    # that no longer exists and this would refuse every archive that took that
    # branch. Found by uploading all three archive shapes rather than the two
    # obvious ones.
    if not any(destination.rglob("*.tmdl")):
        # Answered here rather than left to the adapter. Its message names the
        # folder it was handed, which for an upload is a temporary directory
        # this server invented -- so the reader is told that
        # `/tmp/concordance-upload-8f3a1c` is not a TMDL model, which is true,
        # useless, and leaks a server path into a browser.
        raise SourceError(
            archive.name,
            "no .tmdl files inside it",
            "A .pbix is a single file, but a .pbip project is a folder. Zip "
            "the folder itself -- including its .SemanticModel and the "
            "definition/ inside that -- and upload the zip.",
        )

    root = _model_root(destination, stem)
    try:
        return TmdlAdapter().extract(str(root))
    except SourceError:
        raise
    except (ValueError, KeyError) as error:
        raise SourceError(
            archive.name,
            _without(str(error), destination),
            "Power BI writes this folder itself, so a model that fails here "
            "has usually been edited by hand since.",
        ) from error


def _unpack(bundle: zipfile.ZipFile, destination: Path) -> None:
    """Extract an archive, refusing anything that is not a plain nested file.

    Written out rather than delegated to ``extractall`` because refusal is the
    point. ``extractall`` sanitises a traversing path and carries on; that turns
    a hostile archive into a merely surprising one, and leaves no signal that
    anything was wrong. Here each entry is checked and the first bad one ends
    the whole extraction.
    """
    entries = bundle.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise UploadRefused(
            f"That archive holds {len(entries)} entries, and the limit is "
            f"{MAX_ARCHIVE_ENTRIES}. A semantic model has a few hundred."
        )

    declared = sum(entry.file_size for entry in entries)
    if declared > MAX_UNPACKED_BYTES:
        raise UploadRefused(
            f"That archive expands to {_megabytes(declared)}, and the limit is "
            f"{_megabytes(MAX_UNPACKED_BYTES)}."
        )

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    unpacked = 0

    for entry in entries:
        name = entry.filename
        if _is_symlink(entry):
            raise UploadRefused(
                f"{name!r} is a symbolic link. A semantic model contains none, "
                f"and following one would read a file outside the upload."
            )
        if name.startswith("/") or name.startswith("\\") or ":" in name.split("/")[0]:
            raise UploadRefused(f"{name!r} is an absolute path.")
        if ".." in Path(name.replace("\\", "/")).parts:
            raise UploadRefused(f"{name!r} points outside the archive.")

        target = (root / name.replace("\\", "/")).resolve()
        # Belt and braces: the two checks above cover the paths anyone thinks
        # of, and this covers the ones nobody did. `is_relative_to` compares
        # resolved paths, so it also catches an entry that escapes through a
        # directory created earlier in the same archive.
        if not target.is_relative_to(root):
            raise UploadRefused(f"{name!r} would be written outside the upload.")

        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(entry) as source, target.open("wb") as out:
            while chunk := source.read(64 * 1024):
                unpacked += len(chunk)
                # Counted as it lands, because `file_size` above is a number the
                # archive states about itself. A bomb declares whatever passes
                # the cheap check and expands to whatever it likes.
                if unpacked > MAX_UNPACKED_BYTES:
                    raise UploadRefused(
                        f"That archive expands past the "
                        f"{_megabytes(MAX_UNPACKED_BYTES)} limit."
                    )
                out.write(chunk)


def _is_symlink(entry: zipfile.ZipInfo) -> bool:
    """Whether a zip entry is a symlink, per the Unix mode in its attributes.

    The high 16 bits of ``external_attr`` are ``st_mode`` when the archive was
    written on a Unix system; ``0xA000`` is ``S_IFLNK``. An archive written on
    Windows has no mode there and reads as 0, which is correctly not a symlink.
    """
    return (entry.external_attr >> 16) & 0xF000 == 0xA000


def _model_root(destination: Path, stem: str) -> Path:
    """Find the model folder inside an extracted archive.

    Matters because the *name* comes from this folder. Zipping a model produces
    any of three shapes -- the ``.SemanticModel`` folder itself, a ``.pbip``
    project containing it, or the contents with no wrapper -- and handing the
    adapter the extraction root for all three would name every uploaded model
    after a temporary directory.

    Shallowest match wins, so a model that happens to contain a nested folder
    ending in ``.SemanticModel`` resolves to the outer one.
    """
    named = sorted(
        (path for path in destination.rglob("*") if path.is_dir() and _looks_like_model(path)),
        key=lambda path: (len(path.parts), str(path)),
    )
    if named:
        return named[0]

    # No named folder: either the archive holds `definition/` at its top level,
    # or a single wrapper directory that a zip tool added. Renaming the root to
    # the uploaded filename's stem is what gives the model a name a person will
    # recognise instead of `concordance-upload-8f3a1c`.
    inner = [path for path in destination.iterdir() if path.is_dir()]
    base = inner[0] if len(inner) == 1 and not _has_tmdl_directly(destination) else destination
    renamed = base.with_name(f"{stem}.SemanticModel")
    if renamed != base and not renamed.exists():
        base.rename(renamed)
        return renamed
    return base


def _looks_like_model(path: Path) -> bool:
    return path.name.endswith((".SemanticModel", ".Dataset"))


def _has_tmdl_directly(path: Path) -> bool:
    return any(path.glob("*.tmdl")) or (path / "definition").is_dir()
