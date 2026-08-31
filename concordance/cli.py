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


class _BadAttachment(Exception):
    """A --compare-to or --warehouse naming a model this server was not given."""


def _split_attachment(
    value: str, known: set[str], default: str
) -> tuple[str, str]:
    """Split ``Model=value`` into the model it attaches to and the value.

    A value with no ``=``, or one whose prefix is not a model this server was
    given, attaches to ``default`` and is returned whole. That second case
    matters: a path may legitimately contain ``=``, and splitting on one that
    names nothing would turn a real path into a mysterious "no such file".
    Only a prefix that actually matches a loaded model is treated as a name.

    Raises ``KeyError`` when the prefix looks like a deliberate model name --
    it matched nothing, but the value before ``=`` is not path-shaped -- so the
    caller can refuse rather than silently attach it to the wrong model.
    """
    name, separator, rest = value.partition("=")
    if not separator:
        return default, value
    if name in known:
        return name, rest
    # A prefix with no path separators and no dot reads as an attempt to name a
    # model, not as part of a filename.
    if name and "/" not in name and "\\" not in name and "." not in name:
        raise KeyError(name)
    return default, value


def _load(path: str) -> SemanticGraph:
    """Load a model, choosing the adapter by what the path actually is.

    Everything that can go wrong here becomes a ``SourceError`` carrying the
    file's name and a readable problem. A third-party parser handed a file that
    is not the format it claims to be raises whatever it likes -- PBIXRay ends
    at "Unknown or unsupported DataModel compression format" -- and that
    reaching a user as a bare traceback tells them neither which file broke nor
    what to try instead.
    """
    from concordance.adapters.base import SourceError
    from concordance.adapters.tmdl import TmdlAdapter

    target = Path(path)
    is_tmdl = target.is_dir() or target.suffix.lower() in {".pbip", ".tmdl"}

    if not target.exists():
        raise SourceError(
            path,
            "no such file or folder",
            "Pass a .pbix file, or a TMDL model folder such as "
            "data/models/QualityControl.SemanticModel.",
        )

    try:
        model = (TmdlAdapter() if is_tmdl else PbixAdapter()).extract(path)
    except SourceError:
        raise
    except (ValueError, KeyError) as error:
        raise SourceError(path, str(error)) from error
    except Exception as error:
        # Whatever the underlying parser raised. The type is named because it is
        # often the only clue to what actually went wrong inside a dependency.
        raise SourceError(
            path,
            f"{type(error).__name__}: {error}",
            "This usually means the file is corrupt, truncated, or not the "
            "format its extension claims."
            if not is_tmdl
            else "This usually means a .tmdl file is malformed.",
        ) from error

    return SemanticGraph(model)


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
    grain = tuple(args.sql_grain or ())

    drift = None
    if getattr(args, "compare_to", None):
        from concordance.drift import snapshot as snap
        from concordance.drift.compare import compare

        previous = _load(args.compare_to)
        drift = compare(
            snap.take(previous, label=Path(args.compare_to).stem),
            snap.take(graph, label=graph.model.name),
            after_graph=graph,
        )

    built = doc.build(
        graph,
        kind,
        sql_grain=grain if (grain or args.sql) else None,
        sql_dialect=args.sql_dialect,
        drift=drift,
    )

    suffix = "docx" if args.format == "docx" else "md"
    out = Path(args.out) if args.out else (
        Path("data/out") / f"{graph.model.name}.{args.type}.{suffix}"
    )
    if args.format == "docx":
        from concordance.generate import word

        word.write(built, out)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(doc.to_markdown(built), encoding="utf-8")

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


#: Marks `--token` used as a bare flag, meaning "generate one for me".
_MINT_TOKEN_SENTINEL = "\x00mint"

#: argparse's default for --model. Recognised so the value can be treated as
#: "unset" rather than as a Gemini model name deliberately chosen.
_DEFAULT_MODEL_SENTINEL = "gemini-3.6-flash"


