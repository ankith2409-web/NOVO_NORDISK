/**
 * The first screen, and the one that sets the tone.
 *
 * It leads with what the extractor could *not* read. That is unusual, and
 * deliberate: a tool that opens by admitting its blind spots earns the benefit
 * of the doubt on everything after, and a limitation is only honest if it is as
 * easy to find as the headline numbers. Most tools bury this; here it sits above
 * the fold whenever there is something to say.
 */
import { api, type Overview as OverviewData } from "@/lib/api";
import { Chip, Empty, Failure, Loading, Panel, Stat } from "@/components/primitives";
import { RichText } from "@/components/RichText";
import { useLoad } from "@/lib/useLoad";

export function Overview({ overview }: { overview: OverviewData | null }) {
  // The overview itself is fetched once by the shell and passed in; only the
  // review queue is this view's own.
  const { data: review, error, reload } = useLoad(() => api.review(), []);

  const data = overview;
  if (!data)
    return error ? (
      <Failure
        message={error.message}
        status={error.status}
        what="the model"
        onRetry={reload}
      />
    ) : (
      <Loading what="the model" />
    );

  const gaps = data.not_extracted;
  const unresolved = data.unresolved_references;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header>
        <h1 className="font-serif text-2xl leading-tight font-semibold">{data.model}</h1>
        <p className="mt-1 font-mono text-xs text-faint">
          {data.source_format} · {data.user_tables} user tables of {data.tables}
        </p>
      </header>

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Tables" value={data.user_tables} />
        <Stat label="Columns" value={data.columns} />
        <Stat label="Measures" value={data.measures} />
        <Stat label="Joins" value={data.relationships} />
        <Stat label="Hierarchies" value={data.user_hierarchies} />
        <Stat
          label="Needs review"
          value={review ? review.count : "—"}
          tone={review && review.count > 0 ? "review" : "neutral"}
          hint="Low-confidence statements awaiting a person"
        />
      </div>

      {/* Limits first. If there is nothing to declare, say that too -- silence
          here would be indistinguishable from not having checked.

          Two different admissions share this panel, and the chips keep them
          apart: a coverage gap is a feature this adapter cannot read at all,
          while an unresolved reference is a pointer in the model that names
          something not present. Collapsing them into one word would misreport
          both. */}
      <Panel title="What was not read or resolved">
        {gaps.length === 0 && unresolved.length === 0 ? (
          <Empty>
            Everything this adapter understands was read, and every reference in the model
            resolved to an object that exists.
          </Empty>
        ) : (
          <ul className="divide-y divide-hairline">
            {gaps.map((gap) => (
              <li
                key={gap.feature}
                className="flex items-center justify-between gap-3 px-3.5 py-2.5"
              >
                <span className="text-sm">{gap.feature}</span>
                <Chip tone="review">{gap.count} not read</Chip>
              </li>
            ))}
            {unresolved.map((reference) => (
              <li key={`${reference.from}->${reference.to}`} className="px-3.5 py-2.5">
                <div className="flex items-center justify-between gap-3">
                  <code className="font-mono text-xs">
                    {reference.from} → {reference.to}
                  </code>
                  <Chip tone="review">unresolved</Chip>
                </div>
                <p className="mt-1 text-xs text-muted">{reference.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {review && review.count > 0 && (
        <Panel title={`Awaiting confirmation (${review.count})`}>
          <ul className="divide-y divide-hairline">
            {review.pending.slice(0, 6).map((requirement) => (
              <li key={requirement.id} className="px-3.5 py-2.5">
                <div className="flex items-baseline gap-2">
                  <code className="font-mono text-[11px] text-faint">{requirement.id}</code>
                  <span className="text-sm">
                    <RichText>{requirement.statement}</RichText>
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted">
                  <RichText>{requirement.rationale}</RichText>
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <p className="font-mono text-[11px] text-faint">
        drift {data.capabilities.drift ? "configured" : "not configured"} · reconcile{" "}
        {data.capabilities.reconcile ? "configured" : "not configured"}
      </p>
    </div>
  );
}
