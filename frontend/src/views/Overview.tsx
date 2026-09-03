/**
 * The first screen, and the one that sets the tone.
 *
 * Two people open this tool and they want opposite things. A reviewer or
 * analyst wants to know what the model reports on, what its headline figures
 * are, and where the written requirements are. Whoever maintains the model
 * wants the definitions: the DAX, the SQL, the joins, and what does not
 * resolve. Sending both of them to the same wall of counts and hoping they
 * work out which tab is theirs is how a tool gets called complicated.
 *
 * So the page says what this is, then offers each of them a way in by name.
 * The links are the same four tabs that were always in the rail -- what is new
 * is that they are described by the question they answer rather than by what
 * they are called.
 *
 * What could not be read is still here and still unhidden, because a tool that
 * admits its blind spots earns the benefit of the doubt on everything else. It
 * is no longer the first thing on the page: leading with a list of gaps meant
 * the first impression of a model that read perfectly well was a column of
 * warnings.
 */
import { useState } from "react";
import { api, type Overview as OverviewData } from "@/lib/api";
import { Chip, Failure, Loading, Panel, Stat } from "@/components/primitives";
import { ChevronIcon } from "@/components/icons";
import { RichText } from "@/components/RichText";
import { Bars } from "@/components/Chart";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";
import { FEATURE } from "@/lib/naming";

/**
 * Where to go next, in the words of the question rather than the tab.
 *
 * Written for two readers on purpose. The first three answer "what does this
 * report say"; the last three answer "how is it built". Nobody has to be told
 * which group they are in -- the questions do that.
 */
const WAYS_IN: {
  view: string;
  title: string;
  says: string;
  audience: "reading" | "building";
}[] = [
  {
    view: "dashboard",
    title: "What are the KPIs?",
    says: "Every figure the report states as a number, with the formula behind it.",
    audience: "reading",
  },
  {
    view: "requirements",
    title: "Where are the BRD and FRD?",
    says: "The business and functional requirements, derived from the model and ready to download.",
    audience: "reading",
  },
  {
    view: "review",
    title: "What still needs a person?",
    says: "Statements the model could not settle on its own, waiting to be confirmed.",
    audience: "reading",
  },
  {
    view: "dataset",
    title: "What does it calculate?",
    says: "Tables, joins, and every measure as DAX beside the same thing written in SQL.",
    audience: "building",
  },
  {
    view: "model",
    title: "How is one object defined?",
    says: "Any table, column, measure or drill path, with what depends on it.",
    audience: "building",
  },
  {
    view: "drift",
    title: "What changed between versions?",
    says: "Two versions of the same model, compared object by object.",
    audience: "building",
  },
];

