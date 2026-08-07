"""Command line entry point.

``concordance extract``  -- turn a .pbix into a semantic graph on disk
``concordance inspect``  -- show what was extracted, without writing anything
``concordance explain``  -- show one object's expression, fingerprint and dependencies
``concordance verify``   -- prove the fingerprint scheme on a real measure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from concordance.adapters.pbix import PbixAdapter
from concordance.fingerprint import fingerprint_dax, short
from concordance.graph.csg import SemanticGraph, measure_id
from concordance.normalize.dax import canonicalise


def _load(path: str) -> SemanticGraph:
    """Load a model, choosing the adapter by what the path actually is."""
    from concordance.adapters.tmdl import TmdlAdapter

    target = Path(path)
    if target.is_dir() or target.suffix.lower() in {".pbip", ".tmdl"}:
        return SemanticGraph(TmdlAdapter().extract(path))
    return SemanticGraph(PbixAdapter().extract(path))


def cmd_extract(args: argparse.Namespace) -> int:
    graph = _load(args.source)
    payload = graph.to_dict()

    out = Path(args.out) if args.out else Path("data/out") / f"{graph.model.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    stats = payload["stats"]
    print(f"{graph.model.name}  ->  {out}")
    print(f"  {stats['nodes']} nodes, {stats['edges']} edges")
    if stats["unresolved_references"]:
        print(f"  {stats['unresolved_references']} unresolved references")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    graph = _load(args.source)
    model = graph.model

    print(f"\n{model.name}   [{model.source_type}]")
    print("=" * 64)
    for key, value in model.summary().items():
        print(f"  {key:<20} {value}")

    print("\nTables")
    print("-" * 64)
    for table in model.tables:
        marker = "  (system)" if table.is_system else ""
        columns = sum(1 for c in model.columns if c.table == table.name)
        measures = sum(1 for m in model.measures if m.table == table.name)
        print(f"  {table.name:<42} {columns:>3}c {measures:>3}m{marker}")

    print("\nJoins")
    print("-" * 64)
    for join in graph.join_paths():
        state = "" if join["is_active"] else "  INACTIVE"
        print(
            f"  {join['from']}[{join['from_column']}] -> "
            f"{join['to']}[{join['to_column']}]"
            f"   {join['cardinality']} {join['cross_filter']}{state}"
        )

    if model.hierarchies:
        print(f"\nHierarchies ({len(model.hierarchies)}, "
              f"{len(model.user_hierarchies())} on user tables)")
        print("-" * 64)
        for hierarchy in model.hierarchies:
            system = any(
                t.is_system and t.name == hierarchy.table for t in model.tables
            )
            marker = "  (system)" if system else ""
            print(f"  {short(hierarchy.fingerprint)}  "
                  f"{hierarchy.qualified_name:<38} {hierarchy.path}{marker}")

    print(f"\nMeasures ({len(model.measures)})")
    print("-" * 64)
    for measure in sorted(model.measures, key=lambda m: (m.table, m.name))[: args.limit]:
        deps = len(measure.depends_on_measures) + len(measure.depends_on_columns)
        print(f"  {short(measure.fingerprint)}  {measure.qualified_name:<44} {deps} deps")
    if len(model.measures) > args.limit:
        print(f"  ... {len(model.measures) - args.limit} more (use --limit)")

    if graph.unresolved:
        print(f"\nUnresolved references ({len(graph.unresolved)})")
        print("-" * 64)
        for ref in graph.unresolved[:10]:
            print(f"  {ref.source}  ->  {ref.target}   ({ref.reason})")

    # Printed last and unconditionally labelled, because an unnoticed coverage
    # gap is worse than a noisy one: it makes an incomplete graph look finished.
    if model.coverage_gaps:
        print(f"\nNOT EXTRACTED ({len(model.coverage_gaps)} feature types present "
              f"in this model)")
        print("-" * 64)
        for gap in model.coverage_gaps:
            print(f"  {gap.count:>4}  {gap.feature}")
        print("  These exist in the source model and are absent from the graph.")
    else:
        print("\nCoverage: no unextracted model features detected.")

    print()
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    graph = _load(args.source)
    matches = [
        m for m in graph.model.measures if m.name.casefold() == args.measure.casefold()
    ]
    if not matches:
        print(f"no measure named {args.measure!r}", file=sys.stderr)
        print("\navailable:", file=sys.stderr)
        for m in sorted(graph.model.measures, key=lambda m: m.name)[:25]:
            print(f"  {m.name}", file=sys.stderr)
        return 1

    for measure in matches:
        node = measure_id(measure.table, measure.name)
        print(f"\n{measure.qualified_name}")
        print("=" * 64)
        print(f"fingerprint : {measure.fingerprint}")
        print(f"\nexpression  :\n{measure.expression.strip()}")
        print(f"\ncanonical   :\n{canonicalise(measure.expression)}")

        deps = graph.dependencies_of(node)
        print(f"\ndepends on ({len(deps)}):")
        for dep in deps:
            print(f"  {dep}")

        dependents = graph.dependents_of(node)
        print(f"\nwould break if changed ({len(dependents)}):")
        for dependent in dependents:
            print(f"  {dependent}")
    print()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Demonstrate on a real measure that the fingerprint tracks meaning, not text."""
    graph = _load(args.source)
    measure = next(
        (m for m in graph.model.measures if m.name.casefold() == args.measure.casefold()),
        None,
    )
    if measure is None:
        print(f"no measure named {args.measure!r}", file=sys.stderr)
        return 1

    original = measure.expression
    reformatted = (
        "// reformatted, semantics untouched\n"
        + original.replace("(", " ( ").replace(",", " ,\n    ").upper()
        # Upper-casing the whole expression would also change string literals,
        # so restore them: only object names are case-insensitive in DAX.
    )
    reformatted = _restore_literals(original, reformatted)

    print(f"\n{measure.qualified_name}")
    print("=" * 64)
    print(f"original      {short(fingerprint_dax(original))}")
    print(f"reformatted   {short(fingerprint_dax(reformatted))}   "
          f"{'same -- no drift' if fingerprint_dax(original) == fingerprint_dax(reformatted) else 'DIFFERENT'}")

    mutated = _mutate(original)
    if mutated is None:
        print("\n(no literal to mutate in this measure; skipping the change case)")
        return 0

    changed = fingerprint_dax(original) != fingerprint_dax(mutated)
    print(f"altered       {short(fingerprint_dax(mutated))}   "
          f"{'DRIFT DETECTED' if changed else 'missed -- bug'}")
    print(f"\nmutation applied:\n  {mutated.strip()[:200]}")
    print()
    return 0 if changed else 1