def _build_provider(args: argparse.Namespace):
    """Construct the provider chain, best first, or report why none could be built.

    Kept as one function so ``ask`` and ``serve`` cannot drift into supporting
    different providers by accident. Gemini stays the default: it is the one
    proven to work end to end in this project's own environment. The
    Anthropic, Groq and OpenRouter paths have been written against their
    documented APIs but could not be exercised against a live key from here --
    this sandbox's egress policy blocks all three hosts outright, and the fix
    is to verify locally, not to route around a policy denial.

    By default every provider with a key present joins a fallback chain behind
    the preferred one, because a free-tier quota running out mid-demo is a
    failure this project has actually hit. ``--no-fallback`` pins it to one.
    """
    from concordance.llm.base import LlmError
    from concordance.llm.fallback import FallbackProvider, available_providers

    # `--model` is only meaningful for the provider actually chosen, so it is
    # passed as None when it is still the argparse default -- otherwise a
    # Gemini model name would be handed to whichever provider comes next in the
    # chain and fail every question for an unreadable reason.
    chosen = None if args.model == _DEFAULT_MODEL_SENTINEL else args.model
    providers, skipped = available_providers(
        preferred=args.provider, model=chosen, base_url=args.base_url
    )

    if not providers:
        raise LlmError(
            "No language model provider could be configured.\n  "
            + "\n  ".join(skipped)
        )

    if args.no_fallback:
        # Keep only the requested provider. If its key is missing it is not in
        # the list at all, and saying so plainly beats silently using another.
        first = providers[0]
        if not first.name.startswith(args.provider):
            raise LlmError(
                f"--provider {args.provider} was requested but its key is not set.\n  "
                + "\n  ".join(skipped)
            )
        return first

    if skipped:
        print(
            "  no fallback available from: " + "; ".join(skipped),
            file=sys.stderr,
        )
    elif len(providers) > 1:
        print(
            "  fallback ready: " + " -> ".join(p.name for p in providers),
            file=sys.stderr,
        )

    return FallbackProvider(providers)


def _add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=("gemini", "anthropic", "groq", "openrouter"),
        default="gemini",
        help="which language model backs the chat (default gemini)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="override the provider's API host, e.g. a Claude-compatible gateway "
        "for --provider anthropic (ignored for --provider gemini; also read from "
        "ANTHROPIC_BASE_URL, GROQ_BASE_URL or OPENROUTER_BASE_URL)",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="use only the chosen provider; do not fall back to another when its "
        "quota is exhausted or its key is rejected",
    )


