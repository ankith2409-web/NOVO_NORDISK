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


@runtime_checkable
class SourceAdapter(Protocol):
    """Translates one platform's metadata into the canonical object model."""

    #: Short identifier recorded on the extracted model, e.g. "pbix".
    source_type: str

    def extract(self, source: str) -> SemanticModel:
        """Read ``source`` and return a fully populated semantic model."""
        ...


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