export function Overview({
  overview,
  onGo,
}: {
  overview: OverviewData | null;
  /** Navigate to a view. The cards below are the only reason this exists. */
  onGo?: (view: string, target: string) => void;
}) {
  // The overview itself is fetched once by the shell and passed in; only the
  // review queue is this view's own.
  const { data: review, error, retrying, reload } = useLoad(() => api.review(), []);

  const data = overview;
  if (!data)
    return error ? (
      <Failure
        message={error.message}
        status={error.status}
        what="the model"
        onRetry={reload}
        retrying={retrying}
      />
    ) : (
      <Loading what="the model" />
    );

  const gaps = data.not_extracted;
  const unresolved = data.unresolved_references;
  const clean = gaps.length === 0 && unresolved.length === 0;

  return (
    <div className="flex flex-col gap-5 p-4">
      <header>
        <h1 className="font-serif text-2xl leading-tight font-semibold">{data.model}</h1>
        {/* One sentence, in words with no Power BI in them. A reviewer asked
            twice for "simple language", and a page that opens on a format name
            and a table ratio is written for whoever built it. */}
        <p className="mt-1.5 max-w-prose text-sm text-muted">
          This Power BI model has been read end to end — every table, join, measure and
          formula in it. Below is what it holds, and where to go for the part you came
          for.
        </p>
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

      {data.by_table.length > 0 && (
        <section className="flex flex-col gap-2.5">
          <h2 className="font-serif text-lg font-semibold">Where the weight sits</h2>
          <div className="grid gap-5 rounded border border-hairline bg-ground p-3.5 md:grid-cols-2">
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">Calculations, by table</h3>
              <Bars
                unit="measures"
                rows={data.by_table
                  .filter((t) => t.measures > 0)
                  .map((t) => ({ label: t.name, value: t.measures }))}
                caption="Which tables the model's logic is written on. A table with none holds data that other tables calculate from."
              />
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">Columns, by table</h3>
              <Bars
                unit="columns"
                rows={data.by_table.map((t) => ({ label: t.name, value: t.columns }))}
                caption="How much each table holds. The widest is usually the one everything else joins to."
              />
            </div>
          </div>
        </section>
      )}

      <section className="flex flex-col gap-2.5">
        <h2 className="font-serif text-lg font-semibold">Where to start</h2>
        <p className="max-w-prose text-[13px] text-muted">
          Or press <kbd className="rounded border border-hairline px-1 font-mono text-[11px]">/</kbd>{" "}
          anywhere to search the whole model by name — measures, tables, columns,
          drill paths and the tiles on the report, all at once.
        </p>
        <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
          {/* Drift is offered even where a server was not given the second
              version it needs. The page it opens explains what to pass, and a
              capability nobody discovers is a capability nobody has. */}
          {WAYS_IN.map((way) => (
            <button
              key={way.view}
              type="button"
              onClick={() => onGo?.(way.view, "")}
              disabled={!onGo}
              className={cx(
                "group flex flex-col gap-1 rounded border border-hairline bg-ground p-3 text-left",
                "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
                "hover:border-accent hover:bg-accent-soft disabled:cursor-default disabled:hover:border-hairline disabled:hover:bg-ground",
              )}
            >
              <span className="flex items-center gap-1.5">
                <span className="text-sm font-semibold">{way.title}</span>
                <ChevronIcon
                  size={12}
                  className="flex-none text-faint transition-transform duration-(--duration-feedback) group-hover:translate-x-0.5"
                />
              </span>
              <span className="text-[12.5px] leading-snug text-muted">{way.says}</span>
              <span className="mt-0.5 font-mono text-[10px] tracking-[0.06em] text-faint uppercase">
                {way.audience === "reading" ? "reading it" : "building it"}
              </span>
            </button>
          ))}
        </div>
      </section>

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

      <NotRead gaps={gaps} unresolved={unresolved} clean={clean} />

      {/* Lower-cased so it reads as a status line rather than a heading, but
          named the same as the tabs -- a reader who sees "reconcile" here and
          "Warehouse check" in the rail has to work out that they are the same
          thing. */}
      <p className="font-mono text-[11px] text-faint">
        {FEATURE.drift.tab.toLowerCase()}{" "}
        {data.capabilities.drift ? "configured" : "not configured"} ·{" "}
        {FEATURE.reconcile.tab.toLowerCase()}{" "}
        {data.capabilities.reconcile ? "configured" : "not configured"}
      </p>
    </div>
  );
}

/**
 * What could not be read, folded away when there is nothing to say.
 *
 * Two different admissions share this panel, and the chips keep them apart: a
 * coverage gap is a feature this adapter cannot read at all, while an
 * unresolved reference is a pointer in the model that names something not
 * present. Collapsing them into one word would misreport both.
 *
 * Closed by default when there is something in it. That is a deliberate second
 * thought rather than a retreat: the heading states the count, so the fact is
 * on screen either way, and eight near-identical rows of the same finding used
 * to fill the page below the fold on a model that had read correctly. Open, it
 * says exactly as much as it ever did.
 */
function NotRead({
  gaps,
  unresolved,
  clean,
}: {
  gaps: OverviewData["not_extracted"];
  unresolved: OverviewData["unresolved_references"];
  clean: boolean;
}) {
  const [open, setOpen] = useState(false);
  const count = gaps.length + unresolved.length;

  if (clean)
    return (
      <section className="rounded border border-hairline bg-ground px-3.5 py-2.5">
        <p className="text-[13px] text-muted">
          <span className="font-medium text-ok">Nothing was missed.</span> Everything
          this adapter understands was read, and every reference in the model resolved
          to an object that exists.
        </p>
      </section>
    );

  return (
    <section className="rounded border border-hairline bg-ground">
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left"
      >
        <ChevronIcon
          size={12}
          className={cx(
            "flex-none text-faint transition-transform duration-(--duration-feedback)",
            open && "rotate-90",
          )}
        />
        <span className="text-sm font-semibold">What was not read or resolved</span>
        <Chip tone="review">{count}</Chip>
        <span className="ml-auto hidden text-[12px] text-muted sm:block">
          {open ? "hide" : "show what this file did not give up"}
        </span>
      </button>

      {open && (
        <ul className="divide-y divide-hairline border-t border-hairline">
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
                <code className="min-w-0 font-mono text-xs break-words">
                  {reference.from} → {reference.to}
                </code>
                <Chip tone="review">unresolved</Chip>
              </div>
              <p className="mt-1 text-xs text-muted">{reference.reason}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
