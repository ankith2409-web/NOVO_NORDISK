"""Comparing the same KPI across platforms.

The recurring failure this addresses: the same metric name is implemented once
in Power BI and again in the warehouse, the two definitions differ, and nobody
notices until two reports disagree in a meeting.

Fingerprints cannot answer this. A fingerprint proves a definition has not
changed *in the same language*; DAX and SQL never hash alike even when they
compute exactly the same thing, so equal fingerprints are impossible and unequal
ones prove nothing. Comparison therefore works on what each definition
structurally *does*: which tables it reads, which columns it reads, and which
aggregations it applies.

That yields evidence, not a verdict. Proving two arbitrary expressions in two
languages compute the same number is undecidable in general, so this module
reports what differs and how strongly that suggests a real divergence, and
leaves the judgement to a person. Claiming more would be the confident-but-wrong
behaviour the whole project is built to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from concordance.adapters import sql as sqladapter
from concordance.graph.csg import SemanticGraph
from concordance.model import Measure, SemanticModel
from concordance.normalize.dax import extract_functions, extract_references

#: DAX and SQL spell the same aggregation differently, and an engine rewrites
#: them again when it stores a view -- DuckDB records ``COUNT(*)`` as
#: ``count_star``. Keys are matched with separators removed, so ``COUNT_IF`` and
#: ``COUNTIF`` are one entry. Only pairs that mean the same thing are listed, so
#: a mapping never manufactures a false agreement.
_AGGREGATION_SYNONYMS = {
    "SUM": "SUM",
    "SUMX": "SUM",
    "COUNT": "COUNT",
    "COUNTROWS": "COUNT",
    "COUNTA": "COUNT",
    "COUNTSTAR": "COUNT",
    "COUNTIF": "COUNT",
    "AVERAGE": "AVG",
    "AVERAGEX": "AVG",
    "AVG": "AVG",
    "MIN": "MIN",
    "MINX": "MIN",
    "MAX": "MAX",
    "MAXX": "MAX",
    "MEDIAN": "MEDIAN",
    "DISTINCTCOUNT": "COUNT_DISTINCT",
    "COUNTDISTINCT": "COUNT_DISTINCT",
    "APPROXCOUNTDISTINCT": "COUNT_DISTINCT",
}


class Verdict(Enum):
    """How strongly the evidence points at a real divergence."""

    #: Same structural inputs and aggregations. No reason to suspect a difference.
    CONSISTENT = "consistent"
    #: Reads different data, or aggregates differently. Very likely to disagree.
    DIVERGENT = "divergent"
    #: Differs in ways that may or may not change the number. Needs a person.
    REVIEW = "review"


@dataclass(frozen=True)
class MetricDefinition:
    """One platform's implementation of a named metric."""

    name: str
    platform: str
    language: str
    expression: str
    tables: frozenset[str] = field(default_factory=frozenset)
    columns: frozenset[str] = field(default_factory=frozenset)
    aggregations: frozenset[str] = field(default_factory=frozenset)
    fingerprint: str = ""
    #: Other definitions this one is built on, whose inputs are folded in above.
    #: Reported so a reader can see why a metric is credited with reading a
    #: table its own text never names.
    resolved_through: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Metric names are matched case- and separator-insensitively.

        A warehouse view is ``oos_rate`` where the DAX measure is ``OOS Rate``;
        insisting on an exact match would find nothing at all.
        """
        return normalise_name(self.name)


@dataclass(frozen=True)
class Difference:
    aspect: str
    detail: str


@dataclass(frozen=True)
class Comparison:
    """What two or more platforms say about one metric."""

    metric: str
    definitions: tuple[MetricDefinition, ...]
    verdict: Verdict
    differences: tuple[Difference, ...] = field(default_factory=tuple)

    @property
    def platforms(self) -> tuple[str, ...]:
        return tuple(d.platform for d in self.definitions)

    @property
    def needs_attention(self) -> bool:
        return self.verdict is not Verdict.CONSISTENT


@dataclass(frozen=True)
class PossiblePairing:
    """Two unmatched names close enough that they may be the same metric.

    Deliberately not compared. Acting on a guessed pairing would attach a
    verdict to two metrics that might have nothing to do with each other, which
    is worse than saying nothing; the point is to put the question in front of
    someone who knows the answer.
    """

    left: str
    left_platform: str
    right: str
    right_platform: str
    similarity: float


@dataclass
class ReconciliationReport:
    comparisons: list[Comparison] = field(default_factory=list)
    #: Metrics defined on one platform only -- not a conflict, but worth seeing.
    unique_to_platform: dict[str, list[str]] = field(default_factory=dict)
    #: Unmatched names that resemble each other across platforms.
    possible_pairings: list[PossiblePairing] = field(default_factory=list)

    @property
    def shared(self) -> list[Comparison]:
        return self.comparisons

    def of_verdict(self, verdict: Verdict) -> list[Comparison]:
        return [c for c in self.comparisons if c.verdict is verdict]

    def counts(self) -> dict[str, int]:
        return {
            "shared_metrics": len(self.comparisons),
            "divergent": len(self.of_verdict(Verdict.DIVERGENT)),
            "review": len(self.of_verdict(Verdict.REVIEW)),
            "consistent": len(self.of_verdict(Verdict.CONSISTENT)),
        }


#: Characters a DAX measure name may contain but a SQL identifier may not, with
#: the spelling a modeller is forced into instead. Only symbols with one
#: conventional expansion are listed: ``#`` is left alone because it is written
#: out as ``num``, ``no`` and ``count`` about equally often, and encoding a guess
#: there would pair metrics that are not the same.
_SYMBOL_WORDS = {"%": "pct", "&": "and"}


def normalise_name(name: str) -> str:
    """``OOS Rate``, ``oos_rate`` and ``OOS-RATE`` are the same metric.

    ``Batch Yield %`` and ``batch_yield_pct`` are too. A percentage measure is
    almost always written with a ``%`` in Power BI and spelled ``pct`` in the
    warehouse, because SQL will not accept the symbol in an identifier. Dropping
    punctuation without expanding it first left the two looking unrelated, so
    the pair was never compared -- a metric missing from the report reads
    exactly like a metric that agrees.
    """
    folded = name.casefold()
    for symbol, word in _SYMBOL_WORDS.items():
        folded = folded.replace(symbol, word)
    return "".join(ch for ch in folded if ch.isalnum())


# -- building definitions ----------------------------------------------------

def from_power_bi(graph: SemanticGraph, platform: str = "power_bi") -> list[MetricDefinition]:
    """Every measure in a semantic model, as a comparable definition."""
    measures = graph.model.measures
    direct = {m.name: _dax_definition(m, platform) for m in measures}
    dependencies = {
        m.name: frozenset(d for d in m.depends_on_measures if d in direct) for m in measures
    }
    return _resolve(direct, dependencies)


def _dax_definition(measure: Measure, platform: str) -> MetricDefinition:
    refs = extract_references(measure.expression)
    functions = extract_functions(measure.expression)
    return MetricDefinition(
        name=measure.name,
        platform=platform,
        language="dax",
        expression=measure.expression.strip(),
        # Only the table part: DAX qualifies columns by table, SQL usually does
        # not, so comparing bare column names is the common ground.
        tables=frozenset(t for t, _ in refs.columns) | measure.depends_on_tables,
        columns=frozenset(c for _, c in refs.columns),
        aggregations=_normalise_aggregations(functions),
        fingerprint=measure.fingerprint,
    )


def from_warehouse(
    model: SemanticModel,
    platform: str = "warehouse",
    dialect: str = sqladapter.DEFAULT_DIALECT,
) -> list[MetricDefinition]:
    """Every view in a warehouse model, as a comparable definition."""
    direct: dict[str, MetricDefinition] = {}
    for measure in model.measures:
        tables, columns = sqladapter.references(measure.expression, dialect)
        direct[measure.name] = MetricDefinition(
            name=measure.name,
            platform=platform,
            language="sql",
            expression=measure.expression.strip(),
            tables=tables,
            columns=columns,
            aggregations=_normalise_aggregations(
                sqladapter.aggregations(measure.expression, dialect)
            ),
            fingerprint=measure.fingerprint,
        )

    # A view selecting from another view is the warehouse's version of a measure
    # calling a measure, and needs resolving for the same reason.
    by_lower = {name.casefold(): name for name in direct}
    dependencies = {
        name: frozenset(
            by_lower[t.casefold()]
            for t in definition.tables
            if t.casefold() in by_lower and by_lower[t.casefold()] != name
        )
        for name, definition in direct.items()
    }
    return _resolve(direct, dependencies)


def _resolve(
    direct: dict[str, MetricDefinition],
    dependencies: dict[str, frozenset[str]],
) -> list[MetricDefinition]:
    """Fold each definition's inputs into the ones built on top of it.

    ``OOS Rate = DIVIDE([OOS Results], [Tests Performed], 0)`` names no table and
    no column of its own, so compared as written it looks like it reads nothing
    -- and would be flagged against any warehouse implementation regardless of
    whether the two agree. What it actually reads is whatever the measures it
    calls read. Composition like this is normal in a well-built model, so
    resolving it is not a refinement; without it the comparison is noise.
    """
    resolved: dict[str, MetricDefinition] = {}

    def walk(name: str, path: frozenset[str]) -> MetricDefinition:
        if name in resolved:
            return resolved[name]

        definition = direct[name]
        tables = set(definition.tables)
        columns = set(definition.columns)
        aggregations = set(definition.aggregations)
        through: set[str] = set()

        for dependency in sorted(dependencies.get(name, frozenset())):
            # DAX rejects circular measures and a view cannot select from
            # itself, but a malformed export should not hang the report.
            if dependency in path or dependency not in direct:
                continue
            inner = walk(dependency, path | {name})
            tables |= inner.tables
            columns |= inner.columns
            aggregations |= inner.aggregations
            through.add(dependency)
            through.update(inner.resolved_through)

        out = replace(
            definition,
            tables=frozenset(tables),
            columns=frozenset(columns),
            aggregations=frozenset(aggregations),
            resolved_through=tuple(sorted(through)),
        )
        resolved[name] = out
        return out

    return [walk(name, frozenset()) for name in direct]


def _normalise_aggregations(functions: frozenset[str]) -> frozenset[str]:
    """Map platform-specific function names onto shared aggregation concepts."""
    return frozenset(
        concept
        for f in functions
        if (concept := _AGGREGATION_SYNONYMS.get(_bare_word(f))) is not None
    )


def _bare_word(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


# -- comparison ---------------------------------------------------------------

def reconcile(*definition_sets: list[MetricDefinition]) -> ReconciliationReport:
    """Compare metric definitions across platforms.

    Definitions keep the order the sets are passed in, so a caller that passes
    Power BI first gets Power BI listed first in every comparison.
    """
    by_key: dict[str, list[MetricDefinition]] = {}
    for definitions in definition_sets:
        for definition in definitions:
            by_key.setdefault(definition.key, []).append(definition)

    report = ReconciliationReport()

    for key in sorted(by_key):
        definitions = by_key[key]
        platforms = {d.platform for d in definitions}

        if len(platforms) < 2:
            platform = next(iter(platforms))
            report.unique_to_platform.setdefault(platform, []).append(definitions[0].name)
            continue

        report.comparisons.append(_compare(definitions))

    for names in report.unique_to_platform.values():
        names.sort()
    report.comparisons.sort(key=lambda c: (c.verdict.value, c.metric))
    report.possible_pairings = _possible_pairings(report.unique_to_platform)
    return report


#: Deliberately loose. There is no threshold that separates real pairs from
#: false ones: measured against this model's names, "Batches Released" and
#: "batches_rejected" score 0.80 and "Assay Mean" and "assay_max" score 0.82,
#: while true pairs like "Right First Time %" and "right_first_time_rate" score
#: 0.86 -- opposite meanings can differ by fewer characters than synonyms do,
#: and no character measure can know that. Since the output is a list of
#: questions rather than findings, a wrong suggestion costs a reader a few
#: seconds and a missed metric costs a wrong decision, so the cut favours
#: recall. This is also exactly why a near match is never compared
#: automatically.
_PAIRING_THRESHOLD = 0.80

#: Every unmatched name is weighed against every unmatched name on the other
#: platform, so a large warehouse could produce a long tail of weak suggestions.
#: Only the strongest are worth a person's attention.
_MAX_PAIRINGS = 20


def _possible_pairings(
    unique: dict[str, list[str]], threshold: float = _PAIRING_THRESHOLD
) -> list[PossiblePairing]:
    """Find unmatched names on different platforms that resemble each other.

    Name matching is where a report like this quietly under-delivers: two teams
    implement one KPI, spell it differently enough that the matcher misses it,
    and the metric is never compared. Nothing about the output distinguishes
    that from genuine agreement -- both look like silence. Rather than loosen
    matching until false pairs creep in, close-but-unmatched names are listed
    for a human to confirm.
    """
    from difflib import SequenceMatcher

    pairings: list[PossiblePairing] = []
    platforms = sorted(unique)

    for i, left_platform in enumerate(platforms):
        for right_platform in platforms[i + 1 :]:
            for left in unique[left_platform]:
                for right in unique[right_platform]:
                    score = SequenceMatcher(
                        None, normalise_name(left), normalise_name(right)
                    ).ratio()
                    if score >= threshold:
                        pairings.append(
                            PossiblePairing(
                                left=left,
                                left_platform=left_platform,
                                right=right,
                                right_platform=right_platform,
                                similarity=round(score, 3),
                            )
                        )

    pairings.sort(key=lambda p: (-p.similarity, p.left, p.right))
    return pairings[:_MAX_PAIRINGS]


def _compare(definitions: list[MetricDefinition]) -> Comparison:
    """Judge one metric's definitions against each other."""
    differences: list[Difference] = []

    table_sets = {d.platform: _bare_names(d.tables) for d in definitions}
    column_sets = {d.platform: _bare_names(d.columns) for d in definitions}
    aggregation_sets = {d.platform: d.aggregations for d in definitions}

    if len({frozenset(v) for v in table_sets.values()}) > 1:
        differences.append(
            Difference(
                "source tables",
                "; ".join(
                    f"{platform} reads {sorted(tables) or 'nothing identifiable'}"
                    for platform, tables in sorted(table_sets.items())
                ),
            )
        )

    if len({frozenset(v) for v in column_sets.values()}) > 1:
        differences.append(
            Difference(
                "source columns",
                "; ".join(
                    f"{platform} reads {sorted(columns) or 'nothing identifiable'}"
                    for platform, columns in sorted(column_sets.items())
                ),
            )
        )

    if len({frozenset(v) for v in aggregation_sets.values()}) > 1:
        differences.append(
            Difference(
                "aggregation",
                "; ".join(
                    f"{platform} applies {sorted(aggs) or 'none identifiable'}"
                    for platform, aggs in sorted(aggregation_sets.items())
                ),
            )
        )

    # Reading different data is the strongest available signal that two
    # definitions will disagree, and the one worth acting on first. Differing
    # only in aggregation *may* still agree -- COUNT over a filtered set can
    # equal SUM of a flag -- so that alone is put to a person rather than
    # asserted.
    aspects = {d.aspect for d in differences}
    if not differences:
        verdict = Verdict.CONSISTENT
    elif {"source tables", "source columns"} & aspects:
        verdict = Verdict.DIVERGENT
    else:
        verdict = Verdict.REVIEW

    return Comparison(
        metric=definitions[0].name,
        definitions=tuple(definitions),
        verdict=verdict,
        differences=tuple(differences),
    )


