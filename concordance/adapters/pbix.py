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
    CalculationGroup,
    CalculationItem,
    Column,
    ColumnVariation,
    CoverageGap,
    Hierarchy,
    HierarchyLevel,
    Kpi,
    Measure,
    ObjectPermission,
    Perspective,
    PerspectiveMember,
    Relationship,
    SecurityRole,
    SemanticModel,
    Table,
    TablePermission,
)
from concordance.normalize.calctable import column_names
from concordance.normalize.dax import extract_references
from concordance.normalize.mquery import canonicalise as canonicalise_m

#: Model features PBIXRay surfaces that this adapter does not turn into graph
#: objects. Every sample model reports zero rows *and no columns* for these, so
#: their layout cannot be learned by inspecting the data on hand -- their
#: presence is counted and reported instead, so an unseen model that does use
#: them produces a visible gap rather than a silently incomplete graph.
#:
#: RLS and calculation groups used to be on this list for the same reason. They
#: came off it once it was clear the schema did not have to be guessed at all:
#: PBIXRay builds these frames from SQL written out in its own source, so the
#: column names are a published contract rather than an inference from whatever
#: sample happened to be at hand. Extraction is written against that contract
#: and tested against frames shaped by it.
#: (frame, label, minimum count that means something). Neither of these was on
#: the list before, which was itself a hole: the documentation claimed
#: translations and column variations were reported, and for this format
#: nothing ever looked.
_UNEXTRACTED_FEATURES: tuple[tuple[str, str, int], ...] = (
    # Every model carries exactly one base culture -- `en-US` in all three
    # samples. That is the language the model is written in, not a translation
    # of it, so a gap raised at one row would fire on every model ever read and
    # teach a reader to skip the section. Two or more means somebody actually
    # translated something, and then the names in this document are not the
    # names all of its readers see.
    ("tmschema_cultures", "translations", 2),
)

#: Translations stop here deliberately. The frame carries `ObjectType` as a
#: bare integer and `ObjectID` as a key into a different table per type, and
#: PBIXRay publishes no mapping for that enum -- unlike every other construct
#: read here, whose columns come from SQL in its own source. Guessing which
#: integer means "measure" would attach translated names to the wrong objects,
#: and a document confidently calling one measure by another's French name is
#: worse than one that says it did not read the translations.
_TRANSLATION_GAP = (
    "translated object names are present but not attached to their objects: the "
    "reader exposes the target as an untyped id and publishes no mapping for it, "
    "so which name belongs to which measure cannot be established without "
    "guessing"
)

#: Said whenever a table's rows are fetched at query time rather than stored.
#:
#: Every figure this tool produces comes from running a measure's own SQL
#: against the model's own rows. For a DirectQuery table there are none in the
#: file -- so the measure fails, and the failure reads as a defect here rather
#: than as the file saying where its data actually is.
_REMOTE_TABLE_GAP = (
    "{names} are queried from the source at run time rather than stored in this "
    "file, so this file carries no rows for them: figures over them cannot be "
    "computed here, and their absence is the file's shape rather than a limit of "
    "this tool"
)

#: Why the Q&A synonyms are counted and not used.
#:
#: They looked like the best thing left unread: a glossary wants the words a
#: business actually uses, and this is where Power BI keeps them. Reading them
#: is the part that settles it. Across the three sample files there are 565
#: terms and not one was typed by a person -- every entry is `Generated` (the
#: column's own name with the spaces put back: "location ID" for `LocationID`)
#: or `Suggested` (Power BI's thesaurus, which offers "artifact advertised" for
#: `Product Advertised` and "order" for `Backorder Percentage`).
#:
#: A glossary of business terms containing "artifact advertised" is worse than
#: no glossary, because a reader has no way to tell which entries a person
#: stood behind. So the count is reported and the words are not used, and the
#: day a file turns up with authored synonyms in it this is the note that says
#: what to do about it.
_SYNONYM_GAP = (
    "Q&A synonyms are present and not used as business vocabulary: in the files "
    "read so far every term is machine-generated -- either the object's own name "
    "respaced, or a thesaurus suggestion -- so treating them as words a business "
    "chose would put invented phrases in a glossary"
)

#: What PBIXRay's `rls` frame cannot show, however well it is read. Its query
#: joins TablePermission, so a role that filters no table produces no row at
#: all, and it never selects Role.ModelPermission. Both are stated as gaps
#: whenever RLS is present rather than papered over with a default: a document
#: claiming to list every role, from a source that structurally cannot show
#: the filter-less ones, is exactly the overstatement this project exists to
#: catch.
_RLS_RESIDUAL_GAP = (
    "roles that filter no table, and every role's model-level permission, are "
    "not visible through this file format's reader -- table filters are read "
    "in full, so a role appears here only if it restricts at least one table"
)

