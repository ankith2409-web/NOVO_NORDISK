"""Reading a table's real source out of its Power Query.

Lineage used to end at the Power BI table. Everything below is about the step
past it -- naming the CSV, the workbook, the warehouse a table is loaded from --
because for a tool about the seam between a report and the platforms beneath
it, stopping exactly at that seam was the wrong place to stop.

Two properties matter more than breadth of connector support. The first is that
a source is only ever reported when the query genuinely names one: a guessed
path in a lineage diagram is worse than an absent one, because a diagram is
believed. The second is that lexing beats matching -- a Windows path is full of
backslashes, an M string escapes a quote by doubling it, and a comment can
contain anything, and each of those defeats text matching in a way that yields
a confident wrong answer rather than no answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.graph.csg import SemanticGraph
from concordance.normalize.mquery import Kind, extract_sources, lex

MODEL = Path("data/models/QualityControl.SemanticModel")


def kinds(m: str) -> list[tuple[str, str]]:
    return [(s.kind, s.detail) for s in extract_sources(m)]


# -- the shapes that actually appear in the sample models ---------------------

def test_a_csv_behind_a_format_wrapper_is_found() -> None:
    """`Csv.Document(File.Contents("x.csv"), [Delimiter=","])`.

    The path belongs to the inner call. Reading the first string literal at any
    depth would hand `Csv.Document` either the path or the delimiter depending
    only on argument order, and neither is an argument it was passed.
    """
    m = (
        'let\n  Source = Csv.Document(File.Contents("batch.csv"), [Delimiter=","]),\n'
        "  Promoted = Table.PromoteHeaders(Source)\nin\n  Promoted"
    )
    assert kinds(m) == [("file", "batch.csv")]


def test_a_workbook_with_a_windows_path_survives_its_backslashes() -> None:
    m = 'let Source = Excel.Workbook(File.Contents("C:\\Users\\jt\\AW Sales.xlsx"), null, true) in Source'
    found = extract_sources(m)
    assert found[0].kind == "file"
    assert found[0].detail == "C:\\Users\\jt\\AW Sales.xlsx"
    # The label is what a person recognises; the directory is usually one
    # developer's home folder and identifies nothing.
    assert found[0].label == "AW Sales.xlsx"


def test_a_generated_table_names_no_source() -> None:
    """`Calendar.Dates(#date(2024,1,1), ...)` invents its rows. Reporting a
    source for it would be inventing one."""
    assert kinds("let Source = Calendar.Dates(#date(2024,1,1), 1096) in Source") == []


def test_values_written_into_the_query_are_reported_once_not_three_times() -> None:
    """Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText(...)))) is
    one table of constants. Three of the four functions in that nest are in the
    inline set, and reporting each would say a table has three sources when it
    has none."""
    m = (
        'let Source = Table.FromRows(Json.Document(Binary.Decompress('
        'Binary.FromText("abc", BinaryEncoding.Base64), Compression.Deflate))) in Source'
    )
    found = extract_sources(m)
    assert len(found) == 1
    assert found[0].kind == "inline"


# -- the platforms this project is asked about --------------------------------

@pytest.mark.parametrize(
    "expression,kind,detail",
    [
        ('Sql.Database("prod-sql", "Warehouse")', "sql server", "prod-sql"),
        ('Snowflake.Databases("acme.snowflakecomputing.com", "WH")', "snowflake", "acme.snowflakecomputing.com"),
        ('Databricks.Catalogs("adb-1.azuredatabricks.net", "/sql/1")', "databricks", "adb-1.azuredatabricks.net"),
        ('AmazonRedshift.Database("rs.aws.com:5439", "dev")', "redshift", "rs.aws.com:5439"),
        ('AzureStorage.Blobs("https://acme.blob.core.windows.net")', "azure blob storage", "https://acme.blob.core.windows.net"),
        ('Web.Contents("https://api.example.com/v1")', "web", "https://api.example.com/v1"),
    ],
)
def test_warehouse_and_service_connectors_are_named(
    expression: str, kind: str, detail: str
) -> None:
    assert kinds(f"let Source = {expression} in Source") == [(kind, detail)]


# -- why this is lexed rather than matched ------------------------------------

def test_a_connector_named_inside_a_comment_is_not_a_source() -> None:
    """The case that separates lexing from text matching. A commented-out
    connector is not a source, and text matching cannot tell."""
    m = (
        'let\n  // Source = Sql.Database("old-server", "Legacy"),\n'
        '  Source = Csv.Document(File.Contents("batch.csv"))\nin Source'
    )
    assert kinds(m) == [("file", "batch.csv")]


def test_a_connector_named_inside_a_string_is_not_a_source() -> None:
    m = 'let Note = "we used to use Sql.Database(\'old\')" in Note'
    assert kinds(m) == []


def test_a_doubled_quote_does_not_end_the_string_early() -> None:
    """M escapes a quote by doubling it. Ending the string at the first quote
    would truncate the path and leave the rest of the query as stray tokens."""
    tokens = [t for t in lex('"a""b.csv"') if t.kind is Kind.STRING]
    assert tokens[0].value == 'a"b.csv'


def test_a_bare_mention_of_a_connector_is_not_a_call() -> None:
    """`Sql.Database` passed as a value names no server. Reporting one would be
    invention rather than extraction."""
    assert kinds("let f = Sql.Database in f") == []


def test_a_connector_called_with_a_parameter_is_recorded_without_a_guess() -> None:
    """That it *is* a database is knowable; which one is genuinely not. Saying
    so beats both silence and a fabricated name."""
    found = extract_sources('let Source = Sql.Database(ServerParam, DbParam) in Source')
    assert len(found) == 1
    assert found[0].kind == "sql server"
    assert "not a literal" in found[0].detail


def test_a_quoted_step_name_does_not_derail_the_lexer() -> None:
    """`#"Changed Type"` is one identifier containing a space. Treating the
    quote as a string start would swallow the rest of the query."""
    m = (
        'let\n  Source = Csv.Document(File.Contents("t.csv")),\n'
        '  #"Changed Type" = Table.TransformColumnTypes(Source, {})\nin #"Changed Type"'
    )
    assert kinds(m) == [("file", "t.csv")]


def test_an_unknown_connector_yields_nothing_rather_than_a_guess() -> None:
    """Anything shaped like `X.Database` is not assumed to be one. A wrong
    source in a lineage diagram is worse than an absent one."""
    assert kinds('let Source = Wobbly.Database("srv", "db") in Source') == []


# -- and the reason any of it exists: the graph ------------------------------

def test_the_source_reaches_the_graph_as_a_node_the_table_points_at() -> None:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    graph = SemanticGraph(TmdlAdapter().extract(str(MODEL)))

    sources = [n for n, d in graph.graph.nodes(data=True) if d.get("kind") == "source"]
    assert "source:file:batch.csv" in sources

    # Direction matters: an out-edge means "reads" everywhere else in this
    # graph, so the table points at its source rather than the other way round.
    # Half the arrows meaning "reads" and half meaning "feeds" cannot be walked
    # without special-casing every kind.
    loads = [
        (s, t) for s, t, d in graph.graph.out_edges("table:Batch", data=True)
        if d.get("kind") == "loads"
    ]
    assert loads == [("table:Batch", "source:file:batch.csv")]


def test_two_tables_from_one_workbook_share_one_node() -> None:
    """"These four tables all come from one spreadsheet on somebody's laptop"
    is a finding; one node per table would hide it."""
    from concordance.graph.csg import source_id

    assert source_id("file", "AW.xlsx") == source_id("file", "AW.xlsx")
    assert source_id("file", "AW.xlsx") != source_id("file", "Other.xlsx")
