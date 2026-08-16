/**
 * What moved between two versions, and which requirements that puts in question.
 *
 * The second half is the point. Listing changed objects is what any diff does;
 * naming the requirements whose evidence sits on those objects is what turns a
 * diff into "this document may no longer describe this model". Requirement ids
 * are derived from an object's identity rather than its content, so the same id
 * survives the change and the report can point at it.
 *
 * A change is shown with the fingerprint on both sides and the text each was
 * taken over, because "the hash moved" is not a finding a reviewer can act on.
 */
import { useState } from "react";
import { api, SNAPSHOT_MODE, type DriftChange, type DriftPayload } from "@/lib/api";
import { Button, Chip, ConfidenceChip, Empty, Failure, Loading, Panel, Stat } from "@/components/primitives";
import { NotConfigured, SnapshotGap } from "@/components/NotConfigured";
import { RichText } from "@/components/RichText";
import { Summary } from "@/components/Summary";
import { cx } from "@/lib/cx";
import { diffLines, worthMarking, type DiffLine } from "@/lib/linediff";
import { useLoad } from "@/lib/useLoad";

/** Most costly to overlook first. */
const SEVERITY: Record<DriftChange["kind"], number> = {
  removed: 0,
  changed: 1,
  added: 2,
  renamed: 3,
};

const KINDS: (DriftChange["kind"] | null)[] = [
  null,
  "removed",
  "changed",
  "added",
  "renamed",
];

const TONE = {
  added: "ok",
  removed: "bad",
  changed: "review",
  // Not a warning colour: a rename is proof that nothing needs re-checking.
  renamed: "accent",
} as const;

export function Drift({ alsoOn = [] }: { alsoOn?: string[] } = {}) {
  const { data, error, reload } = useLoad<DriftPayload>(() => api.drift(), []);
  const [only, setOnly] = useState<DriftChange["kind"] | null>(null);

  // 501 is the server saying a flag was not passed, which is not a failure and
  // must not be dressed as one. Anything else genuinely went wrong.
  if (error?.status === 501 && SNAPSHOT_MODE)
    return <SnapshotGap title='Drift' why='Drift compares two versions of a model against each other as they are read, so there is no single capture that could hold it.' />;
  if (error?.status === 501)
    return (
      <NotConfigured
        title="Drift"
        answers="Which objects moved between two versions of this model, and which requirements were written against the ones that moved."
        needs="a second version of this model to compare against"
        flag="--compare-to"
        example="<earlier .pbix or TMDL folder>"
        yields="Every added, removed, changed and renamed object, with the fingerprint on both sides — and separately, the requirements that need re-validating versus the ones where only a name they cite has moved."
        alsoOn={alsoOn}
      />
    );
  if (error)
    return (
      <Failure
        message={error.message}
        status={error.status}
        what="the drift report"
        onRetry={reload}
      />
    );
  if (!data) return <Loading what="both versions" rows={4} />;

  // Ordered by what it costs a reader to miss it: a removal breaks whatever
  // referenced it, a change silently alters a number, an addition adds
  // something nobody has reviewed, and a rename is proven not to have changed
  // anything at all. Alphabetical within a kind, so a second run of the same
  // comparison lists them in the same order.
  const ordered = [...data.changes].sort(
    (a, b) => SEVERITY[a.kind] - SEVERITY[b.kind] || a.node_id.localeCompare(b.node_id),
  );
  const present = new Set(ordered.map((change) => change.kind));
  const shown = only ? ordered.filter((change) => change.kind === only) : ordered;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header>
        <h1 className="font-serif text-2xl leading-tight font-semibold">Drift</h1>
        <p className="mt-1 font-mono text-xs text-faint">
          {data.before} → {data.after}
        </p>
      </header>

      {data.has_drift && !SNAPSHOT_MODE && (
        <Summary fetch={api.driftSummary} />
      )}

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 xl:grid-cols-7">
        <Stat label="Added" value={data.counts.added} />
        <Stat
          label="Removed"
          value={data.counts.removed}
          tone={data.counts.removed > 0 ? "bad" : "neutral"}
        />
        <Stat
          label="Changed"
          value={data.counts.changed}
          tone={data.counts.changed > 0 ? "review" : "neutral"}
        />
        <Stat
          label="Renamed"
          value={data.counts.renamed}
          hint="Same logic under a new name — proven by an identical fingerprint"
        />
        <Stat label="Unchanged" value={data.counts.unchanged} />
        <Stat
          label="Need re-validation"
          value={data.counts.needing_revalidation}
          tone={data.counts.needing_revalidation > 0 ? "review" : "neutral"}
          hint="Requirements resting on something that changed what it computes"
        />
        <Stat
          label="Name update only"
          value={data.counts.reference_updates_only}
          hint="Requirements whose logic is unchanged; only the name they cite moved"
        />
      </div>

      {!data.has_drift ? (
        <Empty>
          Nothing moved. Every object carries the fingerprint it had before, so every
          requirement still describes what it was written against.
        </Empty>
      ) : (
        <>
          {/* The consequences lead. A list of moved objects is what any diff
              produces; which requirements no longer describe the model is the
              part someone has to act on, and it does not belong below a wall
              of DAX where it has to be scrolled to.

              Split deliberately. Listing a rename beside a changed filter under
              one "in question" heading is what makes a reviewer re-check work
              that is provably untouched -- the exact busywork this removes. */}
          <Consequences
            title="Requirements now in question"
            empty="Nothing changed that any requirement was written against."
            items={data.affected_requirements.filter((a) => a.needs_revalidation)}
            tone="text-review"
          />
          <Consequences
            title="Requirements needing only a name update — logic unchanged"
            empty=""
            items={data.affected_requirements.filter((a) => !a.needs_revalidation)}
            tone="text-accent"
          />

          <section className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
              <h2 className="text-sm font-semibold">
                What moved{" "}
                <span className="font-mono text-xs font-normal text-faint">
                  ({shown.length === data.changes.length
                    ? data.changes.length
                    : `${shown.length} of ${data.changes.length}`})
                </span>
              </h2>
              <div className="flex flex-wrap gap-1">
                {KINDS.filter((k) => k === null || present.has(k)).map((kind) => (
                  <Button
                    key={kind ?? "all"}
                    onClick={() => setOnly(kind)}
                    aria-pressed={only === kind}
                    tone={only === kind ? "selected" : "quiet"}
                  >
                    {kind ?? "all"}
                    {/* Inherits the button's own colour rather than pinning
                        `text-faint`. Fixed to the token it failed at 3.85:1 on
                        the selected button's accent background in dark mode,
                        and dimming by opacity fails worse in light mode -- the
                        count's subordinacy comes from its position, which
                        costs no contrast at all. */}
                    <span className="ml-1">
                      {kind === null
                        ? data.changes.length
                        : data.changes.filter((c) => c.kind === kind).length}
                    </span>
                  </Button>
                ))}
              </div>
            </div>
            {shown.map((change) => (
              <Change key={change.node_id} change={change} />
            ))}
          </section>
        </>
      )}
    </div>
  );
}