#: Tables Power BI writes for itself: a hidden date table per date column, a
#: template for them, and the mapping table the "find clusters" button leaves
#: behind on a scatter chart. None of them carry an author's intent, and all of
#: them would otherwise swamp the documentation.
#:
#: The cluster table is matched by name rather than by anything structural
#: because nothing structural separates it: it is a calculated table with a
#: system flag, and so is the hand-written `Date` table in the same file. What
#: does separate them is in the partition name, which carries a GUID for the
#: generated one -- but that is a second field and a more fragile rule than the
#: name Power BI is documented to use.
_SYSTEM_TABLE = re.compile(
    r"^(DateTableTemplate_|LocalDateTable_)[0-9a-fA-F-]+$"
    r"|^ClusterMappingTable( \d+)?$"
)

#: What a column's `SummarizeBy` means, in Power BI's own numbering. 2 is the
#: one that carries information a name cannot: the author saying "do not
#: summarize" about a column that is numeric and is not a quantity.
_SUMMARIZE_BY = {
    1: "default",
    2: "none",
    3: "sum",
    4: "min",
    5: "max",
    6: "count",
    7: "average",
    8: "distinctcount",
}

#: Where a partition's rows come from, in Power BI's own numbering. 2 is
#: "default", which the engine writes on its own internal storage partitions
#: and which says nothing about a table an author made.
_STORAGE_MODE = {0: "import", 1: "directquery", 3: "dual", 4: "push"}

#: Partition types that belong to a table somebody made: 2 is a calculated
#: table, 4 is one loaded by a query. 3 is the engine's own per-column storage.
_AUTHORED_PARTITION = (2, 4)


#: `ObjectType` for a column in the extended-properties frame, in Power BI's
#: own numbering.
_COLUMN_OBJECT = 4


def _parameter_tables(columns) -> set[str]:
    """Tables that are a what-if parameter rather than data.

    A what-if parameter is a whole table: one column the reader moves and a
    measure reading it. The property that marks it sits on the *column*, so the
    table is named from what its columns are -- and only when every one of them
    is a parameter. A real table that happened to carry a parameter column
    would otherwise drop out of the subject areas entirely, which is a worse
    error than the one this fixes.
    """
    by_table: dict[str, list[bool]] = {}
    for column in columns:
        by_table.setdefault(column.table, []).append(bool(column.is_parameter))
    return {table for table, flags in by_table.items() if flags and all(flags)}


def _grouped_column(value) -> str:
    """The column a `GroupingMetadata` property says its column groups."""
    import json

    try:
        blob = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError):
        return ""
    for entry in blob.get("groupedColumns") or []:
        column = (entry or {}).get("Column") or {}
        name = str(column.get("Property", ""))
        if name:
            return name
    return ""


