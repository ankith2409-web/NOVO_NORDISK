"""Power BI .pbix adapter, built on PBIXRay (MIT).

PBIXRay decodes the compressed VertiPaq model inside a .pbix; this module turns
what it surfaces into canonical objects and attaches a fingerprint to each one.

Two Power BI details are handled here rather than leaking downstream:
auto-generated date tables are marked as system objects so they do not pollute
generated documentation, and unqualified ``[Name]`` references are resolved
against the model's real measure names to tell a measure reference from a
same-table column reference.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from pbixray import PBIXRay

from concordance.adapters.base import is_measure_container, resolve_table_dependencies
from concordance.fingerprint import fingerprint_dax, fingerprint_parts, fingerprint_text
from concordance.model import (
    Column,
    CoverageGap,
    Hierarchy,
    HierarchyLevel,
    Measure,
    Relationship,
    SemanticModel,
    Table,
)
from concordance.normalize.dax import extract_references

#: Model features PBIXRay surfaces that this adapter does not yet turn into
#: graph objects. All three sample models report zero rows *and no columns* for
#: every one of these, so their schemas cannot be learned from the data on hand
#: -- writing extraction code against a guessed column layout would be exactly
#: the kind of confident-but-wrong work this project is meant to catch. Instead
#: their presence is counted and reported, so an unseen model that does use them
#: produces a visible gap rather than a silently incomplete graph.
_UNEXTRACTED_FEATURES: tuple[tuple[str, str], ...] = (
    ("tmschema_kpis", "KPI objects"),
    ("rls", "row-level security roles"),
    ("ols", "object-level security"),
    ("tmschema_calculation_groups", "calculation groups"),
    ("tmschema_calculation_items", "calculation items"),
    ("tmschema_perspectives", "perspectives"),
)

#: Power BI creates a hidden date table per date column, plus a template table.
#: They carry no business meaning and would otherwise swamp the documentation.
_SYSTEM_TABLE = re.compile(
    r"^(DateTableTemplate_|LocalDateTable_)[0-9a-fA-F-]+$"
)


class PbixAdapter:
    """Extracts a semantic model from a .pbix file."""

    source_type = "pbix"

    def extract(self, source: str) -> SemanticModel:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"no such .pbix file: {source}")

        raw = PBIXRay(str(path))
        model = SemanticModel(
            name=path.stem,
            source_path=str(path),
            source_type=self.source_type,
        )

        power_query = self._power_query_by_table(raw)
        calc_columns = self._calculated_column_expressions(raw)

        model.columns = self._build_columns(raw, calc_columns)
        measure_rows = _rows(raw.dax_measures)

        # PBIXRay lists only tables that store data, so a table holding nothing
        # but measures -- "Analysis DAX" and "Design DAX" in the Sales & Returns
        # model, 58 measures between them -- never appears. Left uncorrected the
        # graph grows attribute-less placeholder nodes where those tables should
        # be, and every measure they contain is orphaned from its parent.
        declared = list(raw.tables)
        known = {name.casefold() for name in declared}
        measure_hosts = {
            str(r.get("TableName", "")).strip()
            for r in measure_rows
            if str(r.get("TableName", "")).strip()
        }
        with_columns = {c.table.casefold() for c in model.columns}
        implied = sorted(t for t in measure_hosts if t.casefold() not in known)

        for name in declared + implied:
            model.tables.append(
                Table(
                    name=name,
                    fingerprint=fingerprint_text(name),
                    is_system=bool(_SYSTEM_TABLE.match(name)),
                    # PBIXRay does not surface column visibility, so every
                    # extracted column counts as visible here.
                    is_measure_only=is_measure_container(
                        has_measures=name in measure_hosts,
                        visible_columns=0 if name.casefold() not in with_columns else 1,
                    ),
                    power_query=power_query.get(name),
                )
            )
        known_measures = {
            str(r.get("Name", "")).strip().casefold() for r in measure_rows
        }
        model.measures = [
            self._build_measure(r, known_measures) for r in measure_rows
        ]

        model.relationships = self._build_relationships(raw)
        model.hierarchies = self._build_hierarchies(raw)
        model.coverage_gaps = self._coverage_gaps(raw)
        resolve_table_dependencies(model)
        return model

    # -- pieces -------------------------------------------------------------

    def _build_hierarchies(self, raw: PBIXRay) -> list[Hierarchy]:
        """Assemble hierarchies from their separately-stored levels.

        PBIXRay keeps hierarchies and their levels in two tables joined on
        HierarchyID, with levels ordered by Ordinal. Order is load-bearing: a
        drill path of Year -> Quarter -> Month is a different hierarchy from
        Month -> Quarter -> Year, so it is part of the fingerprint.
        """
        levels_by_hierarchy: dict[str, list[HierarchyLevel]] = {}
        for row in _rows(_safe(raw, "tmschema_levels")):
            key = str(row.get("HierarchyID", "")).strip()
            if not key:
                continue
            levels_by_hierarchy.setdefault(key, []).append(
                HierarchyLevel(
                    ordinal=int(row.get("Ordinal", 0) or 0),
                    name=str(row.get("Name", "")).strip(),
                    column=str(row.get("ColumnName", "")).strip(),
                )
            )

        out: list[Hierarchy] = []
        for row in _rows(_safe(raw, "tmschema_hierarchies")):
            table = str(row.get("TableName", "")).strip()
            name = str(row.get("Name", "")).strip()
            if not table or not name:
                continue

            key = str(row.get("ID", "")).strip()
            levels = tuple(
                sorted(levels_by_hierarchy.get(key, []), key=lambda level: level.ordinal)
            )
            out.append(
                Hierarchy(
                    table=table,
                    name=name,
                    levels=levels,
                    fingerprint=fingerprint_parts(
                        table,
                        name,
                        *(f"{lv.ordinal}:{lv.name}:{lv.column}" for lv in levels),
                    ),
                    is_hidden=bool(int(row.get("IsHidden", 0) or 0)),
                    display_folder=_optional(row.get("DisplayFolder")),
                    description=_optional(row.get("Description")),
                )
            )
        return out

    def _coverage_gaps(self, raw: PBIXRay) -> list[CoverageGap]:
        """Report model features present in the source but not yet extracted."""
        gaps: list[CoverageGap] = []
        for attribute, label in _UNEXTRACTED_FEATURES:
            frame = _safe(raw, attribute)
            if frame is None:
                continue
            try:
                count = len(frame)
            except TypeError:
                continue
            if count:
                gaps.append(
                    CoverageGap(
                        feature=label,
                        count=count,
                        reason="present in the model but not yet extracted by this adapter",
                    )
                )
        return gaps

    def _power_query_by_table(self, raw: PBIXRay) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in _rows(raw.power_query):
            name = str(row.get("TableName", "")).strip()
            if name and _present(row.get("Expression")):
                out[name] = str(row.get("Expression"))
        return out

    def _calculated_column_expressions(self, raw: PBIXRay) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        for row in _rows(raw.dax_columns):
            table = str(row.get("TableName", "")).strip()
            column = str(row.get("ColumnName", "")).strip()
            expr = row.get("Expression")
            if table and column and _present(expr):
                out[(table, column)] = str(expr)
        return out

    def _build_columns(
        self, raw: PBIXRay, calc: dict[tuple[str, str], str]
    ) -> list[Column]:
        columns: list[Column] = []
        seen: set[tuple[str, str]] = set()

        for row in _rows(raw.schema):
            table = str(row.get("TableName", "")).strip()
            name = str(row.get("ColumnName", "")).strip()
            if not table or not name:
                continue
            seen.add((table, name))
            expr = calc.get((table, name))
            columns.append(
                Column(
                    table=table,
                    name=name,
                    data_type=str(row.get("PandasDataType", "unknown")),
                    expression=expr,
                    # A stored column's identity is its name and type; a
                    # calculated one's is the expression that produces it.
                    fingerprint=(
                        fingerprint_dax(expr)
                        if expr is not None
                        else fingerprint_parts(
                            table, name, str(row.get("PandasDataType", "unknown"))
                        )
                    ),
                )
            )

        # A calculated column can exist without a schema row; keep it rather
        # than silently dropping a real model object.
        for (table, name), expr in calc.items():
            if (table, name) not in seen:
                columns.append(
                    Column(
                        table=table,
                        name=name,
                        data_type="calculated",
                        expression=expr,
                        fingerprint=fingerprint_dax(expr),
                    )
                )

        return columns

    def _build_measure(self, row: dict, known_measures: set[str]) -> Measure:
        table = str(row.get("TableName", "")).strip()
        name = str(row.get("Name", "")).strip()
        # `row.get("Expression") or ""` looks safe but is not: a pandas NaN is
        # truthy in Python, so a genuinely missing expression would silently
        # become the literal three-character string "nan" instead of an empty
        # one, and get fingerprinted as if it were real DAX. None of the three
        # sample models trigger this, but a broken or placeholder measure in an
        # unseen dataset could -- so a missing expression is treated as broken
        # rather than guessed at.
        expression = str(row.get("Expression")) if _present(row.get("Expression")) else ""

        refs = extract_references(expression)

        # A bare [Name] is either a measure or a column in this measure's own
        # table; the model is the only way to tell them apart.
        measures = {r for r in refs.unqualified if r.casefold() in known_measures}
        same_table_columns = {
            (table, r) for r in refs.unqualified if r.casefold() not in known_measures
        }

        # A qualified Table[Name] is usually a column, but DAX also permits
        # qualifying a measure reference -- 'Analysis DAX'[WIF Adjusted Net Sales]
        # in the Sales & Returns model does exactly that. Measure names are
        # unique model-wide, so a name match is enough to classify it.
        qualified_columns: set[tuple[str, str]] = set()
        for ref_table, ref_name in refs.columns:
            if ref_name.casefold() in known_measures:
                measures.add(ref_name)
            else:
                qualified_columns.add((ref_table, ref_name))

        return Measure(
            table=table,
            name=name,
            expression=expression,
            fingerprint=fingerprint_dax(expression),
            display_folder=_optional(row.get("DisplayFolder")),
            description=_optional(row.get("Description")),
            depends_on_columns=frozenset(qualified_columns | same_table_columns),
            depends_on_measures=frozenset(measures),
        )

    def _build_relationships(self, raw: PBIXRay) -> list[Relationship]:
        out: list[Relationship] = []
        for row in _rows(raw.relationships):
            from_table = str(row.get("FromTableName", "")).strip()
            from_column = str(row.get("FromColumnName", "")).strip()
            to_table = str(row.get("ToTableName", "")).strip()
            to_column = str(row.get("ToColumnName", "")).strip()
            cardinality = str(row.get("Cardinality", "")).strip()
            cross_filter = str(row.get("CrossFilteringBehavior", "")).strip()
            is_active = bool(row.get("IsActive", True))

            out.append(
                Relationship(
                    from_table=from_table,
                    from_column=from_column,
                    to_table=to_table,
                    to_column=to_column,
                    cardinality=cardinality,
                    cross_filter=cross_filter,
                    is_active=is_active,
                    # Direction, cardinality, cross-filter and active state all
                    # change what the join does, so all of them are in the hash.
                    fingerprint=fingerprint_parts(
                        from_table, from_column, to_table, to_column,
                        cardinality, cross_filter, str(is_active),
                    ),
                )
            )
        return out


def _safe(raw: PBIXRay, attribute: str):
    """Read an optional PBIXRay table, tolerating absence.

    Not every .pbix carries every TMSCHEMA table, and the library version in use
    may not expose every attribute. A missing optional feature must degrade to
    "nothing to extract" rather than failing the whole extraction.
    """
    try:
        return getattr(raw, attribute, None)
    except Exception:
        return None


def _rows(frame) -> list[dict]:
    """Normalise a PBIXRay dataframe into plain dicts."""
    if frame is None:
        return []
    if isinstance(frame, pd.DataFrame):
        # An empty frame may also carry no columns at all, in which case
        # to_dict would produce nothing useful anyway.
        return [] if frame.empty else frame.to_dict("records")
    return list(frame)


def _present(value) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip() != ""


def _optional(value) -> str | None:
    return str(value).strip() if _present(value) else None
