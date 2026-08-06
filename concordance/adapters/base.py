"""The adapter contract.

A source platform joins Concordance by implementing this one method. Nothing
downstream knows or cares where a model came from, which is the property that
makes Snowflake and Databricks additive rather than structural work.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from concordance.model import SemanticModel


@runtime_checkable
class SourceAdapter(Protocol):
    """Translates one platform's metadata into the canonical object model."""

    #: Short identifier recorded on the extracted model, e.g. "pbix".
    source_type: str

    def extract(self, source: str) -> SemanticModel:
        """Read ``source`` and return a fully populated semantic model."""
        ...