def cmd_ask(args: argparse.Namespace) -> int:
    """Ask a question about a model, or open an interactive session."""
    from concordance.agent.chat import ModelChat
    from concordance.llm.base import LlmError

    graph = _load(args.source)
    try:
        provider = _build_provider(args)
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
            if exchange.conversational:
                print("  · answered here, without a language model")
            elif not exchange.grounded:
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
    from concordance.web.server import serve

    from concordance.web.api import ApiContext, ModelRegistry

    sources = args.source if isinstance(args.source, list) else [args.source]
    graphs = [_load(source) for source in sources]
    graph = graphs[0]

    try:
        provider = _build_provider(args)
    except LlmError as error:
        print(f"{error}", file=sys.stderr)
        return 2

    # Resolved here, once, from arguments the operator typed. The browser never
    # names a file -- it names a key in the registry below -- so no request can
    # reach a path that was not authorised at launch.
    #
    # Both of these describe *one* of the loaded models, and which one can now
    # be said out loud: `--warehouse QualityControl=qc.duckdb`. Without a name
    # they attach to the first model, as they always did.
    #
    # The named form exists because the bare one made a real configuration
    # unreachable. A server showing drift on one model and reconciliation on
    # another could not be started at all: both flags bound to the first model,
    # so a warehouse built for the second compared against the first and
    # reported nothing in common. That is exactly the deployment this project
    # ships, and it silently served two "not configured" screens.
    known = {loaded.model.name: loaded for loaded in graphs}

    def _attached(value: str | None, what: str) -> tuple[str | None, str | None]:
        if not value:
            return None, None
        try:
            return _split_attachment(value, set(known), graph.model.name)
        except KeyError as error:
            print(
                f"--{what} names {error.args[0]!r}, which is not one of the "
                f"models this server was given: {', '.join(sorted(known))}.",
                file=sys.stderr,
            )
            raise _BadAttachment from None

    try:
        compare_owner, compare_path = _attached(args.compare_to, "compare-to")
        warehouse_owner, warehouse_path = _attached(args.warehouse, "warehouse")
    except _BadAttachment:
        return 2

    compare_to = _load(compare_path) if compare_path else None

    warehouse = Path(warehouse_path) if warehouse_path else None
    if warehouse and not warehouse.exists():
        print(f"No warehouse at {warehouse}.", file=sys.stderr)
        return 2

    contexts = {
        loaded.model.name: ApiContext(
            graph=loaded,
            # Reused rather than reconfigured: the same fallback chain the
            # chat already talks through, so the summary feature needs no
            # flag of its own and degrades the same way chat does when no
            # key is configured.
            provider=provider,
            compare_to=compare_to if loaded.model.name == compare_owner else None,
            compare_label=(
                Path(compare_path).name
                if compare_path and loaded.model.name == compare_owner
                else ""
            ),
            warehouse=warehouse if loaded.model.name == warehouse_owner else None,
            warehouse_schema=args.schema,
            decisions_reset=args.decisions_reset_on_restart,
            # One log per model. Sharing a file between models would let a
            # decision recorded against one model's requirement id collide
            # with another's, and requirement ids are only unique within a
            # model by construction.
            decisions=(
                Path(args.decisions).with_name(
                    f"{Path(args.decisions).stem}.{loaded.model.name}"
                    f"{Path(args.decisions).suffix or '.jsonl'}"
                )
                if args.decisions and len(graphs) > 1
                else (Path(args.decisions) if args.decisions else None)
            ),
        )
        for loaded in graphs
    }
    if len(contexts) != len(graphs):
        print(
            "Two of the given models share a name, so one would shadow the "
            "other. Rename a folder or file so each is distinguishable.",
            file=sys.stderr,
        )
        return 2

    context = ModelRegistry(contexts=contexts, default=graph.model.name)

    # `--token` with no value mints one, so turning auth on never depends on
    # someone inventing a good secret under time pressure.
    token = args.token
    if token == _MINT_TOKEN_SENTINEL:
        import secrets as _secrets

        token = _secrets.token_urlsafe(24)

    auth0 = None
    if args.auth0_domain or args.auth0_audience or args.auth0_client_id:
        from concordance.review.auth0 import Auth0Error, Auth0Verifier

        missing = [
            name
            for name, value in (
                ("--auth0-domain", args.auth0_domain),
                ("--auth0-audience", args.auth0_audience),
                ("--auth0-client-id", args.auth0_client_id),
            )
            if not value
        ]
        if missing:
            # All three or none. Two of the three produces a sign-in page that
            # cannot complete a sign-in, which is worse than refusing to start.
            print(
                f"Auth0 needs {', '.join(missing)} as well as what was given.",
                file=sys.stderr,
            )
            return 2
        try:
            auth0 = Auth0Verifier(
                domain=args.auth0_domain,
                audience=args.auth0_audience,
                client_id=args.auth0_client_id,
            )
        except Auth0Error as error:
            print(f"{error}", file=sys.stderr)
            return 2
        try:
            import jwt  # noqa: F401
            import cryptography  # noqa: F401
        except ImportError:
            # Caught at startup rather than on the first sign-in attempt. A
            # server that accepts an --auth0-domain and then cannot verify
            # anything is a server that looks protected and is not.
            print(
                "Auth0 verification needs PyJWT with its crypto extra. "
                'Install it with: pip install "concordance[auth0]"',
                file=sys.stderr,
            )
            return 2
        if not args.decisions:
            print(
                "--auth0-domain identifies reviewers, but without --decisions "
                "there is no log to record them in. Pass --decisions <path.jsonl>.",
                file=sys.stderr,
            )
            return 2

    users = None
    if args.users:
        from concordance.review.identity import Directory, DirectoryError

        try:
            users = Directory.load(args.users)
        except DirectoryError as error:
            # Refused rather than degraded. Falling back to an unidentified
            # server after being asked to identify reviewers would record
            # claims in a log the operator believes is authenticated.
            print(f"{error}", file=sys.stderr)
            return 2
        if not args.decisions:
            print(
                "--users identifies reviewers, but without --decisions there is "
                "no log to record them in. Pass --decisions <path.jsonl> too.",
                file=sys.stderr,
            )
            return 2

    serve(
        graph,
        provider,
        host=args.host,
        port=args.port,
        context=context,
        access_token=token or "",
        users=users,
        auth0=auth0,
        accepts_uploads=not args.no_upload,
    )
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    """Emit the model's lineage as OpenLineage DatasetEvents."""
    from concordance.generate import openlineage

    graph = _load(args.source)
    emitted = openlineage.emit(graph, namespace=args.namespace)

    out = Path(args.out) if args.out else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(emitted.to_json(), encoding="utf-8")
        print(f"{graph.model.name}  ->  {out}")
        print(f"  {len(emitted.events)} dataset events")
    else:
        print(emitted.to_json())
        return 0

    # Said out loud rather than left to be discovered by comparing counts. A
    # catalog fed lineage that quietly omits measures shows a model simpler
    # than the real one.
    if emitted.omitted:
        print(f"  {len(emitted.omitted)} measures carry no column lineage:")
        for measure, why in emitted.omitted[:5]:
            print(f"    {measure} — {why}")
        if len(emitted.omitted) > 5:
            print(f"    ... and {len(emitted.omitted) - 5} more")
        print(
            "  These depend on filter context, so which columns they read is "
            "not fixed. Guessing would be worse than the gap."
        )
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
        return snap.Snapshot.load(target), None
    graph = _load(path)
    # Label with what was actually read, so the report header distinguishes the
    # two sides instead of saying "before -> after".
    return snap.take(graph, label=label or target.name), graph


