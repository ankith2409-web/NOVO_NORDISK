/**
 * The whole dataset on one page: every measure, its DAX, and its SQL.
 *
 * The Model tab answers "what is this one measure", which is the right shape
 * for a question you already know how to ask. It is the wrong shape for the
 * question people actually arrive with -- "what does this dataset compute, and
 * how would I get the same numbers myself" -- because answering that meant
 * opening each measure in turn and copying it out. This page answers it once.
 *
 * The grain selector is not a filter. SQL has no equivalent of DAX's filter
 * context, so a measure has no single SQL translation; naming the grain is what
 * makes one exist, because GROUP BY *is* the filter context written down.
 * Changing it regenerates every query on the page, which is why it sits at the
 * top rather than beside any one measure.
 */
import { useMemo, useState } from "react";
import {
  api,
  type DatasetJoin,
  type DatasetMeasure,
  type DatasetPayload,
  type DatasetTable,
} from "@/lib/api";
import {
  Button,
  Chip,
  controlClasses,
  Empty,
  Failure,
  Loading,
  Stat,
} from "@/components/primitives";
import { ChevronIcon, CopyIcon, DownloadIcon } from "@/components/icons";
import { SNAPSHOT_MODE } from "@/lib/api";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";
import { FEATURE } from "@/lib/naming";

/** The whole-model figure, which is a single row rather than an absence. */
const WHOLE_MODEL = "__whole__";

/** Power BI's cardinality notation, said out loud for the hover. */
function cardinalityInWords(cardinality: string): string {
  const said: Record<string, string> = {
    "M:1": "Many rows on the left match one row on the right",
    "1:M": "One row on the left matches many rows on the right",
    "1:1": "One row on each side matches exactly one on the other",
    "M:M": "Many rows on each side can match many on the other",
  };
  return said[cardinality] ?? cardinality;
}