def _restore_literals(original: str, transformed: str) -> str:
    """Put original-cased string literals back after a case-changing transform."""
    import re

    literals = re.findall(r'"(?:[^"]|"")*"', original)
    if not literals:
        return transformed
    out = transformed
    for literal in literals:
        out = out.replace(literal.upper(), literal)
    return out


def _mutate(expr: str) -> str | None:
    """Change what the expression means, minimally."""
    import re

    literal = re.search(r'"((?:[^"]|"")*)"', expr)
    if literal:
        return expr[: literal.start(1)] + literal.group(1) + "_MUTATED" + expr[literal.end(1):]
    number = re.search(r"(?<![\w.])(\d+)(?![\w.])", expr)
    if number:
        return expr[: number.start(1)] + str(int(number.group(1)) + 1) + expr[number.end(1):]
    return None


def cmd_document(args: argparse.Namespace) -> int:
    """Generate a BRD or FRD from an implemented model."""
    from concordance.generate import document as doc
    from concordance.generate.requirements import Kind

    graph = _load(args.source)
    kind = Kind.BUSINESS if args.type == "brd" else Kind.FUNCTIONAL
    built = doc.build(graph, kind)
    markdown = doc.to_markdown(built)

    if args.out:
        out = Path(args.out)
    else:
        out = Path("data/out") / f"{graph.model.name}.{args.type}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")

    counts = built.counts()
    print(f"\n{built.title}")
    print("=" * 64)
    print(f"  {counts['requirements']} requirements across {len(built.sections)} sections")
    print(f"  {counts['high']} stated by the model")
    print(f"  {counts['medium']} inferred from structure")
    print(f"  {counts['low']} need human confirmation")
    for section in built.sections:
        print(f"    {len(section.requirements):>4}  {section.title}")
    if built.review_queue:
        print(f"\n  Review queue ({len(built.review_queue)}):")
        for requirement in built.review_queue:
            print(f"    {requirement.id}  {doc._plain(requirement.statement)[:80]}")
    print(f"\n  written to {out}\n")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Ask a question about a model, or open an interactive session."""
    from concordance.agent.chat import ModelChat
    from concordance.llm.base import LlmError
    from concordance.llm.gemini import GeminiProvider

    graph = _load(args.source)
    try:
        provider = GeminiProvider(model=args.model)
    except LlmError as error:
        print(f"{error}", file=sys.stderr)
        return 2

    chat = ModelChat(graph, provider)

    def answer(question: str) -> None:
        try:
            exchange = chat.ask(question)
        except LlmError as error:
            print(f"\n  {error}\n", file=sys.stderr)
            return
        print(f"\n{exchange.answer}\n")
        if args.show_tools:
            for name, arguments in exchange.tool_calls:
                print(f"  · {name}({', '.join(f'{k}={v!r}' for k, v in arguments.items())})")
            for rejected in exchange.rejected_calls:
                print(f"  · {rejected} — rejected, no such tool")
            if not exchange.grounded:
                print("  · answered without consulting the model")
            print()

    if args.question:
        answer(" ".join(args.question))
        return 0

    print(f"Asking about {graph.model.name} via {provider.name}. Ctrl-D to exit.")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question:
            answer(question)


def cmd_serve(args: argparse.Namespace) -> int:
    """Run a local web chat interface for a model."""
    from concordance.llm.base import LlmError
    from concordance.llm.gemini import GeminiProvider
    from concordance.web.server import serve

    graph = _load(args.source)
    try:
        provider = GeminiProvider(model=args.model)
    except LlmError as error:
        print(f"{error}", file=sys.stderr)
        return 2

    serve(graph, provider, host=args.host, port=args.port)
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Record the current fingerprints of a model."""
    from concordance.drift import snapshot as snap

    graph = _load(args.source)
    taken = snap.take(graph, label=args.label or "")

    out = Path(args.out) if args.out else (
        Path("data/snapshots") / f"{graph.model.name}.{args.label or 'latest'}.json"
    )
    taken.save(out)

    print(f"\n{taken.model_name}  [{taken.label or 'unlabelled'}]")
    print(f"  {len(taken.objects)} fingerprinted objects")
    print(f"  taken {taken.taken_at}")
    print(f"  written to {out}\n")
    return 0