def cmd_drift(args: argparse.Namespace) -> int:
    """Compare two versions of a model and report what moved."""
    from concordance.drift.compare import compare, to_text

    before, _ = _as_snapshot(args.before, "")
    after, after_graph = _as_snapshot(args.after, "")

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

    # What the gate fires on. `revalidation` is the default worth wiring into a
    # pipeline: it fails only when a requirement rests on something whose logic
    # actually moved. `any` fires on renames too, which is almost always noise --
    # a check that fails a build for a rename is one people learn to bypass, and
    # a bypassed gate protects nothing.
    mode = "any" if getattr(args, "fail_on_drift", False) else args.fail_on
    triggered = {
        "none": False,
        "any": report.has_drift,
        "semantic": bool(report.semantic_changes),
        "revalidation": bool(report.needing_revalidation),
    }[mode]

    if triggered:
        reason = {
            "any": f"{len(report.changes)} object(s) differ",
            "semantic": f"{len(report.semantic_changes)} object(s) changed what they compute",
            "revalidation": (
                f"{len(report.needing_revalidation)} requirement(s) rest on a "
                f"definition that no longer matches"
            ),
        }[mode]
        print(f"FAILED (--fail-on {mode}): {reason}.", file=sys.stderr)
        for item in report.needing_revalidation:
            print(f"  {item.id}  {item.requirement.statement[:88]}", file=sys.stderr)
        print(file=sys.stderr)
    return 1 if triggered else 0


def cmd_auditpack(args: argparse.Namespace) -> int:
    """Write the evidence bundle for one model."""
    from concordance.generate import auditpack

    graph = _load(args.source)
    out = Path(args.out) if args.out else Path("data/out") / f"{graph.model.name}.auditpack"
    pack = auditpack.build(graph, out)

    print(f"\nEvidence pack — {graph.model.name}")
    print("=" * 64)
    print(f"  {pack.requirement_count} requirements, {pack.object_count} fingerprinted objects")
    if pack.needs_review:
        print(f"  {pack.needs_review} awaiting human confirmation")
    if pack.unresolved:
        print(f"  {pack.unresolved} unresolved reference(s), recorded in the manifest")
    if pack.coverage_gaps:
        print(f"  {pack.coverage_gaps} model feature type(s) not covered, recorded in the manifest")
    print()
    for path in pack.files:
        print(f"    {path.name}")
    print(f"\n  written to {pack.directory}\n")
    return 0


#: Which environment variable each connection parameter comes from, per
#: platform. Kept out of argparse, matching how the LLM providers take their
#: API keys -- a warehouse password is exactly the kind of value a shell
#: history or a process list should not carry.
#:
#: One table rather than a function per platform: every one of these is the
#: same three steps (read the environment, refuse politely if something
#: mandatory is missing, call the connector), and four near-identical functions
#: would be four places for that logic to drift apart.
_PLATFORM_ENV: dict[str, dict[str, str]] = {
    "snowflake": {
        "account": "SNOWFLAKE_ACCOUNT",
        "user": "SNOWFLAKE_USER",
        "password": "SNOWFLAKE_PASSWORD",
        "warehouse": "SNOWFLAKE_WAREHOUSE",
        "database": "SNOWFLAKE_DATABASE",
        "role": "SNOWFLAKE_ROLE",
    },
    "databricks": {
        "server_hostname": "DATABRICKS_SERVER_HOSTNAME",
        "http_path": "DATABRICKS_HTTP_PATH",
        "access_token": "DATABRICKS_TOKEN",
        "catalog": "DATABRICKS_CATALOG",
    },
    "redshift": {
        "host": "REDSHIFT_HOST",
        "database": "REDSHIFT_DATABASE",
        "user": "REDSHIFT_USER",
        "password": "REDSHIFT_PASSWORD",
        "port": "REDSHIFT_PORT",
        # Shared with Athena on purpose: AWS_REGION is the variable the AWS
        # SDKs themselves read, so a shell already configured for the AWS CLI
        # needs nothing added for either service.
        "region": "AWS_REGION",
    },
    "athena": {
        "s3_staging_dir": "ATHENA_S3_STAGING_DIR",
        "region": "AWS_REGION",
        "work_group": "ATHENA_WORKGROUP",
    },
}

