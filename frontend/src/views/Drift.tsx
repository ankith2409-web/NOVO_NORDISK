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
import { useEffect, useState } from "react";
import { api, type DriftChange, type DriftPayload } from "@/lib/api";
import {
  Chip,
  ConfidenceChip,
  Empty,
  Failure,
  Loading,
  Panel,
  Stat,
} from "@/components/primitives";
import { RichText } from "@/components/RichText";
import { cx } from "@/lib/cx";

const TONE = {
  added: "ok",
  removed: "bad",
  changed: "review",
  // Not a warning colour: a rename is proof that nothing needs re-checking.
  renamed: "accent",
} as const;

export function Drift() {
  const [data, setData] = useState<DriftPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const result = await api.drift();
      if (result.ok) setData(result.data);
      else setError(result.message);
    })();
  }, []);

  if (error) return <Failure message={error} />;
  if (!data) return <Loading what="both versions" />;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header>
        <h1 className="font-serif text-2xl leading-tight font-semibold">Drift</h1>
        <p className="mt-1 font-mono text-xs text-faint">
          {data.before} → {data.after}
        </p>
      </header>

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
          <section className="flex flex-col gap-1.5">
            <h2 className="text-sm font-semibold">
              What moved{" "}
              <span className="font-mono text-xs font-normal text-faint">
                ({data.changes.length})
              </span>
            </h2>
            {data.changes.map((change) => (
              <Change key={change.node_id} change={change} />
            ))}
          </section>

          {/* Split deliberately. Listing a rename beside a changed filter under
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
        <Side label="before" side={change.before} />
        <Side label="after" side={change.after} />
      </div>
    </article>
  );
}

function Side({
  label,
  side,
}: {
  label: string;
  side: { short_fingerprint: string; detail: string } | null;
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
      {side ? (
        <pre className="overflow-x-auto rounded border border-hairline bg-surface px-2.5 py-2 font-mono text-[11.5px] whitespace-pre-wrap">
          {side.detail || "—"}
        </pre>
      ) : (
        <p className="text-xs text-muted">did not exist</p>
      )}
    </div>
  );
}