#: A partition whose rows come from a DAX expression rather than from a query
#: against a source. Power BI's own numbering; 2 is `PartitionType.Calculated`.
_CALCULATED_PARTITION = 2


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
        # A calculated table defines its columns inside its own expression --
        # `ADDCOLUMNS(CALENDAR(...), "Year", YEAR([Date]), ...)` -- so those
        # formulas are read from there and merged in alongside the ones
        # `dax_columns` reports for ordinary tables.
        calc_columns.update(self._columns_defined_by_calculated_tables(raw))

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

        # And a calculated table appears in neither list, for a different
        # reason: it stores no data to enumerate, so `raw.tables` skips it, and
        # it hosts no measures, so the rule above does not catch it either.
        # Microsoft's Store Sales sample loses its whole `Date` table this way
        # -- the one its own field descriptions tell authors to use -- along
        # with both of its drill-down hierarchies, whose eight columns were then
        # reported as unresolved references. The model has them; we were not
        # reading them.
        calculated = self._calculated_tables(raw)
        # Only for tables with no stored columns to enumerate. A calculated
        # table that also stores columns -- Power BI keeps the materialised
        # result for some, and newer readers surface more of them -- already has
        # them read, and a second pass over the expression would list every
        # column twice. Their *formulas* are not skipped with them: those are
        # merged into `calc_columns` above, which is the half of this that went
        # missing when a reader upgrade started returning stored columns for
        # `Date`, silently stripping `YEAR([Date])` and every other formula on
        # it out of the model, the lineage and both documents.
        stored = {c.table.casefold() for c in model.columns}
        model.columns.extend(
            self._calculated_columns(
                {t: e for t, e in calculated.items() if t.casefold() not in stored}
            )
        )

        with_columns = {c.table.casefold() for c in model.columns}
        implied = sorted(t for t in measure_hosts if t.casefold() not in known)

        # A calculated table is only *added* here if neither pass above saw it;
        # one that hosts measures is already in `implied`, and it picks up its
        # expression through `calculated` below either way.
        seen = known | {t.casefold() for t in implied}
        new_tables = sorted(t for t in calculated if t.casefold() not in seen)

        says_table = self._table_declarations(raw)
        storage = self._storage_modes(raw)
        parameter_tables = _parameter_tables(model.columns)
        for name in declared + implied + new_tables:
            query = power_query.get(name)
            says = says_table.get(name.casefold(), {})
            model.tables.append(
                Table(
                    name=name,
                    # Over the load query too, exactly as the TMDL adapter does.
                    # Held to the same rule on purpose: a guarantee that depends
                    # on which file format a model was saved in is not a
                    # guarantee. 15 of 18 tables in Sales_Returns_Sample carry M,
                    # and without this their load queries could be rewritten with
                    # nothing in the model registering a change.
                    # Over whichever definition the table has. A calculated
                    # table's DAX is the only statement of what it contains, so
                    # leaving it out would let its every column be redefined
                    # with nothing in the model registering a change.
                    fingerprint=(
                        fingerprint_parts(name, canonicalise_m(query))
                        if query
                        else fingerprint_parts(name, calculated[name])
                        if name in calculated
                        else fingerprint_text(name)
                    ),
                    is_system=bool(_SYSTEM_TABLE.match(name)),
                    # PBIXRay does not surface column visibility, so every
                    # extracted column counts as visible here.
                    is_measure_only=is_measure_container(
                        has_measures=name in measure_hosts,
                        visible_columns=0 if name.casefold() not in with_columns else 1,
                    ),
                    power_query=power_query.get(name),
                    dax_expression=calculated.get(name),
                    description=says.get("description", ""),
                    is_hidden=says.get("is_hidden", False),
                    data_category=says.get("data_category", ""),
                    is_parameter=name in parameter_tables,
                    storage_mode=storage.get(name.casefold(), ""),
                )
            )
        known_measures = {
            str(r.get("Name", "")).strip().casefold() for r in measure_rows
        }
        says_measure = self._measure_declarations(raw)
        model.measures = [
            self._build_measure(r, known_measures, says_measure) for r in measure_rows
        ]

        model.provenance = self._provenance(raw)
        model.relationships = self._build_relationships(raw)
        model.hierarchies = self._build_hierarchies(raw)
        model.roles = _with_members(
            build_roles(_safe(raw, "rls")),
            members_by_role(_safe(raw, "tmschema_role_memberships")),
        )
        model.calculation_groups = build_calculation_groups(
            _safe(raw, "tmschema_calculation_groups"),
            _safe(raw, "tmschema_calculation_items"),
        )
        model.kpis = build_kpis(_safe(raw, "tmschema_kpis"))
        model.object_permissions = build_object_permissions(_safe(raw, "ols"))
        model.perspectives = build_perspectives(_safe(raw, "perspectives"))
        model.variations = build_variations(
            _safe(raw, "tmschema_variations"), _safe(raw, "tmschema_hierarchies")
        )
        model.report_pages = self._report_pages(path)
        model.report_filters = self._report_filters(path)
        model.coverage_gaps = self._coverage_gaps(raw, model)
        resolve_table_dependencies(model)
        return model

    # -- pieces -------------------------------------------------------------

    def _report_pages(self, path: Path):
        """The report layer, read straight out of the .pbix archive.

        PBIXRay reads the data model and does not expose the report, so this
        opens the file a second time as the zip it is. Failing to read it costs
        the report layer and nothing else: a model whose pages cannot be parsed
        is still a model, and refusing to open it over a bonus would be a poor
        trade.
        """
        import zipfile

        from concordance.normalize.layout import read_report

        try:
            with zipfile.ZipFile(path) as archive:
                return read_report(archive)
        except (KeyError, OSError, zipfile.BadZipFile):
            return []

    def _report_filters(self, path: Path):
        """The report's own filters, in whichever format the report uses.

        The same shape as `_report_pages`: the legacy blob first because it is
        one read, and the newer per-file format otherwise. A file has one or
        the other, never both.

        Reading only the first was a real cost, and a quiet one. Store Sales is
        saved in the newer format, and was reported as applying no filters at
        all while its "New Stores" page is pinned to `Store type is Same
        Store`. Failing costs the filters and nothing else, for the same reason
        `_report_pages` fails softly.
        """
        import zipfile

        from concordance.normalize.filters import read_filters, read_pbir_filters
        from concordance.normalize.layout import _decode

        try:
            with zipfile.ZipFile(path) as archive:
                if "Report/Layout" in archive.namelist():
                    return read_filters(_decode(archive.read("Report/Layout")))
                return read_pbir_filters(archive)
        except (KeyError, OSError, zipfile.BadZipFile):
            return []

    def _columns_defined_by_calculated_tables(
        self, raw: PBIXRay
    ) -> dict[tuple[str, str], str]:
        """Formulas a calculated table states inside its own definition.

        `column_names` already knows how to read them -- this only asks it for
        every calculated table rather than only for the ones that turned out to
        have no stored columns, and keys the answer the way `_build_columns`
        expects so a stored column can be given the formula that produced it.
        """
        found: dict[tuple[str, str], str] = {}
        for table, expression in self._calculated_tables(raw).items():
            for name, body in column_names(expression):
                if body:
                    found[(table, name)] = body
        return found

    def _calculated_tables(self, raw: PBIXRay) -> dict[str, str]:
        """Tables whose rows a DAX expression produces, from their partitions.

        A partition of type 2 is a calculated one, and its query definition is
        DAX rather than M. Every such table is returned, whether or not another
        pass already found it: the expression is worth attaching to a table that
        was picked up as a measure host, because it is the only statement of
        where that table's rows come from.
        """
        out: dict[str, str] = {}
        for row in _rows(_safe(raw, "tmschema_partitions")):
            if int(row.get("Type", 0) or 0) != _CALCULATED_PARTITION:
                continue
            name = str(row.get("TableName", "")).strip()
            expression = str(row.get("QueryDefinition", "") or "").strip()
            if not name or not expression:
                continue
            # Power BI's own hidden date tables are calculated too, and they are
            # exactly the noise `_SYSTEM_TABLE` exists to keep out.
            if _SYSTEM_TABLE.match(name):
                continue
            out.setdefault(name, expression)
        return out

    def _calculated_columns(self, tables: dict[str, str]) -> list[Column]:
        """The columns those expressions state, with the DAX behind each.

        Only what the expression says in so many words. A column that the
        expression computes without naming -- there is no such thing in
        `ADDCOLUMNS`, but a future function could -- would be absent here rather
        than invented, which is the same trade every other reader in this
        project makes.
        """
        out: list[Column] = []
        for table, expression in tables.items():
            for name, body in column_names(expression):
                out.append(
                    Column(
                        table=table,
                        name=name,
                        # Not stated anywhere in the file for a calculated
                        # column: Power BI derives it at refresh from the
                        # expression's result. Claiming one would be inventing
                        # it, and every consumer here treats "" as unknown.
                        data_type="",
                        expression=body,
                        fingerprint=(
                            fingerprint_dax(body)
                            if body
                            else fingerprint_parts(table, name)
                        ),
                    )
                )
        return out

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

    def _coverage_gaps(self, raw: PBIXRay, model=None) -> list[CoverageGap]:
        """Report model features present in the source but not yet extracted."""
        gaps: list[CoverageGap] = []
        model = model if model is not None else SemanticModel("", "", "pbix")
        for attribute, label, minimum in _UNEXTRACTED_FEATURES:
            frame = _safe(raw, attribute)
            if frame is None:
                continue
            try:
                count = len(frame)
            except TypeError:
                continue
            if count >= minimum:
                gaps.append(
                    CoverageGap(
                        feature=label,
                        count=count,
                        reason="present in the model but not yet extracted by this adapter",
                    )
                )

        remote = sorted(
            t.name
            for t in model.tables
            if t.storage_mode in {"directquery", "dual"} and not t.is_system
        )
        if remote:
            gaps.append(
                CoverageGap(
                    feature="tables whose rows this file does not carry",
                    count=len(remote),
                    reason=_REMOTE_TABLE_GAP.format(names=", ".join(remote)),
                )
            )

        for count, feature, reason in (
            (self._synonyms(raw), "Q&A synonyms", _SYNONYM_GAP),
        ):
            if count:
                gaps.append(CoverageGap(feature=feature, count=count, reason=reason))

        translations = _safe(raw, "tmschema_translations")
        try:
            translated = len(translations) if translations is not None else 0
        except TypeError:
            translated = 0
        if translated:
            gaps.append(
                CoverageGap(
                    feature="object name translations",
                    count=translated,
                    reason=_TRANSLATION_GAP,
                )
            )

        # Reported even though RLS *is* extracted now, because what is read is
        # not everything there is. See `_RLS_RESIDUAL_GAP`.
        rls = _safe(raw, "rls")
        try:
            present = len(rls) if rls is not None else 0
        except TypeError:
            present = 0
        if present:
            gaps.append(
                CoverageGap(
                    feature="row-level security role metadata",
                    count=present,
                    reason=_RLS_RESIDUAL_GAP,
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

    def _column_declarations(self, raw: PBIXRay) -> dict[tuple[str, str], dict]:
        """What the model says about each column, beyond its name and type.

        `raw.schema` is the storage schema -- what a column *is made of*.
        `tmschema_columns` is the semantic model's own record of what it *is*:
        the data category, whether the author hid it, how it is formatted, and
        any description they wrote. Only the first was ever read, which is why
        two other parts of this project ended up guessing at answers stated
        here: the map decided a column held a latitude by its name, and the
        chart picker decided one held an image by sniffing its bytes.
        """
        found: dict[tuple[str, str], dict] = {}
        for row in _rows(_safe(raw, "tmschema_columns")):
            table = str(row.get("TableName", "")).strip()
            name = str(row.get("Name", "")).strip()
            if not table or not name:
                continue
            found[(table.casefold(), name.casefold())] = {
                "data_category": _text(row.get("DataCategory")),
                "is_hidden": bool(row.get("IsHidden")),
                "format_string": _text(row.get("FormatString")),
                "description": _text(row.get("Description")),
                "summarize_by": _SUMMARIZE_BY.get(row.get("SummarizeBy"), ""),
                "is_key": bool(row.get("IsKey")),
            }
        return found

    def _measure_declarations(self, raw: PBIXRay) -> dict[tuple[str, str], dict]:
        """How each measure is meant to be rendered, and whether it is hidden.

        This one has to reach past PBIXRay. It surfaces a `tmschema_columns`
        table but no `tmschema_measures`, and its `dax_measures` query selects
        five of the twenty columns the model's own `Measure` table holds --
        `FormatString` and `IsHidden` are not among them. So the tool told
        readers that "Power BI renders a ratio like 0.42 as 42.29% using a
        format string this file does not expose", and that was simply untrue:
        `Net Sales` declares `\\$#,0;-\\$#,0;\\$#,0` and `Net Sales Variance %`
        declares `0.0%;-0.0%;0.0%`, right there in the file, unread.

        Reaching into another library's internals is a cost, so it is paid
        narrowly: one read-only query, every failure swallowed, and a model
        that loses nothing but the formats if a future PBIXRay moves the
        furniture.
        """
        try:
            reader = raw._metadata.source._db  # noqa: SLF001 -- see docstring
            rows = _rows(
                reader.query(
                    "SELECT t.Name AS TableName, m.Name AS Name, "
                    "m.FormatString AS FormatString, m.IsHidden AS IsHidden "
                    "FROM Measure m JOIN [Table] t ON m.TableID = t.ID"
                )
            )
        except Exception:  # noqa: BLE001 -- a private path, so anything at all
            return {}

        found: dict[tuple[str, str], dict] = {}
        for row in rows:
            table = str(row.get("TableName", "")).strip()
            name = str(row.get("Name", "")).strip()
            if not table or not name:
                continue
            found[(table.casefold(), name.casefold())] = {
                "format_string": _text(row.get("FormatString")),
                "is_hidden": bool(row.get("IsHidden")),
            }
        return found

    def _column_properties(self, raw: PBIXRay) -> dict[tuple[str, str], dict]:
        """Extended properties, resolved to the columns they belong to.

        The frame keys each property by an `ObjectID` that means a different
        table depending on `ObjectType`, which is the same shape that makes
        translations unreadable here -- except that for a column the mapping is
        not a guess: the id is `Column.ID`, and this already reaches that table
        to read format strings. So the join is real rather than assumed, and
        two things come out of it.

        `ParameterMetadata` marks a what-if parameter: a control the reader
        moves, which the documents had been listing among the subject areas the
        solution reports on. `GroupingMetadata` marks a column the author made
        by grouping another, and names the one it groups.
        """
        rows = _rows(_safe(raw, "tmschema_extended_properties"))
        wanted = {
            int(row["ObjectID"]): str(row.get("Name", ""))
            for row in rows
            if row.get("ObjectType") == _COLUMN_OBJECT and row.get("ObjectID") is not None
        }
        if not wanted:
            return {}

        try:
            reader = raw._metadata.source._db  # noqa: SLF001 -- see docstring
            columns = _rows(
                reader.query(
                    "SELECT c.ID AS ID, t.Name AS TableName, c.ExplicitName AS Name "
                    "FROM Column c JOIN [Table] t ON c.TableID = t.ID"
                )
            )
        except Exception:  # noqa: BLE001 -- a private path, so anything at all
            return {}

        by_id = {
            int(row["ID"]): (str(row.get("TableName", "")), str(row.get("Name", "")))
            for row in columns
            if row.get("ID") is not None
        }
        grouped = {
            int(row["ObjectID"]): _grouped_column(row.get("Value"))
            for row in rows
            if row.get("Name") == "GroupingMetadata" and row.get("ObjectID") is not None
        }

        found: dict[tuple[str, str], dict] = {}
        for object_id, name in wanted.items():
            where = by_id.get(object_id)
            if where is None:
                continue
            entry = found.setdefault(
                (where[0].casefold(), where[1].casefold()),
                {"is_parameter": False, "grouped_from": ""},
            )
            if name == "ParameterMetadata":
                entry["is_parameter"] = True
            elif name == "GroupingMetadata":
                entry["grouped_from"] = grouped.get(object_id, "")
        return found

    def _provenance(self, raw: PBIXRay) -> dict[str, str]:
        """What the file records about how it was made.

        Three keys in these samples: the Desktop version that wrote it, whether
        time intelligence (and so the hidden date tables) is on, and the order
        the author put their queries in. The last is not read -- the model
        already carries the tables, and their order in a query pane is not a
        fact about the model -- but the first two are provenance a signed
        document should carry.
        """
        wanted = {"PBIDesktopVersion", "__PBI_TimeIntelligenceEnabled", "PBI_ProTooling"}
        found: dict[str, str] = {}
        for row in _rows(_safe(raw, "metadata")):
            name = str(row.get("Name", "")).strip()
            if name in wanted:
                found[name] = _text(row.get("Value"))
        return found

    def _synonyms(self, raw: PBIXRay) -> int:
        """How many Q&A terms the model carries. See `_SYNONYM_GAP`."""
        import json

        rows = _rows(_safe(raw, "tmschema_linguistic_metadata"))
        found = 0
        for row in rows:
            try:
                blob = json.loads(str(row.get("Content") or "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            for entity in (blob.get("Entities") or {}).values():
                for term in (entity or {}).get("Terms") or []:
                    found += len(term or {})
        return found

    def _sort_by_columns(self, raw: PBIXRay) -> dict[tuple[str, str], str]:
        """Which column puts each other column in order, where one is declared.

        `Calendar[Month]` is sorted by `Calendar[MonthSort]`; without reading
        that, `Jan, Feb, Mar` is three strings and sorting them starts at
        April. Same private path as `_measure_declarations`, for the same
        reason and with the same containment: losing it costs the ordering and
        nothing else.
        """
        try:
            reader = raw._metadata.source._db  # noqa: SLF001 -- see docstring
            rows = _rows(
                reader.query(
                    "SELECT t.Name AS TableName, c.ExplicitName AS Name, "
                    "s.ExplicitName AS SortBy "
                    "FROM Column c JOIN [Table] t ON c.TableID = t.ID "
                    "JOIN Column s ON c.SortByColumnID = s.ID"
                )
            )
        except Exception:  # noqa: BLE001 -- a private path, so anything at all
            return {}

        found: dict[tuple[str, str], str] = {}
        for row in rows:
            table = str(row.get("TableName", "")).strip()
            name = str(row.get("Name", "")).strip()
            sorts_by = str(row.get("SortBy", "")).strip()
            if not table or not name or not sorts_by or sorts_by == name:
                continue
            found[(table.casefold(), name.casefold())] = sorts_by
        return found

    def _storage_modes(self, raw: PBIXRay) -> dict[str, str]:
        """Where each table's rows live. See `Table.storage_mode`."""
        found: dict[str, str] = {}
        for row in _rows(_safe(raw, "tmschema_partitions")):
            if row.get("Type") not in _AUTHORED_PARTITION:
                continue
            name = str(row.get("TableName", "")).strip()
            mode = _STORAGE_MODE.get(row.get("Mode"), "")
            if name and mode:
                found[name.casefold()] = mode
        return found

    def _table_declarations(self, raw: PBIXRay) -> dict[str, dict]:
        """What the author said about each table, in their own words.

        A description written by the person who built the table beats anything
        this tool can infer about what it is for, and `tmschema_tables` has
        been carrying them unread.
        """
        found: dict[str, dict] = {}
        for row in _rows(_safe(raw, "tmschema_tables")):
            name = str(row.get("Name", "")).strip()
            if not name:
                continue
            found[name.casefold()] = {
                "description": _text(row.get("Description")),
                "is_hidden": bool(row.get("IsHidden")),
                "data_category": _text(row.get("DataCategory")),
            }
        return found

    def _build_columns(
        self, raw: PBIXRay, calc: dict[tuple[str, str], str]
    ) -> list[Column]:
        columns: list[Column] = []
        seen: set[tuple[str, str]] = set()
        declared = self._column_declarations(raw)
        sorts_by = self._sort_by_columns(raw)
        properties = self._column_properties(raw)

        for row in _rows(raw.schema):
            table = str(row.get("TableName", "")).strip()
            name = str(row.get("ColumnName", "")).strip()
            if not table or not name:
                continue
            seen.add((table, name))
            expr = calc.get((table, name))
            says = declared.get((table.casefold(), name.casefold()), {})
            extra = properties.get((table.casefold(), name.casefold()), {})
            columns.append(
                Column(
                    table=table,
                    name=name,
                    data_type=str(row.get("PandasDataType", "unknown")),
                    expression=expr,
                    data_category=says.get("data_category", ""),
                    is_hidden=says.get("is_hidden", False),
                    format_string=says.get("format_string", ""),
                    description=says.get("description", ""),
                    sort_by=sorts_by.get((table.casefold(), name.casefold()), ""),
                    summarize_by=says.get("summarize_by", ""),
                    is_key=says.get("is_key", False),
                    is_parameter=extra.get("is_parameter", False),
                    grouped_from=extra.get("grouped_from", ""),
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
                says = declared.get((table.casefold(), name.casefold()), {})
                columns.append(
                    Column(
                        table=table,
                        name=name,
                        data_type="calculated",
                        expression=expr,
                        data_category=says.get("data_category", ""),
                        is_hidden=says.get("is_hidden", False),
                        format_string=says.get("format_string", ""),
                        description=says.get("description", ""),
                        fingerprint=fingerprint_dax(expr),
                    )
                )

        return columns

    def _build_measure(
        self,
        row: dict,
        known_measures: set[str],
        declared: dict[tuple[str, str], dict] | None = None,
    ) -> Measure:
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

        says = (declared or {}).get((table.casefold(), name.casefold()), {})
        return Measure(
            table=table,
            name=name,
            expression=expression,
            fingerprint=fingerprint_dax(expression),
            display_folder=_optional(row.get("DisplayFolder")),
            description=_optional(row.get("Description")),
            format_string=says.get("format_string", ""),
            is_hidden=says.get("is_hidden", False),
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


def build_roles(frame) -> list[SecurityRole]:
    """Security roles from PBIXRay's ``rls`` frame.

    One row per (role, table) permission -- columns ``RoleName``,
    ``RoleDescription``, ``TableName``, ``FilterExpression`` -- so rows are
    grouped back into a role here. Taken from the query in PBIXRay's own
    source rather than inferred from a sample, which is what makes this
    extraction rather than guesswork.

    A module-level function, not a method, so it can be exercised against
    frames of the documented shape without constructing a PBIXRay -- none of
    the sample .pbix files in this repository define any roles.
    """
    permissions: dict[str, list[TablePermission]] = {}
    descriptions: dict[str, str | None] = {}

    for row in _rows(frame):
        role = str(row.get("RoleName", "") or "").strip()
        table = str(row.get("TableName", "") or "").strip()
        if not role or not table:
            continue
        expression = str(row.get("FilterExpression", "") or "").strip()
        descriptions.setdefault(role, _text_or_none(row.get("RoleDescription")))
        permissions.setdefault(role, []).append(
            TablePermission(
                table=table,
                expression=expression,
                fingerprint=(
                    fingerprint_dax(expression)
                    if expression
                    else fingerprint_parts(role, table)
                ),
            )
        )

    return [
        SecurityRole(
            name=role,
            # PBIXRay's query never selects Role.ModelPermission, so this is
            # not read from the file. Reported as a coverage gap rather than
            # presented as though it had been -- see `_RLS_RESIDUAL_GAP`.
            model_permission="",
            permissions=tuple(entries),
            fingerprint=fingerprint_parts(role, *(p.fingerprint for p in entries)),
            description=descriptions.get(role),
        )
        for role, entries in sorted(permissions.items())
    ]


def build_calculation_groups(groups_frame, items_frame) -> list[CalculationGroup]:
    """Calculation groups and their items, joined on the table they sit on.

    PBIXRay exposes the group (``TableName``, ``Precedence``, ``Description``)
    and its items (``TableName``, ``Name``, ``Expression``, ``Ordinal``) as two
    frames. Items carry ``TableName`` themselves, so the join is by that rather
    than by CalculationGroupID -- the id is an internal key with no meaning
    outside the file, and the name is what everything downstream addresses.
    """
    items: dict[str, list[CalculationItem]] = {}
    for row in _rows(items_frame):
        table = str(row.get("TableName", "") or "").strip()
        name = str(row.get("Name", "") or "").strip()
        if not table or not name:
            continue
        expression = str(row.get("Expression", "") or "").strip()
        items.setdefault(table, []).append(
            CalculationItem(
                name=name,
                expression=expression,
                fingerprint=(
                    fingerprint_dax(expression)
                    if expression
                    else fingerprint_parts(table, name)
                ),
                ordinal=int(row.get("Ordinal", 0) or 0),
                # Only an id for the format definition is exposed, never the
                # expression itself, so there is nothing honest to put here.
                format_expression=None,
            )
        )
    for entries in items.values():
        entries.sort(key=lambda item: (item.ordinal, item.name))

    out: list[CalculationGroup] = []
    seen: set[str] = set()
    for row in _rows(groups_frame):
        table = str(row.get("TableName", "") or "").strip()
        if not table or table in seen:
            continue
        seen.add(table)
        entries = tuple(items.get(table, ()))
        out.append(
            CalculationGroup(
                table=table,
                items=entries,
                fingerprint=fingerprint_parts(
                    table, *(item.fingerprint for item in entries)
                ),
                precedence=_int_or_none(row.get("Precedence")),
                description=_text_or_none(row.get("Description")),
            )
        )
    return out


def build_variations(frame, hierarchies_frame=None) -> list[ColumnVariation]:
    """Column variations from PBIXRay's ``tmschema_variations`` frame.

    Columns ``TableName``, ``ColumnName``, ``Name``, ``DefaultHierarchyID``,
    ``IsDefault``. The hierarchy id is resolved against
    ``tmschema_hierarchies`` when that frame is supplied; unresolved, it is
    left as None rather than reported as a numeric id nobody can act on.
    """
    by_id: dict[str, str] = {}
    for row in _rows(hierarchies_frame):
        key = str(row.get("ID", "") or "").strip()
        table = str(row.get("TableName", "") or "").strip()
        name = str(row.get("Name", "") or "").strip()
        if key and name:
            by_id[key] = f"{table}[{name}]" if table else name

    out: list[ColumnVariation] = []
    for row in _rows(frame):
        table = str(row.get("TableName", "") or "").strip()
        column = str(row.get("ColumnName", "") or "").strip()
        if not table or not column:
            continue
        hierarchy_key = str(row.get("DefaultHierarchyID", "") or "").strip()
        out.append(
            ColumnVariation(
                table=table,
                column=column,
                name=str(row.get("Name", "") or "").strip() or "Variation",
                default_hierarchy=by_id.get(hierarchy_key),
                is_default=bool(int(row.get("IsDefault", 0) or 0)),
            )
        )
    return out


def members_by_role(frame) -> dict[str, tuple[str, ...]]:
    """Role membership from ``tmschema_role_memberships``.

    Columns ``RoleName`` and ``MemberName``. Worth reading for its own sake --
    "who is in this role" is half of what an access review asks -- and it also
    reaches roles the `rls` frame cannot: that query joins TablePermission, so
    a role filtering no table has no row there, while a role with members has
    one here.
    """
    out: dict[str, list[str]] = {}
    for row in _rows(frame):
        role = str(row.get("RoleName", "") or "").strip()
        member = str(row.get("MemberName", "") or "").strip()
        if role and member:
            out.setdefault(role, []).append(member)
    return {role: tuple(sorted(set(names))) for role, names in out.items()}


def build_kpis(frame) -> list[Kpi]:
    """KPIs from PBIXRay's ``tmschema_kpis`` frame.

    Columns come from that query: ``MeasureName``, ``TableName``,
    ``TargetExpression``, ``StatusExpression``, ``TrendExpression`` and their
    descriptions. Fingerprinted over the three expressions together, because
    all three decide what the report calls good and none of them appear in the
    measure's own DAX.
    """
    out: list[Kpi] = []
    for row in _rows(frame):
        table = str(row.get("TableName", "") or "").strip()
        measure = str(row.get("MeasureName", "") or "").strip()
        if not table or not measure:
            continue
        target = str(row.get("TargetExpression", "") or "").strip()
        status = str(row.get("StatusExpression", "") or "").strip()
        trend = str(row.get("TrendExpression", "") or "").strip()
        out.append(
            Kpi(
                table=table,
                measure=measure,
                target_expression=target,
                status_expression=status,
                trend_expression=trend,
                fingerprint=fingerprint_parts(table, measure, target, status, trend),
                description=_text_or_none(row.get("Description")),
                target_description=_text_or_none(row.get("TargetDescription")),
                status_description=_text_or_none(row.get("StatusDescription")),
            )
        )
    return out


def build_object_permissions(frame) -> list[ObjectPermission]:
    """Object-level security from PBIXRay's ``ols`` frame.

    Columns ``RoleName``, ``TableName``, ``ColumnName``, ``Scope``,
    ``Permission``. Rows whose permission is ``Default`` are dropped: that
    value means "no OLS restriction", and it is the ordinary row-level case
    already reported through `rls`. Keeping them would list every RLS role as
    though it hid something.
    """
    out: list[ObjectPermission] = []
    for row in _rows(frame):
        role = str(row.get("RoleName", "") or "").strip()
        table = str(row.get("TableName", "") or "").strip()
        permission = str(row.get("Permission", "") or "").strip()
        if not role or not table or not permission:
            continue
        if permission.casefold() == "default":
            continue
        out.append(
            ObjectPermission(
                role=role,
                table=table,
                column=_text_or_none(row.get("ColumnName")),
                permission=permission,
            )
        )
    return out


def build_perspectives(frame) -> list[Perspective]:
    """Perspectives from PBIXRay's consolidated ``perspectives`` frame.

    One row per included object -- ``PerspectiveName``, ``ObjectType``,
    ``TableName``, ``ObjectName`` -- rolled back up into one entry per
    perspective here.
    """
    members: dict[str, list[PerspectiveMember]] = {}
    for row in _rows(frame):
        name = str(row.get("PerspectiveName", "") or "").strip()
        if not name:
            continue
        members.setdefault(name, []).append(
            PerspectiveMember(
                object_kind=str(row.get("ObjectType", "") or "").strip() or "Object",
                table=str(row.get("TableName", "") or "").strip(),
                name=str(row.get("ObjectName", "") or "").strip(),
            )
        )

    return [
        Perspective(
            name=name,
            members=tuple(entries),
            fingerprint=fingerprint_parts(
                name,
                *(f"{m.object_kind}:{m.table}:{m.name}" for m in entries),
            ),
        )
        for name, entries in sorted(members.items())
    ]


def _with_members(
    roles: list[SecurityRole], members: dict[str, tuple[str, ...]]
) -> list[SecurityRole]:
    """Attach membership, and surface roles the `rls` frame could not show.

    A role that filters no table has no row in `rls` at all. If it has
    members, it has a row here -- so this is the one place such a role becomes
    visible, and it is added rather than dropped.
    """
    from dataclasses import replace

    out = [replace(role, members=members.get(role.name, ())) for role in roles]
    known = {role.name for role in roles}
    for name in sorted(set(members) - known):
        out.append(
            SecurityRole(
                name=name,
                model_permission="",
                permissions=(),
                fingerprint=fingerprint_parts("role", name),
                members=members[name],
            )
        )
    return out


def _text_or_none(value) -> str | None:
    """A frame cell as text, treating pandas' NaN and empty alike as absent."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value) -> int | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _text(value) -> str:
    """One cell as a stripped string, with pandas' many spellings of nothing
    all coming out as the empty string."""
    if not _present(value):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in ("none", "nan", "<na>") else text


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