#: What each platform cannot start without. Everything else in the table above
#: is genuinely optional and is omitted rather than passed empty, so the
#: connector's own default applies.
#:
#: Redshift lists neither user nor password: supplying both takes the password
#: path, supplying neither takes IAM through the standard AWS credential chain,
#: and demanding a password here would make the IAM path unreachable. Athena
#: lists no credentials at all for the same reason.
_PLATFORM_REQUIRED: dict[str, tuple[str, ...]] = {
    "snowflake": ("account", "user", "password", "warehouse", "database"),
    "databricks": ("server_hostname", "http_path", "access_token"),
    "redshift": ("host", "database"),
    "athena": ("s3_staging_dir", "region"),
}

#: The schema each platform calls its default one. Snowflake upper-cases
#: unquoted identifiers and the others lower-case them, which is why these
#: differ in case rather than all reading "public".
_PLATFORM_DEFAULT_SCHEMA = {
    "duckdb": "main",
    "snowflake": "PUBLIC",
    "databricks": "default",
    "redshift": "public",
    "athena": "default",
}

#: The connector function in ``adapters.sql`` each platform is served by.
_PLATFORM_CONNECTOR = {
    "snowflake": "from_snowflake",
    "databricks": "from_databricks",
    "redshift": "from_redshift",
    "athena": "from_athena",
}