function Consequences({
  title,
  empty,
  items,
  tone,
}: {
  title: string;
  empty: string;
  items: DriftPayload["affected_requirements"];
  tone: string;
}) {
  if (items.length === 0 && !empty) return null;
  return (
    <Panel title={`${title} (${items.length})`}>
      {items.length === 0 ? (
        <Empty>{empty}</Empty>
      ) : (
        <ul className="divide-y divide-hairline">
          {items.map(({ requirement, because }) => (
            <li key={requirement.id} className="px-3.5 py-2.5">
              <div className="flex items-start gap-2.5">
                <code className="mt-0.5 flex-none font-mono text-[11px] text-faint">
                  {requirement.id}
                </code>
                <p className="min-w-0 flex-1 text-sm">
                  <RichText>{requirement.statement}</RichText>
                </p>
                <ConfidenceChip level={requirement.confidence} />
              </div>
              <p className={cx("mt-1 pl-[4.5rem] font-mono text-[11px]", tone)}>
                because {because.join("; ")}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}


function Change({ change }: { change: DriftChange }) {
  // Only worth aligning when both sides exist -- an addition and a removal have
  // nothing to align against, and marking every line of one side as new says
  // nothing the "added" chip has not already said.
  const marks =
    change.before && change.after
      ? diffLines(change.before.detail, change.after.detail)
      : null;
  const marked = marks && worthMarking(marks) ? marks : null;

  return (
    <article className="rounded border border-hairline bg-ground">
      <header className="flex items-center gap-2 border-b border-hairline px-3.5 py-2">
        <Chip tone={TONE[change.kind]}>{change.kind}</Chip>
        <code className="min-w-0 truncate font-mono text-xs">{change.node_id}</code>
        <span className="ml-auto flex-none font-mono text-[10px] text-faint">
          {change.object_kind}
        </span>
      </header>

      <div className="grid divide-y divide-hairline sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <Side label="before" side={change.before} lines={marked?.before} />
        <Side label="after" side={change.after} lines={marked?.after} />
      </div>
    </article>
  );
}

function Side({
  label,
  side,
  lines,
}: {
  label: string;
  side: { short_fingerprint: string; detail: string } | null;
  /** Line alignment against the other side, when there is one worth showing. */
  lines?: DiffLine[];
}) {
  return (
    <div className={cx("min-w-0 p-3", !side && "opacity-60")}>
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
          {label}
        </span>
        {side && (
          <code className="font-mono text-[10px] text-faint">{side.short_fingerprint}</code>
        )}
      </div>
      {side && lines ? (
        <pre className="overflow-x-auto rounded border border-hairline bg-surface py-2 font-mono text-[11.5px] whitespace-pre-wrap">
          {lines.map((line, index) => (
            <div
              key={index}
              className={cx(
                "px-2.5",
                line.tag === "removed" && "bg-bad-soft text-bad",
                line.tag === "added" && "bg-ok-soft text-ok",
              )}
            >
              {line.text || "\u00a0"}
            </div>
          ))}
        </pre>
      ) : side ? (
        <pre className="overflow-x-auto rounded border border-hairline bg-surface px-2.5 py-2 font-mono text-[11.5px] whitespace-pre-wrap">
          {side.detail || "—"}
        </pre>
      ) : (
        <p className="text-xs text-muted">did not exist</p>
      )}
    </div>
  );
}
