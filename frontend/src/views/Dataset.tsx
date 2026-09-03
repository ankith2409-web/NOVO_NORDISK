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
import { useMemo, useRef, useState } from "react";
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
import { ChevronIcon, CopyIcon, DownloadIcon, PageIcon } from "@/components/icons";
import { DocumentPreview } from "@/components/DocumentPreview";
import { SNAPSHOT_MODE } from "@/lib/api";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";
import { FEATURE } from "@/lib/naming";
import {
  MARK_CLASS,
  isMarked,
  useFocusTarget,
  type FocusRequest,
} from "@/lib/useFocusTarget";

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

export function Dataset({ focus = null }: { focus?: FocusRequest | null }) {
  const scope = useRef<HTMLDivElement>(null);
  const marked = useFocusTarget(focus, scope);
  const [grain, setGrain] = useState<string>(WHOLE_MODEL);
  const [dialect, setDialect] = useState("duckdb");
  const [onlyTranslated, setOnlyTranslated] = useState(false);
  const [copied, setCopied] = useState("");
  const [readingFrd, setReadingFrd] = useState(false);

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

  /**
   * Every measure's DAX in one block, and every measure's SQL in another.
   *
   * Both, not just the SQL, and both copyable whole. Asked for in exactly those
   * words -- "the DAX in one place so she can copy it in one go, and the SQL in
   * one place" -- and the reason is the job it is for: pasting a model's
   * definitions into a review, a ticket or another tool is one action, and
   * doing it a measure at a time down a list of sixty is not.
   *
   * Measures with no SQL are still named in the SQL block, as a comment saying
   * why. A block that silently held 34 of 58 queries would be a wrong answer to
   * "give me the SQL for this model".
   */
  const allDax = data.measures
    .map((m) => `// ${m.measure}\n${m.dax}`)
    .join("\n\n");
  const allSql = data.measures
    .map((m) =>
      m.sql
        ? `-- ${m.measure}\n${m.sql};`
        : `-- ${m.measure}: no SQL — ${m.reason}`,
    )
    .join("\n\n");

  /**
   * Every measure as columns of as few queries as possible.
   *
   * A different thing from the block above, and the one actually asked for:
   * "if you can convert the whole thing into an SQL query rather than giving
   * one each". One query you run once and get the whole dashboard back, rather
   * than forty you run in turn and line up by hand.
   *
   * Usually one query. Two when the model compares against an earlier period,
   * because a monthly figure and an all-time total cannot share a result set
   * without one of them changing meaning -- so each says which grain it is at.
   */
  const oneQuery = [
    ...data.combined.map(
      (q) => `-- ${q.label}\n-- ${q.measures.join(", ")}\n${q.sql};`,
    ),
    ...(data.not_combined.length
      ? [
          "-- Not in any query above, and why:\n" +
            data.not_combined
              .map((m) => `--   ${m.measure}: ${m.reason}`)
              .join("\n"),
        ]
      : []),
  ].join("\n\n");

  const copy = (text: string, token: string) => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(token);
      window.setTimeout(() => setCopied((c) => (c === token ? "" : c)), 1600);
    });
  };

  return (
    <div ref={scope} className="flex flex-col gap-4 p-3">
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
          {/* Sentence case, in the page's own typeface. Set as small mono
              capitals these read as field names in a schema rather than as
              the question they are, and a screen where every label looks like
              code is a screen a non-technical reader stops reading. */}
          <span className="text-[12.5px] text-muted">One row per</span>
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
          <span className="text-[12.5px] text-muted">Written for</span>
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
              <span className="text-[12.5px] text-muted">
                The FRD, with these queries in it
              </span>
              {/* Reading uses the grain and dialect chosen above, exactly as
                  saving does -- one URL, so the two cannot disagree. */}
              <Button onClick={() => setReadingFrd(true)}>
                <PageIcon size={11} className="flex-none" />
                Read
              </Button>
              <span className="mx-0.5 h-4 w-px bg-hairline" />
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
        </div>
      </div>

      {readingFrd && (
        <DocumentPreview
          kind="functional"
          title="Functional Requirements Document"
          sql={{ grain: grains, dialect }}
          onClose={() => setReadingFrd(false)}
        />
      )}

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
        <>
          <AllInOnePlace
            dax={allDax}
            sql={allSql}
            oneQuery={oneQuery}
            model={data.model}
            counts={data.counts}
            dialect={dialect}
            grain={grains}
            copied={copied}
            onCopy={copy}
          />
          <div className="flex flex-col gap-3">
          {shown.map((m) => (
            <MeasureRow
              key={m.measure}
              measure={m}
              copied={copied}
              onCopy={copy}
              marked={isMarked(marked, m.measure)}
            />
          ))}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Every definition in one block, one for DAX and one for SQL.
 *
 * Collapsed by default and placed above the per-measure list: someone reading
 * measure by measure should not have to scroll past a wall of sixty
 * definitions, and someone who came to take the lot should not have to scroll
 * to the bottom to find out they can.
 */
function AllInOnePlace({
  dax,
  sql,
  oneQuery,
  model,
  counts,
  dialect,
  grain,
  copied,
  onCopy,
}: {
  dax: string;
  sql: string;
  oneQuery: string;
  model: string;
  counts: DatasetPayload["counts"];
  dialect: string;
  grain: string[];
  copied: string;
  onCopy: (text: string, token: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [showing, setShowing] = useState<"dax" | "sql" | "one">("dax");
  const text = showing === "dax" ? dax : showing === "sql" ? sql : oneQuery;

  return (
    <section className="rounded border border-hairline bg-ground">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-3.5 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((was) => !was)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <ChevronIcon
            size={12}
            className={cx(
              "flex-none text-faint transition-transform duration-(--duration-feedback)",
              open ? "rotate-90" : "",
            )}
          />
          <span className="text-sm font-semibold">Everything in one place</span>
          <span className="truncate font-mono text-[11px] text-faint">
            all {counts.measures} definitions, to copy in one go
          </span>
        </button>
        {/* On the header, not only inside: copying the lot is the reason to
            come here, and needing to expand a panel first would put a click
            in front of the one thing it exists for. */}
        {(
          [
            { id: "dax", text: dax, label: "copy all DAX" },
            { id: "sql", text: sql, label: "copy all SQL" },
            { id: "one", text: oneQuery, label: "copy one query" },
          ] as const
        ).map((one) => (
          <Button
            key={one.id}
            tone="primary"
            onClick={() => {
              setShowing(one.id);
              onCopy(one.text, `all-${one.id}`);
            }}
          >
            <CopyIcon size={11} className="flex-none" />
            {copied === `all-${one.id}` ? "copied" : one.label}
          </Button>
        ))}
      </div>

      {open && (
        <div className="flex flex-col gap-2 border-t border-hairline p-3">
          <div className="flex flex-wrap items-center gap-2">
            {/* Two blocks rather than one interleaved: they go to different
                places. The DAX goes to whoever owns the Power BI model; the
                SQL goes into a warehouse. */}
            {(
              [
                { id: "dax", label: "DAX", said: "as written in Power BI" },
                { id: "sql", label: "SQL", said: "one query per measure" },
                { id: "one", label: "SQL", said: "all measures in one query" },
              ] as const
            ).map((tab) => (
              <Button
                key={tab.id}
                tone={showing === tab.id ? "selected" : "quiet"}
                onClick={() => setShowing(tab.id)}
                aria-pressed={showing === tab.id}
              >
                {tab.label} <span className="text-faint">{tab.said}</span>
              </Button>
            ))}
            {/* No copy button in here. The header already carries one for
                each block, and a second control with the same accessible name
                is how a name-based click -- a screen reader's element list, a
                test, voice control -- lands on the wrong one. */}
          </div>

          <p className="text-xs text-muted">
            {showing === "one" ? (
              <>
                Every measure as columns of a single query — run it once and the whole
                dashboard comes back, instead of running {counts.translated} queries and
                lining the answers up by hand. Measures that read different tables are
                aggregated separately and joined on the grain, so nothing is multiplied
                by a table it does not belong to.
              </>
            ) : showing === "dax" ? (
              <>
                Every measure in {model}, exactly as it is written in the model, each
                under its name.
              </>
            ) : (
              <>
                The same {counts.measures} measures written as {dialect}, at one row per{" "}
                {grain.length ? grain.join(", ") : "the whole model"}.{" "}
                {counts.blocked > 0 && (
                  <>
                    {counts.blocked} of them have no query — those appear as a comment
                    saying why, rather than being left out.
                  </>
                )}
              </>
            )}
          </p>

          {/* `whitespace-pre-wrap`, matching every other code block here: the
              "no SQL" lines are one long sentence each, and left unwrapped they
              run out of the box and hide the reason. */}
          <pre className="max-h-[28rem] overflow-auto rounded border border-hairline bg-surface px-3 py-2.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
            {text}
          </pre>
        </div>
      )}
    </section>
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
  // Open. "What are the datasets, how are they joined with each other, and
  // what are the SQL" was asked as one question, and the tables and joins are
  // the first two thirds of it -- putting them behind a disclosure meant this
  // page opened on the third part alone.
  const [open, setOpen] = useState(true);
  // Which table the reader is asking about. Held here rather than inside the
  // drawing so the picture and the list below it are always saying the same
  // thing about the same table.
  const [focusTable, setFocusTable] = useState("");
  const inactive = joins.filter((j) => !j.active).length;
  const shown = focusTable
    ? joins.filter((j) => j.from_table === focusTable || j.to_table === focusTable)
    : joins;

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
              tables — click one to see only its joins
            </span>
            <ul className="flex flex-col gap-1">
              {tables.map((table) => (
                <li
                  key={table.name}
                  className={cx(
                    "flex items-baseline gap-2 rounded border bg-surface px-2.5 py-1.5",
                    focusTable === table.name
                      ? "border-accent bg-accent-soft"
                      : "border-hairline",
                  )}
                >
                  <button
                    type="button"
                    onClick={() =>
                      setFocusTable(focusTable === table.name ? "" : table.name)
                    }
                    className="min-w-0 flex-1 truncate text-left text-sm"
                  >
                    {table.name}
                  </button>
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
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                joins
              </span>
              {focusTable && (
                <button
                  type="button"
                  onClick={() => setFocusTable("")}
                  className="text-[11.5px] text-accent underline underline-offset-2"
                >
                  showing only {focusTable} — show all {joins.length}
                </button>
              )}
            </div>
            {joins.length === 0 ? (
              <p className="text-sm text-muted">
                This model defines no relationships, so its tables stand alone and
                every measure below reads a single one.
              </p>
            ) : shown.length === 0 ? (
              <p className="text-sm text-muted">
                <span className="font-mono">{focusTable}</span> is not joined to
                anything in this model, so nothing filters through it.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {shown.map((join) => (
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
  marked = false,
}: {
  measure: DatasetMeasure;
  copied: string;
  onCopy: (text: string, token: string) => void;
  /** True when search sent somebody here to read this one. */
  marked?: boolean;
}) {
  const has = measure.status === "exact";
  return (
    <section
      data-focus={measure.measure}
      className={cx(
        "rounded border bg-ground",
        has ? "border-hairline" : "border-review/40",
        marked && MARK_CLASS,
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

      {/* Side by side is the point -- "this DAX, that SQL" -- but only where
          there is room for it. At the `md` breakpoint the copilot is still
          docked, leaving each column about 300px, which is narrower than most
          single lines of either language. */}
      <div className="grid gap-4 p-3.5 xl:grid-cols-[minmax(0,4fr)_minmax(0,5fr)]">
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
      {/* `overflow-x-auto` alone was hiding text rather than revealing it: a
          `pre` does not wrap, so a long measure scrolled sideways inside a
          column with no visible scrollbar and simply appeared to stop --
          `CALCULATE(COUNTA([Store type]), FILTER(ALL(Store), [St`. Wrapping is
          the right behaviour for a column of code somebody is reading rather
          than editing; `break-words` covers the case wrapping cannot, a single
          token longer than the box. */}
      <pre className="min-w-0 overflow-x-auto rounded border border-hairline bg-surface px-3 py-2 font-mono text-[11.5px] leading-relaxed break-words whitespace-pre-wrap">
        {body}
      </pre>
    </div>
  );
}