def _as_snapshot(path: str, label: str):
    """Read a snapshot file, or take one from a model on the spot."""
    from concordance.drift import snapshot as snap

    target = Path(path)
    if target.is_file() and target.suffix == ".json":
        return snap.load(target), None
    graph = _load(path)
    return snap.take(graph, label=label), graph


def cmd_drift(args: argparse.Namespace) -> int:
    """Compare two versions of a model and report what moved."""
    from concordance.drift.compare import compare, to_text

    before, _ = _as_snapshot(args.before, "before")
    after, after_graph = _as_snapshot(args.after, "after")

    if before.model_name != after.model_name and not args.allow_different_models:
        print(
            f"Refusing to compare {before.model_name!r} against {after.model_name!r}: "
            f"these look like different models, so every object would appear added "
            f"or removed. Pass --allow-different-models if that is genuinely what "
            f"you want.",
            file=sys.stderr,
        )
        return 2

    report = compare(before, after, after_graph=after_graph)
    print()
    print(to_text(report))
    print()
    # Non-zero when drift is found, so this can gate a pipeline.
    return 1 if (report.has_drift and args.fail_on_drift) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="concordance",
        description="Extract a canonical semantic graph from a Power BI model.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="write the semantic graph to JSON")
    p.add_argument("source", help="path to a .pbix file")
    p.add_argument("-o", "--out", help="output path (default data/out/<name>.json)")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("inspect", help="print what was extracted")
    p.add_argument("source", help="path to a .pbix file")
    p.add_argument("--limit", type=int, default=20, help="measures to list")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("explain", help="show one measure in full")
    p.add_argument("source", help="path to a .pbix file")
    p.add_argument("measure", help="measure name")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("verify", help="prove the fingerprint on a real measure")
    p.add_argument("source", help="path to a .pbix file")
    p.add_argument("measure", help="measure name")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("ask", help="ask questions about a model")
    p.add_argument("source", help="path to a .pbix file or TMDL model folder")
    p.add_argument("question", nargs="*", help="question; omit for interactive mode")
    p.add_argument("--model", default="gemini-3.6-flash", help="Gemini model to use")
    p.add_argument("--show-tools", action="store_true",
                   help="show which tools were called to reach the answer")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("snapshot", help="record a model's fingerprints for later comparison")
    p.add_argument("source", help="path to a .pbix file or TMDL model folder")
    p.add_argument("--label", help="name for this snapshot, e.g. v1 or 2026-08-07")
    p.add_argument("-o", "--out", help="output path")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("drift", help="compare two model versions and report what changed")
    p.add_argument("before", help="a snapshot .json, or a model to snapshot now")
    p.add_argument("after", help="a snapshot .json, or a model to snapshot now")
    p.add_argument("--fail-on-drift", action="store_true",
                   help="exit non-zero when drift is found, for use in a pipeline")
    p.add_argument("--allow-different-models", action="store_true",
                   help="compare across differently-named models")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("serve", help="run a local web chat interface for a model")
    p.add_argument("source", help="path to a .pbix file or TMDL model folder")
    p.add_argument("--model", default="gemini-3.6-flash", help="Gemini model to use")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("document", help="generate a BRD or FRD from a model")
    p.add_argument("source", help="path to a .pbix file")
    p.add_argument("--type", choices=["brd", "frd"], default="brd",
                   help="business (brd) or functional (frd) requirements")
    p.add_argument("-o", "--out", help="output path (default data/out/<name>.<type>.md)")
    p.set_defaults(func=cmd_document)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
