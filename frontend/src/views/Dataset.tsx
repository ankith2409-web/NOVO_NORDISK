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
import { api, type DatasetMeasure, type DatasetPayload } from "@/lib/api";
import {
  Button,
  Chip,
  controlClasses,
  Failure,
  Loading,
  Stat,
} from "@/components/primitives";
import { CopyIcon, DownloadIcon } from "@/components/icons";
import { SNAPSHOT_MODE } from "@/lib/api";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";

/** The whole-model figure, which is a single row rather than an absence. */
const WHOLE_MODEL = "__whole__";

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
        what="the dataset"
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
        <h1 className="font-serif text-2xl font-bold">The whole dataset</h1>
        <p className="text-sm text-muted">
          Every measure in {data.model}, as it is written in Power BI and as it would
          be written in SQL.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Stat label="measures" value={data.counts.measures} />
        <Stat label="with SQL" value={data.counts.translated} tone="ok" />
        <Stat
          label="no SQL possible"
          value={data.counts.blocked}
          tone={data.counts.blocked ? "review" : "neutral"}
          hint="Constructs that depend on filter context the query cannot fix."
        />
      </div>

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
            <option value={WHOLE_MODEL}>the whole model</option>
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

      {data.grain_options.length === 0 && (
        <p className="text-xs text-faint">
          This model defines no relationships, so there is no dimension to group by.
          Every measure below is the whole-model figure.
        </p>
      )}

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
    </div>
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
              no SQL at any grain
            </span>
            <p className="text-sm text-review">{measure.reason}</p>
            <p className="text-xs text-muted">
              This is a property of the expression, not a gap in the translation:
              the value depends on filter context that a query cannot fix.
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