def _bare_names(names: frozenset[str]) -> frozenset[str]:
    """Compare on the object name alone.

    DAX writes ``TestResult[ResultStatus]`` where SQL writes ``result_status``.
    Stripping qualification and separators is what makes the two comparable at
    all; it is a deliberate loosening, and the reason a difference here is
    reported as evidence rather than proof.
    """
    return frozenset(normalise_name(n.split(".")[-1]) for n in names if n)


# -- rendering ----------------------------------------------------------------

def to_text(report: ReconciliationReport) -> str:
    counts = report.counts()
    lines: list[str] = []

    lines.append("Cross-platform KPI reconciliation")
    lines.append("=" * 68)
    lines.append(
        f"  {counts['shared_metrics']} metric(s) defined on more than one platform: "
        f"{counts['divergent']} divergent, {counts['review']} need review, "
        f"{counts['consistent']} consistent"
    )

    for verdict, heading in (
        (Verdict.DIVERGENT, "Divergent - these will report different numbers"),
        (Verdict.REVIEW, "Needs review - differs, but may still agree"),
        (Verdict.CONSISTENT, "Consistent"),
    ):
        items = report.of_verdict(verdict)
        if not items:
            continue
        lines.append("")
        lines.append(f"{heading} ({len(items)})")
        lines.append("-" * 68)
        for comparison in items:
            lines.append(f"  {comparison.metric}")
            for definition in comparison.definitions:
                lines.append(
                    f"      [{definition.platform}/{definition.language}] "
                    f"{_clip(definition.expression)}"
                )
                if definition.resolved_through:
                    lines.append(
                        f"          via {', '.join(definition.resolved_through)}"
                    )
            for difference in comparison.differences:
                lines.append(f"      ! {difference.aspect}: {_clip(difference.detail, 84)}")

    if report.possible_pairings:
        lines.append("")
        lines.append("Possibly the same metric under different names - confirm before acting")
        lines.append("-" * 68)
        for pairing in report.possible_pairings:
            lines.append(
                f"  {pairing.left} [{pairing.left_platform}]  ~  "
                f"{pairing.right} [{pairing.right_platform}]  ({pairing.similarity:.0%})"
            )

    if report.unique_to_platform:
        lines.append("")
        lines.append("Defined on one platform only")
        lines.append("-" * 68)
        for platform, names in sorted(report.unique_to_platform.items()):
            lines.append(f"  {platform}: {len(names)} metric(s)")

    return "\n".join(lines)


def _clip(text: str, width: int = 76) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "..."