def _warehouse_model(platform: str, schema: str):
    """Build the warehouse-side model from a live platform, or say what is missing.

    Anything mandatory that is absent is reported by the *variable name* the
    operator has to set, all of them at once, before a connection is attempted
    -- discovering a missing password one round-trip at a time is a worse
    experience than being told the whole list up front.

    Optional values are dropped rather than passed as empty strings, so each
    connector's own default applies. That distinction is load-bearing for
    Redshift: an empty password is not "no password" to the driver, and passing
    one would take the password path with a blank secret instead of the IAM
    path the caller intended.

    A failed connection is folded into ``SourceError`` like every other input
    problem this CLI reports, rather than left to surface as the driver's own
    traceback. A network policy rejecting the connection and a wrong password
    look identical from here -- both are "could not reach or use this
    warehouse" -- so both get the same readable treatment; ``--debug`` still
    shows the real one underneath.
    """
    import os

    from concordance.adapters import sql as sqladapter
    from concordance.adapters.base import SourceError

    variables = _PLATFORM_ENV[platform]
    values = {key: os.environ.get(var, "").strip() for key, var in variables.items()}

    missing = [variables[key] for key in _PLATFORM_REQUIRED[platform] if not values[key]]
    if missing:
        print(
            f"Missing environment variable(s) for --platform {platform}: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return None

    supplied = {key: value for key, value in values.items() if value}

    # The one non-string parameter in any of these tables. Rejected here rather
    # than handed to the driver as text, which would fail somewhere far less
    # obvious than the variable that actually holds the wrong value.
    if "port" in supplied:
        try:
            supplied["port"] = int(supplied["port"])
        except ValueError:
            print(
                f"{variables['port']} must be a number: {supplied['port']!r}",
                file=sys.stderr,
            )
            return None

    connect = getattr(sqladapter, _PLATFORM_CONNECTOR[platform])
    try:
        return connect(schema=schema, **supplied)
    except ImportError as error:
        # A missing driver is a setup problem with an exact fix, and saying
        # which extra to install beats the bare "No module named ..." the
        # lazy import would otherwise raise from inside the connector.
        print(
            f"--platform {platform} needs a driver that is not installed: {error}\n"
            f"  pip install 'concordance[{platform}]'",
            file=sys.stderr,
        )
        return None
    except Exception as error:
        # Named by the values that identify the target, never the secret ones.
        target = (
            values.get("account")
            or values.get("server_hostname")
            or values.get("host")
            or values.get("s3_staging_dir")
            or platform
        )
        raise SourceError(
            f"{platform}://{target}",
            f"{type(error).__name__}: {error}",
            "Check the connection details, credentials and network access. If "
            "this is running somewhere with restricted outbound access, a "
            "connection timeout here usually means that policy, not a wrong "
            "password -- try the same command from an unrestricted network.",
        ) from error


def _print_platforms() -> int:
    """List what each platform needs, so nobody has to read this file to find out.

    Marked required/optional rather than listed flat: which variables are
    genuinely mandatory is the part that is otherwise only discoverable by
    running the command and reading what it complains about.
    """
    print("\nEnvironment variables per --platform:\n")
    for platform in ("snowflake", "databricks", "redshift", "athena"):
        required = _PLATFORM_REQUIRED[platform]
        print(f"  {platform}  (pip install 'concordance[{platform}]')")
        for key, variable in _PLATFORM_ENV[platform].items():
            mark = "required" if key in required else "optional"
            print(f"      {variable:<28} {mark}")
        print(f"      {'--schema':<28} defaults to {_PLATFORM_DEFAULT_SCHEMA[platform]}")
        print()

    print("  redshift authenticates by password when REDSHIFT_USER and")
    print("  REDSHIFT_PASSWORD are both set, and by IAM through the standard AWS")
    print("  credential chain when they are not. athena always uses that chain.\n")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Compare one KPI's Power BI definition against the warehouse's."""
    from concordance.reconcile import metrics

    if getattr(args, "help_platforms", False):
        return _print_platforms()

    # `source` is only optional so `--help-platforms` can run without one;
    # every other invocation still requires it. Refused here rather than left
    # to fail inside `_load`, where a None path surfaces as a TypeError from
    # pathlib with no indication of what the caller forgot.
    if not args.source:
        print(
            "the following arguments are required: source\n"
            "  concordance reconcile <model> [--platform ...]",
            file=sys.stderr,
        )
        return 2

    graph = _load(args.source)

    if args.platform != "duckdb":
        model = _warehouse_model(
            args.platform, args.schema or _PLATFORM_DEFAULT_SCHEMA[args.platform]
        )
        if model is None:
            return 2
    else:
        import duckdb

        from concordance.adapters import sql as sqladapter

        warehouse = Path(args.warehouse)
        if not warehouse.exists():
            print(
                f"No warehouse at {warehouse}. Run scripts/build_warehouse.py to create "
                f"the local one, or pass --warehouse.",
                file=sys.stderr,
            )
            return 2

        try:
            connection = duckdb.connect(str(warehouse), read_only=True)
        except duckdb.Error as error:
            # The path exists -- checked above -- but is not a database DuckDB
            # can open: truncated, replaced by something else, or a real
            # database from an incompatible version. Left unguarded this
            # surfaces as a raw IOException, indistinguishable from the tool
            # itself crashing.
            print(f"Cannot open the warehouse at {warehouse}: {error}", file=sys.stderr)
            return 2
        try:
            model = sqladapter.from_duckdb(connection, schema=args.schema or "main")
        finally:
            connection.close()

    report = metrics.reconcile(
        metrics.from_power_bi(graph, platform=args.model_platform),
        metrics.from_warehouse(model, platform=args.warehouse_platform),
    )

    print()
    print(metrics.to_text(report))
    print()

    if model.coverage_gaps:
        print("Not read from the warehouse")
        print("-" * 68)
        for gap in model.coverage_gaps:
            print(f"  {gap.feature}: {gap.count} — {gap.reason}")
        print()

    # Non-zero when a metric is divergent, so this can gate a pipeline. Metrics
    # needing review do not fail the run: an unresolved question is not a defect,
    # and failing on one would train people to pass --no-fail permanently.
    divergent = report.counts()["divergent"]
    return 1 if (divergent and args.fail_on_conflict) else 0

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
    p.add_argument("--model", default="gemini-3.6-flash", help="model name for the chosen provider")
    _add_provider_arguments(p)
    p.add_argument("--show-tools", action="store_true",
                   help="show which tools were called to reach the answer")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser(
        "reconcile", help="compare a model's KPIs against the same KPIs in a warehouse"
    )
    # Optional only so `--help-platforms` can run without one. A missing source
    # is still refused below, with the same message argparse would have given.
    p.add_argument(
        "source", nargs="?", help="path to a .pbix file or TMDL model folder"
    )
    p.add_argument(
        "--platform",
        choices=("duckdb", "snowflake", "databricks", "redshift", "athena"),
        default="duckdb",
        help="which warehouse to read from. Everything except duckdb takes its "
        "connection details from environment variables rather than flags, the "
        "same way the chat's API keys are read -- a warehouse password does not "
        "belong in a shell history. See --help-platforms for the exact variables",
    )
    p.add_argument(
        "--help-platforms",
        action="store_true",
        help="list the environment variables each --platform reads, and exit",
    )
    p.add_argument(
        "--warehouse",
        default="data/warehouse/quality_control.duckdb",
        help="path to a DuckDB warehouse (default data/warehouse/quality_control.duckdb); "
        "ignored for every other --platform",
    )
    p.add_argument(
        "--schema",
        default=None,
        help="warehouse schema to read (defaults per platform: main for duckdb, "
        "PUBLIC for snowflake, default for databricks and athena, public for redshift)",
    )
    p.add_argument("--model-platform", default="power_bi", help="label for the model side")
    p.add_argument(
        "--warehouse-platform", default="warehouse", help="label for the warehouse side"
    )
    p.add_argument(
        "--fail-on-conflict",
        action="store_true",
        help="exit non-zero when a KPI is divergent, to gate a pipeline",
    )
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("auditpack", help="write the evidence bundle for a model")
    p.add_argument("source", help="path to a .pbix file or TMDL model folder")
    p.add_argument("-o", "--out", help="output directory")
    p.set_defaults(func=cmd_auditpack)

    p = sub.add_parser(
        "lineage",
        help="emit the model's lineage as OpenLineage, for a data catalog",
    )
    p.add_argument("source", help="path to a .pbix file or TMDL model folder")
    p.add_argument(
        "--namespace",
        default="powerbi",
        help="the OpenLineage namespace these datasets live in. OpenLineage's "
        "naming spec registers no scheme for a Power BI semantic model, so this "
        "one is a choice rather than a standard -- set it to whatever your "
        "catalog already uses.",
    )
    p.add_argument("-o", "--out", help="output path; prints to stdout when omitted")
    p.set_defaults(func=cmd_lineage)

    p = sub.add_parser("snapshot", help="record a model's fingerprints for later comparison")
    p.add_argument("source", help="path to a .pbix file or TMDL model folder")
    p.add_argument("--label", help="name for this snapshot, e.g. v1 or 2026-08-07")
    p.add_argument("-o", "--out", help="output path")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("drift", help="compare two model versions and report what changed")
    p.add_argument("before", help="a snapshot .json, or a model to snapshot now")
    p.add_argument("after", help="a snapshot .json, or a model to snapshot now")
    p.add_argument(
        "--fail-on",
        choices=("none", "any", "semantic", "revalidation"),
        default="none",
        help="exit non-zero to gate a pipeline: 'revalidation' when a requirement "
        "needs re-confirming (recommended), 'semantic' when any logic changed, "
        "'any' including renames (noisy)",
    )
    p.add_argument("--fail-on-drift", action="store_true",
                   help="deprecated alias for --fail-on any")
    p.add_argument("--allow-different-models", action="store_true",
                   help="compare across differently-named models")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("serve", help="run a local web chat interface for a model")
    p.add_argument(
        "source",
        nargs="+",
        help="one or more .pbix files or TMDL model folders. The first is the "
        "default the interface opens on, and the one --compare-to and "
        "--warehouse attach to unless they name a model explicitly.",
    )
    p.add_argument("--model", default="gemini-3.6-flash", help="model name for the chosen provider")
    _add_provider_arguments(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument(
        "--compare-to",
        metavar="[MODEL=]PATH",
        help="a second model to serve drift against, e.g. an earlier version. "
        "Prefix it with a loaded model's name to attach it to that one rather "
        "than the first, e.g. --compare-to ClinicalTrialSafety=path/to/v1",
    )
    p.add_argument(
        "--warehouse",
        metavar="[MODEL=]PATH",
        help="a DuckDB warehouse to serve reconciliation against. Prefix it "
        "with a loaded model's name to attach it to that one rather than the "
        "first, e.g. --warehouse QualityControl=data/warehouse/qc.duckdb",
    )
    p.add_argument("--schema", default="main", help="warehouse schema to read")
    p.add_argument(
        "--decisions",
        nargs="?",
        const="concordance-decisions.jsonl",
        help="record review decisions to this append-only JSON Lines file, so the "
        "Review queue can be answered rather than only read. Each decision is "
        "bound to the fingerprints of what it was made about, so it stops "
        "applying by itself when that logic changes. Pass the flag alone for "
        "concordance-decisions.jsonl in the current directory.",
    )
    p.add_argument(
        "--decisions-reset-on-restart",
        action="store_true",
        help="declare that the --decisions file does not survive a restart, so "
        "the review queue says so before anyone signs anything off. Set it when "
        "the log sits on a container with no mounted volume: nothing can detect "
        "that from a path, and a reviewer who is not told assumes their decision "
        "was kept.",
    )
    p.add_argument(
        "--no-upload",
        action="store_true",
        help="refuse models uploaded through the browser. By default a visitor "
        "may drop in their own .pbix, or a .zip of a .pbip / .SemanticModel "
        "folder, and it is read for their browser session alone -- never "
        "written to disk, never visible to anyone else, and gone when they "
        "leave. Pass this on a server that should read only the models it was "
        "started with.",
    )
    p.add_argument(
        "--users",
        metavar="PATH",
        help='a JSON file of {"name": "token"}, one token per reviewer. With '
        "it, the server resolves each request's token to a person and records "
        "that name against their review decisions as an authenticated fact "
        "rather than a claim. Implies access control: every request must "
        "carry a token the file knows.",
    )
    p.add_argument(
        "--auth0-domain",
        metavar="TENANT",
        help="an Auth0 tenant, e.g. acme.eu.auth0.com. Enables sign-in, account "
        "creation and Google through Auth0's Universal Login, and verifies every "
        "request's access token against the tenant's published signing keys. "
        "Requires --auth0-audience and --auth0-client-id.",
    )
    p.add_argument(
        "--auth0-audience",
        metavar="API",
        help="the Auth0 API identifier this server accepts tokens for. Without "
        "one Auth0 issues an opaque token rather than a JWT, and nothing can "
        "verify it.",
    )
    p.add_argument(
        "--auth0-client-id",
        metavar="ID",
        help="the Auth0 single-page application client id. Public by design -- "
        "it is handed to every browser. Never pass a client secret: a browser "
        "app cannot hold one.",
    )
    p.add_argument(
        "--token",
        nargs="?",
        const=_MINT_TOKEN_SENTINEL,
        default="",
        help="require an access token on every request. Pass a value to choose "
        "one, or use the flag alone to have one generated. Off by default, "
        "which is safe on loopback and not safe once --host is opened up. "
        "Grants access without identifying anyone; see --users for that.",
    )
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("document", help="generate a BRD or FRD from a model")
    p.add_argument("source", help="path to a .pbix file")
    p.add_argument("--type", choices=["brd", "frd"], default="brd",
                   help="business (brd) or functional (frd) requirements")
    p.add_argument("--format", choices=["md", "docx"], default="md",
                   help="Markdown, or Word for circulation and sign-off")
    p.add_argument("-o", "--out", help="output path (default data/out/<name>.<type>.md)")
    p.add_argument(
        "--sql",
        action="store_true",
        help="include each measure's SQL alongside its DAX (FRD only). Without "
             "--sql-grain the query is the whole-model figure, which is one row",
    )
    p.add_argument(
        "--sql-grain",
        action="append",
        metavar="TABLE[COLUMN]",
        help="group the generated SQL by this column, e.g. --sql-grain "
             "'Site[SiteName]'. Repeatable. Naming a grain is what gives a "
             "measure a single SQL translation at all, since GROUP BY is the "
             "filter context DAX supplies from the visual. Implies --sql",
    )
    p.add_argument(
        "--sql-dialect",
        default="duckdb",
        choices=["duckdb", "snowflake", "databricks", "redshift", "athena"],
        help="which warehouse the generated SQL should be written for",
    )
    p.add_argument(
        "--compare-to",
        metavar="PATH",
        help="a previous version of this model. Adds a 'What changed since' "
             "section saying which definitions moved and what that means for "
             "the requirements bound to them",
    )
    p.set_defaults(func=cmd_document)

    parser.add_argument(
        "--debug",
        action="store_true",
        help="show the full traceback instead of a summarised error",
    )

    args = parser.parse_args(argv)

    # One boundary for every command. A model that cannot be read, or a
    # provider that cannot be configured, is an ordinary outcome of pointing
    # this at the wrong thing -- not a crash, and not something a user should
    # have to read a stack trace to understand. The trace stays one flag away.
    from concordance.adapters.base import SourceError
    from concordance.llm.base import LlmError

    try:
        return args.func(args)
    except SourceError as error:
        if args.debug:
            raise
        print(f"\n{error.render()}\n", file=sys.stderr)
        return 2
    except LlmError as error:
        if args.debug:
            raise
        print(f"\n{error}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
