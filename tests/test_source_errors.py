"""Pointing this at the wrong file should not produce a stack trace.

Handing a corrupt .pbix to the CLI used to end in PBIXRay's own exception --
"RuntimeError: Unknown or unsupported DataModel compression format" -- printed
as an unhandled traceback. That says nothing about which file was at fault or
what to try instead, and it makes ordinary user error look like the tool
falling over.

Failures now funnel into one ``SourceError`` carrying the file's name, a
readable problem, and where useful a hint. The traceback is still one ``--debug``
away for whoever actually wants it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.base import SourceError
from concordance.cli import _load, main

MODEL = Path("data/models/QualityControl.SemanticModel")


@pytest.fixture
def corrupt_pbix(tmp_path: Path) -> Path:
    """A file with the right extension and entirely the wrong contents."""
    path = tmp_path / "broken.pbix"
    path.write_text("this is plainly not a Power BI file", encoding="utf-8")
    return path


# -- what a user sees ---------------------------------------------------------

def test_a_corrupt_file_becomes_a_readable_error(corrupt_pbix: Path) -> None:
    with pytest.raises(SourceError) as caught:
        _load(str(corrupt_pbix))

    error = caught.value
    assert error.source == str(corrupt_pbix)
    # The underlying parser's own words are kept -- they are often the only
    # clue to what went wrong inside a dependency -- but no longer alone.
    assert "compression format" in error.problem
    assert "corrupt" in error.hint


def test_a_missing_path_says_so_before_any_parser_runs(tmp_path: Path) -> None:
    with pytest.raises(SourceError) as caught:
        _load(str(tmp_path / "nothing_here.pbix"))

    assert caught.value.problem == "no such file or folder"
    assert ".pbix" in caught.value.hint


def test_a_folder_that_is_not_a_tmdl_model_explains_itself(tmp_path: Path) -> None:
    empty = tmp_path / "Empty.SemanticModel"
    empty.mkdir()

    with pytest.raises(SourceError) as caught:
        _load(str(empty))
    assert "TMDL" in caught.value.problem


def test_the_rendered_error_names_the_file_and_the_problem(corrupt_pbix: Path) -> None:
    with pytest.raises(SourceError) as caught:
        _load(str(corrupt_pbix))

    rendered = caught.value.render()
    assert str(corrupt_pbix) in rendered
    assert "Cannot read" in rendered
    assert "\n" in rendered, "the problem belongs on its own line, not run together"


# -- the exit code a pipeline reads -------------------------------------------

@pytest.mark.parametrize("command", ["inspect", "extract", "document"])
def test_every_command_exits_two_rather_than_crashing(
    tmp_path: Path, capsys, command: str
) -> None:
    """2 means "your input was wrong", distinct from 1 which several commands
    already use to mean "ran fine, found something you asked me to fail on"."""
    code = main([command, str(tmp_path / "absent.pbix")])
    assert code == 2
    assert "Cannot read" in capsys.readouterr().err


def test_a_good_model_is_unaffected() -> None:
    """The boundary must not swallow a working path."""
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    assert main(["inspect", str(MODEL)]) == 0


# -- the escape hatch ---------------------------------------------------------

def test_debug_re_raises_so_a_traceback_is_still_available(
    tmp_path: Path,
) -> None:
    with pytest.raises(SourceError):
        main(["--debug", "inspect", str(tmp_path / "absent.pbix")])


# -- a partially readable model is read, not rejected -------------------------

def test_a_sparse_model_is_reported_rather_than_refused(tmp_path: Path) -> None:
    """TMDL is a text format and a thin model is a legitimate one.

    The boundary exists to explain genuine failures, not to start rejecting
    files a parser handled fine. What matters is that the counts reported are
    the ones actually found -- an empty model described as empty is honest, an
    empty model described as full would not be.
    """
    root = tmp_path / "Sparse.SemanticModel"
    tables = root / "definition" / "tables"
    tables.mkdir(parents=True)
    (tables / "Solo.tmdl").write_text(
        "table Solo\n\n\tcolumn OnlyOne\n\t\tdataType: string\n", encoding="utf-8"
    )

    graph = _load(str(root))
    assert graph.model.summary()["tables"] == 1
    assert graph.model.summary()["measures"] == 0