export function Dataset() {
  const [grain, setGrain] = useState<string>(WHOLE_MODEL);
  const [dialect, setDialect] = useState("duckdb");
  const [onlyTranslated, setOnlyTranslated] = useState(false);
  const [copied, setCopied] = useState("");

  const grains = useMemo(
    () => (grain === WHOLE_MODEL ? [] : [grain]),
    [grain],
  );
  const { data, error, retrying, reload } = useLoad<DatasetPayload>(
    () => api.dataset(grains, dialect),
    [grain, dialect],
  );

  if (error)
    return (
      <Failure
        message={error.message}
        status={error.status}
        what={FEATURE.dataset.subject}
        onRetry={reload}
        retrying={retrying}
      />
    );
  if (!data) return <Loading what="every measure in this dataset" rows={6} />;

  const shown = onlyTranslated
    ? data.measures.filter((m) => m.status === "exact")
    : data.measures;

  /** Every query on the page, in one block, which is the point of the page. */
  const everything = data.measures
    .map((m) =>
      m.sql
        ? `-- ${m.measure}\n${m.sql};`
        : `-- ${m.measure}: no SQL — ${m.reason}`,
    )
    .join("\n\n");

  const copy = (text: string, token: string) => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(token);
      window.setTimeout(() => setCopied((c) => (c === token ? "" : c)), 1600);
    });
  };

  return (
    <div className="flex flex-col gap-4 p-3">
      <header className="flex flex-col gap-1">
        <h1 className="font-serif text-2xl font-bold">{FEATURE.dataset.heading}</h1>
        <p className="text-sm text-muted">
          Everything {data.model} calculates: the tables it holds, how they join, and
          each number as it is written in Power BI and as it would be written in SQL.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Stat label="measures" value={data.counts.measures} />
        <Stat label="with SQL" value={data.counts.translated} tone="ok" />
        <Stat
          label="no SQL possible"
          value={data.counts.blocked}
          tone={data.counts.blocked ? "review" : "neutral"}
          hint="Measures whose answer depends on what the report is showing, so no single query can stand for them."
        />
      </div>

      {/* The tables and how they join, above the measures rather than on
          another tab. Asked for as one question -- "what are the data sets and
          how it is joined with each other and what are the SQL" -- and it is
          above rather than below because a JOIN in the SQL further down means
          nothing until you know what it refers to. */}
      <Structure tables={data.tables} joins={data.joins} />

      {/* Controls. The grain changes what the SQL means, so it is labelled as
          a question rather than as a filter. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded border border-hairline bg-ground px-3.5 py-2.5">
        <label className="flex items-center gap-2 text-sm">
          <span className="font-mono text-[11px] tracking-wide text-faint uppercase">
            one row per
          </span>
          <select
            value={grain}
            onChange={(e) => setGrain(e.target.value)}
            className="min-h-8 rounded border border-hairline bg-ground px-2 py-1 text-sm"
          >
            <option value={WHOLE_MODEL}>everything, as one total</option>
            {data.grain_options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.table} · {o.column}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm">
          <span className="font-mono text-[11px] tracking-wide text-faint uppercase">
            written for
          </span>
          <select
            value={dialect}
            onChange={(e) => setDialect(e.target.value)}
            className="min-h-8 rounded border border-hairline bg-ground px-2 py-1 text-sm"
          >
            {data.dialects.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={onlyTranslated}
            onChange={(e) => setOnlyTranslated(e.target.checked)}
          />
          only those with SQL
        </label>

        <div className="ml-auto flex items-center gap-1.5">
          {/* The FRD is offered here rather than only on Requirements because
              the grain and dialect are chosen here: the document downloads
              with exactly the queries on screen, not a different set. */}
          {!SNAPSHOT_MODE && (
            <>
              <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                frd with this sql
              </span>
              {(
                [
                  { format: "md", label: ".md" },
                  { format: "docx", label: ".docx" },
                ] as const
              ).map(({ format, label }) => (
                <a
                  key={format}
                  href={api.documentUrl("functional", format, {
                    grain: grains,
                    dialect,
                  })}
                  download
                  className={controlClasses("quiet")}
                  title={`Download the FRD as ${label}, with every measure's SQL beneath its DAX`}
                >
                  <DownloadIcon size={11} className="flex-none" />
                  {label}
                </a>
              ))}
            </>
          )}
          <Button tone="primary" onClick={() => copy(everything, "all")}>
            <CopyIcon size={11} className="flex-none" />
            {copied === "all" ? "copied" : "copy every query"}
          </Button>
        </div>
      </div>

      {data.grain_options.length === 0 && data.counts.measures > 0 && (
        <p className="text-xs text-faint">
          This model has no relationships, so there is nothing to break the numbers
          down by. Every measure below is a single total across everything.
        </p>
      )}

      {/* A model can genuinely have no measures -- uploading a single exported
          table is now a supported thing to do, and one table is columns and no
          DAX. Without this the page was three zeroes, a row of controls that
          do nothing, and a note promising measures "below" that were not
          there. */}
      {data.counts.measures === 0 ? (
        <Empty>
          {data.model} defines no measures, so there is no DAX to translate. This
          page shows what a model computes; a model that only holds tables and
          columns computes nothing yet. The Model tab has its tables and
          relationships.
        </Empty>
      ) : shown.length === 0 ? (
        <Empty>
          Every measure here changes its answer with what the report is showing, so
          none of them can be written as a single query. Untick “only those with
          SQL” to see them and the reason for each.
        </Empty>
      ) : (
        <div className="flex flex-col gap-3">
          {shown.map((m) => (
            <MeasureRow
              key={m.measure}
              measure={m}
              copied={copied}
              onCopy={copy}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The dataset itself: which tables it holds and how they are joined.
 *
 * Collapsed by default on purpose. Somebody arriving to read the SQL should not
 * have to scroll past sixteen tables to reach it, and somebody asking what the
 * dataset *is* wants this first -- so it opens with the counts on one line and
 * expands to the detail, rather than choosing between the two audiences.
 *
 * Each join carries the actual SQL the queries below use. That is the point of
 * showing it here rather than as a diagram: a relationship drawn as an arrow
 * has to be trusted, and a relationship written as `ON a.x = b.y` can be
 * checked against the query that follows it.
 */
function Structure({
  tables,
  joins,
}: {
  tables: DatasetTable[];
  joins: DatasetJoin[];
}) {
  const [open, setOpen] = useState(false);
  const inactive = joins.filter((j) => !j.active).length;

  return (
    <section className="rounded border border-hairline bg-ground">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left"
      >
        <ChevronIcon
          size={13}
          className={cx(
            "flex-none text-faint transition-transform duration-(--duration-feedback)",
            open && "rotate-90",
          )}
        />
        <span className="font-medium">The dataset</span>
        <span className="font-mono text-[11px] text-faint">
          {tables.length} tables · {joins.length} joins
          {inactive > 0 && ` · ${inactive} inactive`}
        </span>
        <span className="ml-auto font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
          {open ? "hide" : "what it holds, and how it joins"}
        </span>
      </button>

      {open && (
        <div className="grid gap-4 border-t border-hairline p-3.5 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div className="flex min-w-0 flex-col gap-1.5">
            <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
              tables
            </span>
            <ul className="flex flex-col gap-1">
              {tables.map((table) => (
                <li
                  key={table.name}
                  className="flex items-baseline gap-2 rounded border border-hairline bg-surface px-2.5 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate text-sm">{table.name}</span>
                  {table.measures_only ? (
                    <Chip tone="neutral" title="Holds measures only — a grouping, not a table of data">
                      measures only
                    </Chip>
                  ) : (
                    <span className="flex-none font-mono text-[11px] text-faint tabular-nums">
                      {table.columns} cols
                      {table.measures > 0 && ` · ${table.measures}fx`}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>

          <div className="flex min-w-0 flex-col gap-1.5">
            <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
              joins
            </span>
            {joins.length === 0 ? (
              <p className="text-sm text-muted">
                This model defines no relationships, so its tables stand alone and
                every measure below reads a single one.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {joins.map((join) => (
                  <li
                    key={`${join.from_table}.${join.from_column}-${join.to_table}.${join.to_column}`}
                    className={cx(
                      "flex flex-col gap-1 rounded border bg-surface px-2.5 py-2",
                      join.active ? "border-hairline" : "border-review/40",
                    )}
                  >
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
                      <span className="font-mono text-[12px]">
                        {join.from_table}[{join.from_column}]
                      </span>
                      <span className="text-faint">→</span>
                      <span className="font-mono text-[12px]">
                        {join.to_table}[{join.to_column}]
                      </span>
                      {/* "M:1" is Power BI's notation and means nothing to
                          somebody reading a requirements document, so the
                          hover says it in words. Kept short on screen because
                          the pattern repeats down the list and the long form
                          would drown the table names. */}
                      <span
                        className="font-mono text-[11px] text-faint"
                        title={cardinalityInWords(join.cardinality)}
                      >
                        {join.cardinality}
                      </span>
                      {/* Marked, not hidden. An inactive relationship only
                          applies where a calculation deliberately invokes it,
                          so a reader who assumes it is live will expect a join
                          the queries below never make. */}
                      {!join.active && (
                        <Chip
                          tone="review"
                          title="Only applies where a calculation activates it deliberately"
                        >
                          inactive
                        </Chip>
                      )}
                    </div>
                    <code className="overflow-x-auto font-mono text-[11px] text-muted">
                      {join.sql}
                    </code>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function MeasureRow({
  measure,
  copied,
  onCopy,
}: {
  measure: DatasetMeasure;
  copied: string;
  onCopy: (text: string, token: string) => void;
}) {
  const has = measure.status === "exact";
  return (
    <section
      className={cx(
        "rounded border bg-ground",
        has ? "border-hairline" : "border-review/40",
      )}
    >
      <header className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 border-b border-hairline px-3.5 py-2">
        <h2 className="font-medium">{measure.measure}</h2>
        <span className="font-mono text-[11px] text-faint">{measure.table}</span>
        {measure.folder && <Chip tone="neutral">{measure.folder}</Chip>}
        {!has && (
          <Chip tone="review" title={measure.reason}>
            no SQL
          </Chip>
        )}
      </header>

      {measure.description && (
        <p className="px-3.5 pt-2 text-sm text-muted">{measure.description}</p>
      )}

      <div className="grid gap-4 p-3.5 md:grid-cols-[minmax(0,4fr)_minmax(0,5fr)]">
        <Side
          label="Power BI"
          language="dax"
          body={measure.dax}
          copied={copied === `dax:${measure.measure}`}
          onCopy={() => onCopy(measure.dax, `dax:${measure.measure}`)}
        />
        {has ? (
          <Side
            label="SQL"
            language="sql"
            body={measure.sql}
            copied={copied === `sql:${measure.measure}`}
            onCopy={() => onCopy(measure.sql, `sql:${measure.measure}`)}
          />
        ) : (
          // Deliberately not an empty panel. The reason is the useful content
          // here, and a reader who sees only a blank cannot tell "we could not"
          // from "we did not try".
          <div className="flex flex-col gap-1.5 rounded border border-review/40 bg-review-soft px-3 py-2.5">
            <span className="font-mono text-[10px] tracking-[0.08em] text-review uppercase">
              no single query can do this
            </span>
            <p className="text-sm text-review">{measure.reason}</p>
            <p className="text-xs text-muted">
              This is about the measure, not a gap in the tool. Its answer changes
              with what the report is filtered to, and a query has to be written
              for one fixed set of filters.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function Side({
  label,
  language,
  body,
  copied,
  onCopy,
}: {
  label: string;
  language: string;
  body: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
          {label}
        </span>
        <span className="font-mono text-[10px] text-faint">{language}</span>
        <Button size="sm" className="ml-auto" onClick={onCopy}>
          <CopyIcon size={11} className="flex-none" />
          {copied ? "copied" : "copy"}
        </Button>
      </div>
      <pre className="overflow-x-auto rounded border border-hairline bg-surface px-3 py-2 font-mono text-[11.5px] leading-relaxed">
        {body}
      </pre>
    </div>
  );
}
