/**
 * The dashboard's tiles, each with the formula behind it.
 *
 * This page exists because of one question, asked twice in the same review and
 * named as the only thing missing:
 *
 *   "How do I understand which DAX and SQL is for which particular KPI in the
 *    dashboard?"
 *   "This is the formula for total sales, and this is the formula for total
 *    profit. The clear distinction between the formulas and the KPIs should be
 *    there."
 *
 * Every other page here starts from the model and works outward. This one
 * starts from the screen somebody is actually looking at -- a tile with a title
 * on it -- and works back to the definition. That is the direction a reviewer
 * reads in, and it was the one direction the tool could not go.
 *
 * Grouped by report page, in the report's own order, because that is how the
 * person holding the dashboard knows where a tile is. Renaming or re-sorting
 * them would break the only landmark they have.
 */
import { useMemo, useState } from "react";
import { api, type ReportPayload, type Tile, type TileField } from "@/lib/api";
import { Chip, Empty, Failure, Loading, Stat } from "@/components/primitives";
import { ChevronIcon } from "@/components/icons";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";

/** Power BI's internal names for its visuals, in the words people use. */
const VISUAL_NAMES: Record<string, string> = {
  card: "single number",
  multiRowCard: "numbers",
  barChart: "bar chart",
  clusteredBarChart: "bar chart",
  columnChart: "column chart",
  clusteredColumnChart: "column chart",
  lineChart: "line chart",
  areaChart: "area chart",
  pieChart: "pie chart",
  donutChart: "donut chart",
  tableEx: "table",
  pivotTable: "table",
  matrix: "matrix",
  slicer: "filter",
  gauge: "gauge",
  treemap: "treemap",
  scatterChart: "scatter chart",
  waterfallChart: "waterfall chart",
  funnel: "funnel",
  kpi: "KPI visual",
  map: "map",
  filledMap: "map",
  keyDriversVisual: "key influencers",
  decompositionTreeVisual: "decomposition tree",
  qnaVisual: "Q&A",
};

/**
 * A custom visual is stored as the publisher's id, which is unreadable and
 * meaningless to the reader. Saying "custom visual" is both shorter and more
 * true than showing `PBI_CV_EB3A4088_75C5_4746_9D8B_255A7B7ECD6C`.
 */
function visualInWords(type: string): string {
  if (VISUAL_NAMES[type]) return VISUAL_NAMES[type];
  if (/^(PBI_CV_|PowerApps_)/.test(type) || /[0-9a-f]{12}/i.test(type)) {
    return "custom visual";
  }
  return type;
}

export function Dashboard({ dialect = "duckdb" }: { dialect?: string }) {
  const { data, error, retrying, reload } = useLoad<ReportPayload>(
    () => api.report([], dialect),
    [dialect],
  );
  const [onlyWithFormulas, setOnlyWithFormulas] = useState(false);

  const pages = useMemo(() => {
    if (!data) return [];
    // A page whose visuals are all furniture -- Microsoft's sample opens on a
    // legal notice made of text boxes -- has nothing to correlate. Dropped from
    // the list rather than shown as a row reading "0 tiles", and counted below
    // so that nothing disappears silently.
    const withTiles = data.pages.filter((page) => page.tiles.length > 0);
    if (!onlyWithFormulas) return withTiles;
    return withTiles
      .map((page) => ({
        ...page,
        tiles: page.tiles.filter((tile) => tile.fields.some((f) => f.kind === "measure")),
      }))
      .filter((page) => page.tiles.length > 0);
  }, [data, onlyWithFormulas]);

  if (error)
    return (
      <Failure
        message={error.message}
        status={error.status}
        what="the report's tiles"
        onRetry={reload}
        retrying={retrying}
      />
    );
  if (!data) return <Loading what="the report's tiles" rows={5} />;

  // A .SemanticModel folder is the model without the report. Saying so is a
  // true answer; showing an empty page would read as a failure to parse.
  if (data.counts.pages === 0)
    return (
      <div className="flex flex-col gap-3 p-4">
        <header>
          <h1 className="font-serif text-2xl font-bold">Dashboard tiles</h1>
        </header>
        <Empty>
          {data.model} was opened from a {data.source_format} source, which holds the
          semantic model without the report built on it. There are no tiles to show
          because this file does not contain any — not because none could be read.
          Open the same model as a <code className="font-mono text-ink">.pbix</code> and
          every page, tile and title comes with it.
        </Empty>
      </div>
    );

  const c = data.counts;
  const blank = data.pages.filter((page) => page.tiles.length === 0).length;
  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-col gap-1">
        <h1 className="font-serif text-2xl font-bold">Dashboard tiles</h1>
        <p className="max-w-prose text-sm text-muted">
          Every tile on {data.model}&rsquo;s report, and the formula behind the number
          it shows. Read from the report itself, so a tile appears here under the
          title its author typed.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="pages" value={c.pages} />
        <Stat label="tiles" value={c.tiles} />
        <Stat label="show a measure" value={c.with_measures} tone="ok" />
        <Stat
          label="not in this model"
          value={c.unresolved}
          tone={c.unresolved ? "review" : "neutral"}
          hint="Fields a tile is bound to that this semantic model does not contain."
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={onlyWithFormulas}
            onChange={(event) => setOnlyWithFormulas(event.target.checked)}
          />
          only tiles with a formula behind them
        </label>
        {blank > 0 && (
          <span className="text-xs text-faint">
            {blank} page{blank === 1 ? "" : "s"} not shown: nothing on{" "}
            {blank === 1 ? "it" : "them"} but text, images or buttons.
          </span>
        )}
      </div>

      {pages.length === 0 ? (
        <Empty>
          No tile on this report shows a measure. Every one of them displays columns
          directly, so there is no DAX to correlate — the numbers are the data as
          stored, not a calculation over it.
        </Empty>
      ) : (
        <div className="flex flex-col gap-3">
          {pages.map((page) => (
            <PageCard key={`${page.ordinal}-${page.name}`} name={page.name} tiles={page.tiles} />
          ))}
        </div>
      )}
    </div>
  );
}

