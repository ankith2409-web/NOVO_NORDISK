/**
 * Whether the warehouse agrees with the model, metric by metric.
 *
 * The comparison cannot work by fingerprint, and the interface has to make that
 * visible rather than hide it. DAX and SQL never hash alike even when they
 * compute the same number, so what is compared is what each definition
 * structurally reads -- tables, columns, aggregations. Showing those side by
 * side, with the differing ones marked, is the argument; the verdict is just its
 * summary.
 *
 * Hence three verdicts rather than a pass/fail. Deciding whether two arbitrary
 * expressions in two languages compute the same number is undecidable in
 * general, so "needs review" is not hedging -- it is the honest answer for a
 * difference that may or may not change the number, and collapsing it into
 * either neighbour would misreport it.
 */
import { useState } from "react";
import {
  api,
  SNAPSHOT_MODE,
  type Comparison,
  type MetricDefinition,
  type ReconcilePayload,
  type Verdict,
} from "@/lib/api";
import { NotConfigured, SnapshotGap } from "@/components/NotConfigured";
import { Summary } from "@/components/Summary";
import { Button, Chip, Failure, Loading, Panel, Stat, VerdictChip } from "@/components/primitives";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";
import { FEATURE } from "@/lib/naming";

const HEADING: Record<Verdict, string> = {
  divergent: "Divergent — these will report different numbers",
  review: "Needs review — differs, but may still agree",
  consistent: "Consistent",
};

const ORDER: Verdict[] = ["divergent", "review", "consistent"];

