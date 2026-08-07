"""TMDL adapter -- Power BI's text format for semantic models.

TMDL is what Power BI writes when a model is saved as a `.pbip` project, and
unlike `.pbix` it is plain text: diffable, reviewable, and editable. That makes
it the format in which a genuine "version 2" of a model can be authored, which
is what drift detection needs in order to be demonstrated on a real change
rather than a simulated one.

The grammar implemented here follows Microsoft's TMDL specification:

* objects are declared as ``<type> <name>``, names with spaces or punctuation
  wrapped in single quotes, an embedded quote escaped by doubling;
* structure comes from indentation alone -- children are indented one level
  deeper than their parent, with no closing delimiters;
* ``:`` assigns an ordinary property, ``=`` assigns an expression, and a
  property name on its own is a boolean set to true;
* ``///`` above a declaration is its description;
* an expression may continue over several lines, indented past its parent, and
  a triple-backtick block preserves whitespace literally.

Only the subset that carries semantic meaning is interpreted -- tables,
columns, measures, hierarchies, levels and relationships. Presentation and
lineage properties are read but not turned into graph objects, and anything the
grammar does not recognise is skipped rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from concordance.adapters.base import resolve_table_dependencies
from concordance.fingerprint import fingerprint_dax, fingerprint_parts, fingerprint_text
from concordance.model import (
    Column,
    Hierarchy,
    HierarchyLevel,
    Measure,
    Relationship,
    SemanticModel,
    Table,
)
from concordance.normalize.dax import extract_references

_FENCE = "```"


@dataclass
class Node:
    """One declaration and everything nested beneath it."""

    kind: str
    name: str
    #: Value assigned on the declaration line itself (``partition X = m``).
    inline_value: str = ""
    #: Expression assigned with ``=``, possibly spanning many lines.
    expression: str | None = None
    description: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)

    def child(self, kind: str) -> list["Node"]:
        return [c for c in self.children if c.kind == kind]


def parse(text: str) -> list[Node]:
    """Parse one TMDL document into its top-level declarations."""
    return _Parser(text).parse()


class _Parser:
    def __init__(self, text: str) -> None:
        # Tabs are the specified indent, but files edited by hand often arrive
        # with spaces; normalising to spaces lets both parse identically.
        self.lines = text.replace("\t", "    ").splitlines()
        self.pos = 0

    def parse(self) -> list[Node]:
        return self._block(indent=0)

    # -- helpers ---------------------------------------------------------

    def _indent_of(self, line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _peek(self) -> str | None:
        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            if line.strip():
                return line
            self.pos += 1
        return None

    # -- grammar ---------------------------------------------------------

    def _block(self, indent: int) -> list[Node]:
        """Read every declaration at ``indent`` until the block ends."""
        nodes: list[Node] = []
        pending_description: list[str] = []

        while True:
            line = self._peek()
            if line is None:
                break

            current = self._indent_of(line)
            if current < indent:
                break

            stripped = line.strip()

            if stripped.startswith("///"):
                pending_description.append(stripped[3:].strip())
                self.pos += 1
                continue

            # `ref table X` records ordering; it declares nothing new.
            if stripped.startswith("ref "):
                self.pos += 1
                continue

            node = self._declaration(stripped, current)
            if node is None:
                self.pos += 1
                continue

            if pending_description:
                node.description = " ".join(pending_description)
                pending_description = []
            nodes.append(node)

        return nodes

    def _declaration(self, stripped: str, indent: int) -> Node | None:
        parts = stripped.split(None, 1)
        if not parts:
            return None

        kind = parts[0]
        remainder = parts[1] if len(parts) > 1 else ""
        self.pos += 1

        inline_value = ""
        expression: str | None = None

        if "=" in remainder:
            name_part, _, value_part = remainder.partition("=")
            name = _unquote(name_part.strip())
            value = value_part.strip()

            if value.startswith(_FENCE):
                expression = self._fenced_block()
            elif value:
                # A short value such as `partition X = m` is a mode, not an
                # expression; only the multi-token case is DAX.
                inline_value = value
                expression = value
            else:
                expression = self._indented_expression(indent)
        else:
            name = _unquote(remainder.strip())

        node = Node(kind=kind, name=name, inline_value=inline_value, expression=expression)
        self._body(node, indent)
        return node

    def _body(self, node: Node, indent: int) -> None:
        """Read properties and nested declarations belonging to ``node``."""
        while True:
            line = self._peek()
            if line is None:
                return

            current = self._indent_of(line)
            if current <= indent:
                return

            stripped = line.strip()

            if stripped.startswith("///"):
                # A description belongs to the *next* declaration, so hand the
                # remainder of this block to the block reader.
                node.children.extend(self._block(indent=current))
                return

            if stripped.startswith(_FENCE):
                node.expression = self._fenced_block()
                continue

            key, sep, value = stripped.partition(":")
            if sep and _looks_like_property(key):
                node.properties[key.strip()] = value.strip()
                self.pos += 1
                continue

            # A property may also take an expression: a partition's `source =`
            # holds the M query. Without this it parses as a nested declaration
            # and the query becomes unreachable.
            key, sep, value = stripped.partition("=")
            token = key.strip()
            if sep and token and " " not in token and token not in _DECLARATION_KEYWORDS:
                value = value.strip()
                if value.startswith(_FENCE):
                    node.properties[token] = self._fenced_block()
                elif value:
                    node.properties[token] = value
                    self.pos += 1
                else:
                    self.pos += 1
                    node.properties[token] = self._indented_expression(current)
                continue

            if "=" in stripped or _looks_like_declaration(stripped):
                node.children.extend(self._block(indent=current))
                return

            # A bare word is a boolean property set to true.
            if stripped.isidentifier():
                node.properties[stripped] = "true"
                self.pos += 1
                continue

            self.pos += 1

    def _indented_expression(self, indent: int) -> str:
        """Collect an expression written across the lines below its declaration."""
        collected: list[str] = []
        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            if not line.strip():
                self.pos += 1
                continue
            if self._indent_of(line) <= indent:
                break
            stripped = line.strip()
            # A property line ends the expression.
            key, sep, _ = stripped.partition(":")
            if sep and _looks_like_property(key) and collected:
                break
            if stripped.startswith(_FENCE):
                return self._fenced_block()
            collected.append(stripped)
            self.pos += 1
        return "\n".join(collected)

    def _fenced_block(self) -> str:
        """Read a ``` block, preserving its contents literally."""
        line = self.lines[self.pos].strip()
        # The opening fence may sit on the assignment line.
        if line.endswith(_FENCE) and line != _FENCE and not line.startswith(_FENCE):
            self.pos += 1
        elif line.startswith(_FENCE):
            self.pos += 1

        collected: list[str] = []
        while self.pos < len(self.lines):
            current = self.lines[self.pos]
            if current.strip() == _FENCE:
                self.pos += 1
                break
            collected.append(current.strip())
            self.pos += 1
        return "\n".join(collected)


def _unquote(name: str) -> str:
    name = name.strip()
    if name.startswith("'") and name.endswith("'") and len(name) >= 2:
        return name[1:-1].replace("''", "'")
    return name


#: Properties are camelCase words; a declaration keyword is followed by a name.
_DECLARATION_KEYWORDS = {
    "table", "column", "measure", "hierarchy", "level", "partition",
    "relationship", "model", "database", "role", "perspective", "culture",
    "annotation", "changedProperty", "calculationGroup", "calculationItem",
    "extendedProperty", "variation",
}


def _looks_like_property(key: str) -> bool:
    """Distinguish ``column: Year`` (a property) from ``column Year`` (a declaration).

    The colon is the only reliable discriminator, and it has already been found
    by the caller's partition. A keyword may legitimately name a property --
    a hierarchy level's target column is written ``column: Year`` -- so the word
    itself must not disqualify it. What rules a line out is whitespace before
    the colon, which means the colon belongs to an expression rather than to a
    property name.
    """
    key = key.strip()
    return bool(key) and " " not in key


def _looks_like_declaration(stripped: str) -> bool:
    first = stripped.split(None, 1)[0] if stripped.split() else ""
    return first in _DECLARATION_KEYWORDS


class TmdlAdapter:
    """Extracts a semantic model from a TMDL definition folder."""

    source_type = "tmdl"

    def extract(self, source: str) -> SemanticModel:
        root = Path(source)
        if not root.exists():
            raise FileNotFoundError(f"no such TMDL model: {source}")

        definition = self._definition_dir(root)
        if definition is None:
            raise ValueError(
                f"{source} does not look like a TMDL model: no definition/ folder "
                f"containing .tmdl files was found"
            )

        model = SemanticModel(
            name=self._model_name(root),
            source_path=str(root),
            source_type=self.source_type,
        )

        table_dir = definition / "tables"
        table_files = (
            sorted(table_dir.glob("*.tmdl")) if table_dir.is_dir() else []
        )
        for path in table_files:
            self._read_table(path, model)

        relationships = definition / "relationships.tmdl"
        if relationships.is_file():
            self._read_relationships(relationships, model)

        resolve_table_dependencies(model)
        return model

    # -- pieces -----------------------------------------------------------

    def _definition_dir(self, root: Path) -> Path | None:
        for candidate in (root / "definition", root):
            if candidate.is_dir() and any(candidate.rglob("*.tmdl")):
                return candidate
        # A .pbip project points at a sibling .SemanticModel folder.
        for child in sorted(root.glob("*.SemanticModel")):
            found = self._definition_dir(child)
            if found is not None:
                return found
        return None

    def _model_name(self, root: Path) -> str:
        name = root.name
        for suffix in (".SemanticModel", ".Dataset"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    def _read_table(self, path: Path, model: SemanticModel) -> None:
        for node in parse(path.read_text(encoding="utf-8")):
            if node.kind != "table":
                continue

            columns = node.child("column")
            measures = node.child("measure")

            model.tables.append(
                Table(
                    name=node.name,
                    fingerprint=fingerprint_text(node.name),
                    is_system=False,  # TMDL models do not carry auto date tables
                    is_measure_only=not columns
                    or all(c.properties.get("isHidden") for c in columns) and bool(measures),
                    power_query=self._partition_source(node),
                )
            )

            for column in columns:
                expression = column.expression
                model.columns.append(
                    Column(
                        table=node.name,
                        name=column.name,
                        data_type=column.properties.get("dataType", "unknown"),
                        expression=expression,
                        fingerprint=(
                            fingerprint_dax(expression)
                            if expression
                            else fingerprint_parts(
                                node.name,
                                column.name,
                                column.properties.get("dataType", "unknown"),
                            )
                        ),
                    )
                )

            known = {m.name.casefold() for m in measures}
            for measure in measures:
                model.measures.append(
                    self._build_measure(node.name, measure, known)
                )

            for hierarchy in node.child("hierarchy"):
                model.hierarchies.append(self._build_hierarchy(node.name, hierarchy))

    def _partition_source(self, table: Node) -> str | None:
        for partition in table.child("partition"):
            source = partition.properties.get("source") or partition.expression
            if source and source not in {"m", "calculated", "entity"}:
                return source
        return None

    def _build_measure(self, table: str, node: Node, known: set[str]) -> Measure:
        expression = (node.expression or "").strip()
        refs = extract_references(expression)

        measures = {r for r in refs.unqualified if r.casefold() in known}
        columns = {
            (table, r) for r in refs.unqualified if r.casefold() not in known
        }
        for ref_table, ref_name in refs.columns:
            if ref_name.casefold() in known:
                measures.add(ref_name)
            else:
                columns.add((ref_table, ref_name))

        return Measure(
            table=table,
            name=node.name,
            expression=expression,
            fingerprint=fingerprint_dax(expression),
            display_folder=node.properties.get("displayFolder"),
            description=node.description,
            depends_on_columns=frozenset(columns),
            depends_on_measures=frozenset(measures),
        )

    def _build_hierarchy(self, table: str, node: Node) -> Hierarchy:
        levels: list[HierarchyLevel] = []
        for ordinal, level in enumerate(node.child("level")):
            levels.append(
                HierarchyLevel(
                    ordinal=ordinal,
                    name=level.name,
                    column=_unquote(level.properties.get("column", "")),
                )
            )

        return Hierarchy(
            table=table,
            name=node.name,
            levels=tuple(levels),
            fingerprint=fingerprint_parts(
                table,
                node.name,
                *(f"{lv.ordinal}:{lv.name}:{lv.column}" for lv in levels),
            ),
            is_hidden=node.properties.get("isHidden") == "true",
            display_folder=node.properties.get("displayFolder"),
            description=node.description,
        )

    def _read_relationships(self, path: Path, model: SemanticModel) -> None:
        for node in parse(path.read_text(encoding="utf-8")):
            if node.kind != "relationship":
                continue

            from_table, from_column = _split_reference(node.properties.get("fromColumn", ""))
            to_table, to_column = _split_reference(node.properties.get("toColumn", ""))
            if not from_table or not to_table:
                continue

            # TMDL records the *to* side; "one" is the many-to-one default that
            # Power BI shows as M:1.
            to_cardinality = node.properties.get("toCardinality", "one")
            from_cardinality = node.properties.get("fromCardinality", "many")
            cardinality = f"{'M' if from_cardinality == 'many' else '1'}:" \
                          f"{'1' if to_cardinality == 'one' else 'M'}"

            cross_filter = node.properties.get("crossFilteringBehavior", "singleDirection")
            cross_filter = "Both" if cross_filter == "bothDirections" else "Single"
            is_active = node.properties.get("isActive", "true") != "false"

            model.relationships.append(
                Relationship(
                    from_table=from_table,
                    from_column=from_column,
                    to_table=to_table,
                    to_column=to_column,
                    cardinality=cardinality,
                    cross_filter=cross_filter,
                    is_active=is_active,
                    fingerprint=fingerprint_parts(
                        from_table, from_column, to_table, to_column,
                        cardinality, cross_filter, str(is_active),
                    ),
                )
            )


def _split_reference(reference: str) -> tuple[str, str]:
    """Split ``Table.Column`` or ``Table.'Column Name'`` into its two parts."""
    reference = reference.strip()
    if not reference:
        return "", ""

    if reference.startswith("'"):
        closing = reference.find("'", 1)
        while closing != -1 and closing + 1 < len(reference) and reference[closing + 1] == "'":
            closing = reference.find("'", closing + 2)
        if closing == -1:
            return _unquote(reference), ""
        table = _unquote(reference[: closing + 1])
        rest = reference[closing + 1 :].lstrip(".")
        return table, _unquote(rest)

    table, _, column = reference.partition(".")
    return _unquote(table), _unquote(column)
