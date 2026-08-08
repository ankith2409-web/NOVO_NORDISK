"""The adapter contract.

A source platform joins Concordance by implementing this one method. Nothing
downstream knows or cares where a model came from, which is the property that
makes Snowflake and Databricks additive rather than structural work.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, runtime_checkable

from concordance.model import SemanticModel
from concordance.normalize.dax import extract_references


class SourceError(Exception):
    """A model could not be read, for a reason worth showing a person.

    Extraction failures arrive from three very different places -- this
    project's own validation, the standard library when a path is wrong, and
    whatever a third-party parser raises when a file is not the format it
    claims to be. Left alone, the third kind reaches a user as a stack trace
    ending in something like "Unknown or unsupported DataModel compression
    format", which says nothing about which file was at fault or what to do.

    Every entry point funnels them into this one type so the answer is always a
    sentence naming the file and the problem, and the traceback stays available
    behind ``--debug`` for whoever actually wants it.
    """

    def __init__(self, source: str, problem: str, hint: str = "") -> None:
        self.source = source
        self.problem = problem
        self.hint = hint
        super().__init__(f"Cannot read {source}: {problem}")

    def render(self) -> str:
        lines = [f"Cannot read {self.source}", f"  {self.problem}"]
        if self.hint:
            lines.append(f"  {self.hint}")
        return "\n".join(lines)


@runtime_checkable
class SourceAdapter(Protocol):
    """Translates one platform's metadata into the canonical object model."""

    #: Short identifier recorded on the extracted model, e.g. "pbix".
    source_type: str

    def extract(self, source: str) -> SemanticModel:
        """Read ``source`` and return a fully populated semantic model."""
        ...


def is_measure_container(has_measures: bool, visible_columns: int) -> bool:
    """Is this table a grouping for measures rather than a data entity?

    Both source formats express the same idea differently -- a .pbix
    measure-only table carries no columns at all, while TMDL requires at least
    one, so modellers add a hidden placeholder. Deciding it here keeps the two
    adapters from drifting into subtly different meanings for the same field;
    each supplies the visible-column count its format can determine.

    Requiring measures matters: a table with neither columns nor measures is
    empty, not a measure container.
    """
    return has_measures and visible_columns == 0


def resolve_table_dependencies(model: SemanticModel) -> None:
    """Bind whole-table references in measures, in place.

    ``COUNTROWS(Patient)`` depends on the Patient table, but the lexer can only
    report that *some* bare word was used that way -- whether it names a table,
    a DAX keyword or a variable is a question only the finished model can
    answer. So resolution runs once every table name is known, which is why it
    is a post-pass rather than part of building each measure.

    Without it, a measure like ``Adverse Event Count`` records no dependency at
    all, and changing its source table looks like it affects nothing.
    """
    names = {t.name.casefold(): t.name for t in model.tables}
    model.measures = [
        replace(
            measure,
            depends_on_tables=frozenset(
                names[candidate.casefold()]
                for candidate in extract_references(measure.expression).table_candidates
                if candidate.casefold() in names
            ),
        )
        for measure in model.measures
    ]