export function Reconcile({ alsoOn = [] }: { alsoOn?: string[] } = {}) {
  const { data, error, retrying, reload } = useLoad<ReconcilePayload>(() => api.reconcile(), []);
  const [showConsistent, setShowConsistent] = useState(false);

  if (error?.status === 501 && SNAPSHOT_MODE)
    return <SnapshotGap title={FEATURE.reconcile.heading} why='Reconciliation reads a live warehouse alongside the model, so it cannot be captured into a static page.' />;
  if (error?.status === 501)
    return (
      <NotConfigured
        title={FEATURE.reconcile.heading}
        answers="Whether the numbers defined in Power BI and the ones defined in the warehouse actually agree — metric by metric, not model-wide."
        needs="a warehouse to read the other definitions from"
        flag="--warehouse"
        example="path/to/warehouse.duckdb"
        yields="Each metric matched to its warehouse counterpart, with the tables, columns and aggregations each side reads shown next to one another and the differing ones marked."
        alsoOn={alsoOn}
      />
    );
  if (error)
    return (
      <Failure
        message={error.message}
        status={error.status}
        what={FEATURE.reconcile.subject}
        onRetry={reload}
        retrying={retrying}
      />
    );
  if (!data) return <Loading what="both platforms" rows={4} />;

  const counts = data.counts;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header>
        <h1 className="font-serif text-2xl leading-tight font-semibold">
          {FEATURE.reconcile.heading}
        </h1>
        <p className="mt-1 font-mono text-xs text-faint">
          {data.model} against {data.warehouse}
        </p>
      </header>

      {!SNAPSHOT_MODE && <Summary fetch={api.reconcileSummary} />}

      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        <Stat label="Defined on both" value={counts.shared_metrics} />
        <Stat
          label="Divergent"
          value={counts.divergent}
          tone={counts.divergent > 0 ? "bad" : "neutral"}
        />
        <Stat
          label="Needs review"
          value={counts.review}
          tone={counts.review > 0 ? "review" : "neutral"}
        />
        <Stat label="Consistent" value={counts.consistent} />
      </div>

      {ORDER.map((verdict) => {
        const items = data.comparisons.filter((c) => c.verdict === verdict);
        if (items.length === 0) return null;
        // The consistent ones are folded away. They are usually the majority,
        // each is two expressions and three lists tall, and between them they
        // push the two verdicts that need a decision off the screen. Folded,
        // not dropped: "these four agree" is a finding, and the evidence for it
        // has to stay one click away.
        const foldable = verdict === "consistent";
        const open = !foldable || showConsistent;
        return (
          <section key={verdict} className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold">
              {HEADING[verdict]}{" "}
              <span className="font-mono text-xs font-normal text-faint">({items.length})</span>
              {foldable && (
                <Button
                  onClick={() => setShowConsistent((shown) => !shown)}
                  aria-expanded={open}
                  className="ml-2 font-normal"
                >
                  {open ? "hide" : "show"}
                </Button>
              )}
            </h2>
            {open &&
              items.map((comparison) => (
                <Metric key={comparison.metric} comparison={comparison} />
              ))}
            {!open && (
              <p className="font-mono text-xs text-faint">
                {items.map((c) => c.metric).join(" · ")}
              </p>
            )}
          </section>
        );
      })}

      {data.possible_pairings.length > 0 && (
        <Panel title="Possibly the same metric under different names">
          <p className="px-3.5 pt-2.5 text-xs text-muted">
            Questions, never findings — none of these is compared automatically. A
            name score alone cannot tell a synonym from an antonym, so each one shows
            what the two definitions actually read, which is usually enough to settle
            it without opening either.
          </p>
          <ul className="mt-1.5 divide-y divide-hairline">
            {data.possible_pairings.map((pairing) => (
              <li key={`${pairing.left}~${pairing.right}`} className="px-3.5 py-2">
                <div className="flex items-center gap-2 font-mono text-xs">
                  <span>{pairing.left}</span>
                  <span className="text-faint">~</span>
                  <span>{pairing.right}</span>
                  <span className="ml-auto flex flex-none items-center gap-1.5">
                    {/* Named, because "found by what they read" and "found by
                        spelling" are different strengths of claim and a reader
                        deserves to know which one they are looking at. */}
                    <Chip tone={pairing.contradicted ? "bad" : pairing.basis === "name" ? "neutral" : "accent"}>
                      {pairing.basis}
                    </Chip>
                    <span className="tabular text-faint">
                      name {Math.round(pairing.similarity * 100)}%
                    </span>
                  </span>
                </div>
                <p
                  className={cx(
                    "mt-1 text-[11.5px]",
                    pairing.contradicted ? "text-bad" : "text-muted",
                  )}
                >
                  {pairing.evidence}
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        {Object.entries(data.unique_to_platform).map(([platform, names]) => (
          <Panel key={platform} title={`Only in ${platform} (${names.length})`}>
            <ul className="divide-y divide-hairline">
              {names.map((name) => (
                <li key={name} className="px-3.5 py-1.5 font-mono text-[11.5px] text-muted">
                  {name}
                </li>
              ))}
            </ul>
          </Panel>
        ))}
      </div>

      {data.coverage_gaps.length > 0 && (
        <Panel title="Not read from the warehouse">
          <ul className="divide-y divide-hairline">
            {data.coverage_gaps.map((gap) => (
              <li key={gap.feature} className="px-3.5 py-2.5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm">{gap.feature}</span>
                  <Chip tone="review">{gap.count}</Chip>
                </div>
                <p className="mt-1 text-xs text-muted">{gap.reason}</p>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function Metric({ comparison }: { comparison: Comparison }) {
  const [left, right] = comparison.definitions;

  // Marking only what actually differs is what turns two definitions into an
  // argument. Everything shared stays quiet.
  const differing = {
    tables: !same(left?.tables, right?.tables),
    columns: !same(left?.columns, right?.columns),
    aggregations: !same(left?.aggregations, right?.aggregations),
  };

  return (
    <article className="rounded border border-hairline bg-ground">
      <header className="flex items-center gap-2 border-b border-hairline px-3.5 py-2">
        <h3 className="text-sm font-semibold">{comparison.metric}</h3>
        <VerdictChip verdict={comparison.verdict} />
      </header>

      <div className="grid divide-y divide-hairline lg:grid-cols-2 lg:divide-x lg:divide-y-0">
        {comparison.definitions.map((definition) => (
          <Definition
            key={definition.platform}
            definition={definition}
            differing={differing}
          />
        ))}
      </div>

      {comparison.differences.length > 0 && (
        <ul className="border-t border-hairline">
          {comparison.differences.map((difference) => (
            <li key={difference.aspect} className="px-3.5 py-2 text-xs">
              <span className="font-mono text-[11px] text-bad">{difference.aspect}</span>
              <span className="ml-2 text-muted">{difference.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

function Definition({
  definition,
  differing,
}: {
  definition: MetricDefinition;
  differing: { tables: boolean; columns: boolean; aggregations: boolean };
}) {
  return (
    <div className="min-w-0 p-3">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
          {definition.platform}
        </span>
        <Chip>{definition.language}</Chip>
      </div>

      <pre className="overflow-x-auto rounded border border-hairline bg-surface px-2.5 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
        {definition.expression}
      </pre>

      {/* A measure that calls other measures reads whatever they read. Saying
          which ones is what stops a table appearing from nowhere. */}
      {definition.resolved_through.length > 0 && (
        <p className="mt-1.5 font-mono text-[10px] text-faint">
          via {definition.resolved_through.join(", ")}
        </p>
      )}

      <dl className="mt-2 space-y-1.5">
        <Facet label="reads tables" values={definition.tables} differs={differing.tables} />
        <Facet label="reads columns" values={definition.columns} differs={differing.columns} />
        <Facet
          label="aggregates"
          values={definition.aggregations}
          differs={differing.aggregations}
        />
      </dl>
    </div>
  );
}

function Facet({
  label,
  values,
  differs,
}: {
  label: string;
  values: string[];
  differs: boolean;
}) {
  return (
    <div className="flex gap-2 text-[11px]">
      <dt className="w-20 flex-none font-mono text-[10px] text-faint">{label}</dt>
      <dd className="flex min-w-0 flex-wrap gap-1">
        {values.length === 0 ? (
          <span className="font-mono text-[10px] text-faint">none identifiable</span>
        ) : (
          values.map((value) => (
            <span
              key={value}
              className={cx(
                "rounded px-1 py-0.5 font-mono text-[10px]",
                differs ? "bg-bad-soft text-bad" : "bg-raised text-muted",
              )}
            >
              {value}
            </span>
          ))
        )}
      </dd>
    </div>
  );
}

function same(a: string[] | undefined, b: string[] | undefined): boolean {
  if (!a || !b) return true;
  // Compared the way the server compares them: name only, case and separators
  // ignored, since DAX writes TestResult[ResultStatus] where SQL writes
  // result_status.
  const bare = (values: string[]) =>
    new Set(values.map((v) => v.split(".").pop()!.toLowerCase().replace(/[^a-z0-9]/g, "")));
  const left = bare(a);
  const right = bare(b);
  return left.size === right.size && [...left].every((value) => right.has(value));
}
