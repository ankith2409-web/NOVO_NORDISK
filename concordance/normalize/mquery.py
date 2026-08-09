"""Where a table's data actually comes from, read out of its Power Query (M).

Lineage used to stop at the Power BI table. A reviewer could trace a number back
through the measures that compose it, down to the column it reads, and there the
chain ended -- with no way to see that ``AdverseEvent`` is a CSV, or which
warehouse a fact table is loaded from. For a tool whose subject is the seam
between a report and the platforms beneath it, stopping exactly at that seam was
the wrong place to stop.

This is not an M interpreter and does not try to be. M is a full functional
language with lazy evaluation, and running it is neither possible here nor
necessary: the question is only "which external systems does this query name",
which is answered by the literal arguments to a connector function. What this
module does is lex M properly and read those literals out.

Lexing rather than pattern-matching, for the same reason the DAX module lexes:
a Windows path in a string is full of backslashes, an M string escapes a quote
by doubling it, and comments can contain anything at all. Text matching gets
each of those wrong in a way that produces a confident, incorrect source.

Honest about its limits by construction. A query whose source is computed rather
than written -- a path assembled from a parameter, a connector this module does
not know -- yields no reference rather than a guessed one, and the caller can
see the difference between "reads nothing external" and "could not tell".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(Enum):
    STRING = "string"        # "..." with "" as an escaped quote
    IDENT = "ident"          # Foo.Bar, or #"quoted identifier"
    NUMBER = "number"
    OP = "op"
    COMMENT = "comment"
    WS = "ws"


@dataclass(frozen=True)
class Token:
    kind: Kind
    value: str  # inner text for strings and quoted identifiers, raw otherwise
    raw: str


def lex(text: str) -> list[Token]:
    """Turn M source into tokens.

    Only as much of the grammar as the question needs: strings, identifiers
    (including ``#"quoted"`` form), numbers, comments and everything else as
    single-character operators. That is enough to find a function call and read
    its literal arguments, and pretending to more would be a claim this module
    does not have to make.
    """
    tokens: list[Token] = []
    i = 0
    length = len(text)

    while i < length:
        char = text[i]

        # -- whitespace ------------------------------------------------------
        if char.isspace():
            start = i
            while i < length and text[i].isspace():
                i += 1
            tokens.append(Token(Kind.WS, " ", text[start:i]))
            continue

        # -- comments --------------------------------------------------------
        # Checked before operators: `//` and `/*` both start with a character
        # that is otherwise a division operator.
        if char == "/" and i + 1 < length and text[i + 1] == "/":
            start = i
            while i < length and text[i] != "\n":
                i += 1
            tokens.append(Token(Kind.COMMENT, text[start:i], text[start:i]))
            continue

        if char == "/" and i + 1 < length and text[i + 1] == "*":
            start = i
            i += 2
            while i + 1 < length and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, length)
            tokens.append(Token(Kind.COMMENT, text[start:i], text[start:i]))
            continue

        # -- strings ---------------------------------------------------------
        # A doubled quote inside a string is an escaped quote, not the end of
        # it. Getting this wrong truncates a path at its first quote and turns
        # the rest of the query into stray tokens.
        if char == '"':
            start = i
            i += 1
            inner: list[str] = []
            while i < length:
                if text[i] == '"':
                    if i + 1 < length and text[i + 1] == '"':
                        inner.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                inner.append(text[i])
                i += 1
            tokens.append(Token(Kind.STRING, "".join(inner), text[start:i]))
            continue

        # -- quoted identifiers ----------------------------------------------
        # `#"Changed Type"` is one identifier. `#date`, `#duration` and friends
        # are ordinary keyword identifiers and fall through to the branch below.
        if char == "#" and i + 1 < length and text[i + 1] == '"':
            start = i
            i += 2
            inner = []
            while i < length:
                if text[i] == '"':
                    if i + 1 < length and text[i + 1] == '"':
                        inner.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                inner.append(text[i])
                i += 1
            tokens.append(Token(Kind.IDENT, "".join(inner), text[start:i]))
            continue

        # -- identifiers -----------------------------------------------------
        # Dots are part of the name: `Csv.Document` is one identifier, and
        # splitting it would lose the very thing that names the connector.
        if char.isalpha() or char == "_" or char == "#":
            start = i
            i += 1
            while i < length and (text[i].isalnum() or text[i] in "._"):
                i += 1
            raw = text[start:i]
            tokens.append(Token(Kind.IDENT, raw, raw))
            continue

        # -- numbers ---------------------------------------------------------
        if char.isdigit():
            start = i
            while i < length and (text[i].isdigit() or text[i] == "."):
                i += 1
            raw = text[start:i]
            tokens.append(Token(Kind.NUMBER, raw, raw))
            continue

        tokens.append(Token(Kind.OP, char, char))
        i += 1

    return tokens


@dataclass(frozen=True)
class SourceReference:
    """One external system a query names.

    ``detail`` is the literal as written, not normalised: a file path, a server
    name, a URL. Cleaning it up would only make two references that differ look
    identical, and the point of recording it is to tell them apart.
    """

    #: Coarse category, for grouping and for the graph node's label.
    kind: str
    #: The literal that identifies this particular source.
    detail: str
    #: The M function it was read from, so the claim can be checked.
    function: str

    @property
    def label(self) -> str:
        """Short enough for a lineage node, still identifying."""
        if self.kind == "file":
            # The tail of a path is what a person recognises; the directory is
            # usually one developer's home folder and says nothing useful.
            tail = self.detail.replace("\\", "/").rstrip("/").split("/")[-1]
            return tail or self.detail
        return self.detail


#: Connector functions whose *first* string literal names the source, mapped to
#: the category it belongs to. Deliberately a fixed list rather than a guess at
#: anything shaped like `X.Database`: a wrong source in a lineage diagram is
#: worse than an absent one, because it is believed.
_CONNECTORS: dict[str, str] = {
    # Files. The format wrappers (Csv.Document, Excel.Workbook, Json.Document)
    # take the *result* of File.Contents rather than a path, so it is
    # File.Contents that carries the literal and the only one listed.
    "File.Contents": "file",
    "Folder.Files": "folder",
    "Folder.Contents": "folder",
    # Relational databases.
    "Sql.Database": "sql server",
    "Sql.Databases": "sql server",
    "PostgreSQL.Database": "postgresql",
    "MySQL.Database": "mysql",
    "Oracle.Database": "oracle",
    "Teradata.Database": "teradata",
    "Db2.Database": "db2",
    "Odbc.DataSource": "odbc",
    "Odbc.Query": "odbc",
    "OleDb.DataSource": "oledb",
    # The platforms this project is asked about by name.
    "Snowflake.Databases": "snowflake",
    "Databricks.Catalogs": "databricks",
    "Databricks.Query": "databricks",
    "AmazonRedshift.Database": "redshift",
    "AmazonAthena.Database": "athena",
    "AzureStorage.Blobs": "azure blob storage",
    "AzureStorage.DataLake": "azure data lake",
    "DataLake.Contents": "azure data lake",
    "Synapse.Database": "synapse",
    "GoogleBigQuery.Database": "bigquery",
    # Services.
    "Web.Contents": "web",
    "OData.Feed": "odata",
    "SharePoint.Files": "sharepoint",
    "SharePoint.Tables": "sharepoint",
    "SharePoint.Contents": "sharepoint",
    "Salesforce.Data": "salesforce",
    "Salesforce.Reports": "salesforce",
}

#: Functions that build a table from values written into the query itself.
#: Recorded so "no external source" is a statement rather than a silence -- a
#: table of constants is a real answer to "where does this come from".
_INLINE = {
    "Table.FromRows",
    "Table.FromRecords",
    "Table.FromColumns",
    "#table",
    "Json.Document",
    "Binary.FromText",
}


def extract_sources(m: str) -> tuple[SourceReference, ...]:
    """The external systems this query names, in the order they appear.

    Returns an empty tuple when nothing recognisable is named, which the caller
    must not read as "reads nothing": a computed path, a parameter, or a
    connector absent from the list above all land here too. That distinction is
    the caller's to report, and the reason this function does not invent a
    placeholder to fill the gap.
    """
    tokens = [t for t in lex(m) if t.kind not in (Kind.WS, Kind.COMMENT)]
    found: list[SourceReference] = []
    seen: set[tuple[str, str]] = set()

    for index, token in enumerate(tokens):
        if token.kind is not Kind.IDENT:
            continue

        name = token.value
        if name not in _CONNECTORS and name not in _INLINE:
            continue
        # A bare mention is not a call. `Sql.Database` as a value passed to
        # something else names no server, and reporting one would be invention.
        if index + 1 >= len(tokens) or tokens[index + 1].raw != "(":
            continue

        if name in _INLINE:
            # Keyed on the category alone, not the function. These nest --
            # `Table.FromRows(Json.Document(Binary.FromText(...)))` is one
            # table of constants, and reporting it three times would say a
            # table has three sources when it has none.
            key = ("inline", "")
            if key not in seen:
                seen.add(key)
                found.append(
                    SourceReference(
                        kind="inline",
                        detail="values written into the query",
                        function=name,
                    )
                )
            continue

        literal = _first_string_argument(tokens, index + 1)
        if literal is None:
            # Called, but with something other than a literal -- a parameter or
            # an expression. That it *is* a connector is still worth recording;
            # which instance it points at genuinely is not knowable from here.
            literal = ""

        reference = SourceReference(
            kind=_CONNECTORS[name],
            detail=literal or "named by an expression, not a literal",
            function=name,
        )
        key = (reference.kind, reference.detail)
        if key not in seen:
            seen.add(key)
            found.append(reference)

    return tuple(found)


def _first_string_argument(tokens: list[Token], open_paren: int) -> str | None:
    """The first string literal in this call's own argument list.

    Depth-tracked so a literal belonging to a nested call is not mistaken for
    this one's. ``Csv.Document(File.Contents("batch.csv"), [Delimiter=","])``
    is the case that matters: reading the first string at any depth would give
    ``Csv.Document`` the delimiter or the path depending on order, and neither
    is an argument it was passed.
    """
    depth = 0
    for token in tokens[open_paren:]:
        if token.kind is Kind.OP:
            if token.raw in "([{":
                depth += 1
                continue
            if token.raw in ")]}":
                depth -= 1
                if depth == 0:
                    return None
                continue
        if depth == 1 and token.kind is Kind.STRING:
            return token.value
    return None