function PageCard({ name, tiles }: { name: string; tiles: Tile[] }) {
  // Closed. Eighteen pages expanded is several thousand pixels of scroll, and
  // the reader came looking for one tile -- the page names are the index they
  // find it by.
  const [open, setOpen] = useState(false);
  const withFormula = tiles.filter((t) => t.fields.some((f) => f.kind === "measure")).length;

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
        <span className="min-w-0 truncate text-sm font-semibold">{name}</span>
        <span className="ml-auto flex-none font-mono text-[11px] text-faint tabular">
          {tiles.length} tiles · {withFormula} with a formula
        </span>
      </button>

      {open && (
        <ul className="divide-y divide-hairline border-t border-hairline">
          {tiles.map((tile, index) => (
            <TileRow key={`${tile.title}-${index}`} tile={tile} />
          ))}
        </ul>
      )}
    </section>
  );
}

function TileRow({ tile }: { tile: Tile }) {
  return (
    <li className="px-3.5 py-3">
      <div className="flex flex-wrap items-baseline gap-2">
        {tile.title ? (
          <h3 className="text-sm font-semibold">{tile.title}</h3>
        ) : (
          // Not invented. Power BI renders a default title from the fields at
          // display time; writing one here would put words on screen that are
          // in no file.
          <h3 className="text-sm font-semibold text-faint italic">no title set</h3>
        )}
        <Chip tone="neutral">{visualInWords(tile.visual_type)}</Chip>
      </div>

      <ul className="mt-2 flex flex-col gap-2">
        {tile.fields.map((field, index) => (
          <FieldRow key={`${field.qualified_name}-${index}`} field={field} />
        ))}
      </ul>
    </li>
  );
}

function FieldRow({ field }: { field: TileField }) {
  const shown = field.aggregation
    ? `${field.aggregation}(${field.qualified_name})`
    : field.qualified_name;

  return (
    <li className="rounded border border-hairline bg-surface px-2.5 py-2">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
          {field.role || "field"}
        </span>
        <span className="font-mono text-[11.5px]">{shown}</span>
        {field.kind === "measure" && <Chip tone="accent">measure</Chip>}
        {field.kind === "column" && <Chip tone="neutral">column</Chip>}
        {field.kind === "" && (
          <Chip tone="review">not in this model</Chip>
        )}
      </div>

      {field.kind === "" && (
        <p className="mt-1.5 text-[12px] text-muted">
          This tile is bound to a field the extracted model does not contain. Either
          it was renamed or removed after the report was built, or it is a kind of
          object this extractor does not read.
        </p>
      )}

      {field.dax && (
        <div className="mt-1.5">
          <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
            {field.kind === "measure" ? "dax" : "calculated by"}
          </span>
          <pre className="mt-0.5 overflow-x-auto rounded border border-hairline bg-ground px-2.5 py-1.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
            {field.dax}
          </pre>
        </div>
      )}

      {field.sql && (
        <div className="mt-1.5">
          <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
            the same number in sql
          </span>
          <pre className="mt-0.5 overflow-x-auto rounded border border-hairline bg-ground px-2.5 py-1.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
            {field.sql}
          </pre>
        </div>
      )}

      {/* Said, not hidden. Which numbers cannot be reproduced as a query is
          half of what a reviewer came to find out, and a tile that simply
          stopped after its DAX would read as this tool having failed. */}
      {field.kind === "measure" && !field.sql && field.reason && (
        <p className="mt-1.5 text-[12px] text-review">
          No single SQL query stands for this: {field.reason}
        </p>
      )}
    </li>
  );
}
