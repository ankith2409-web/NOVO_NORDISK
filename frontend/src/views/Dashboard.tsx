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
 * The KPIs lead, with their formulas already open. That ordering is the same
 * reviewer's, in her words while pointing at a dashboard: "Here it's the
 * numbers. Here it's the graphical representation." The numbers are what
 * somebody came to check; the charts are how the same measures are drawn. An
 * earlier version of this page opened with every page collapsed, which put
 * three clicks between arriving and seeing a single formula -- on the page
 * built specifically to show formulas.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ReportPayload, type Tile, type TileField } from "@/lib/api";
import { Chip, Empty, Failure, Loading, Stat } from "@/components/primitives";
import { ChevronIcon } from "@/components/icons";
import { ReportMap } from "@/components/ReportMap";
import { Meter } from "@/components/Chart";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";
import {
  MARK_CLASS,
  isMarked,
  useFocusTarget,
  type FocusRequest,
} from "@/lib/useFocusTarget";

/** Power BI's internal names for its visuals, in the words people use. */
const VISUAL_NAMES: Record<string, string> = {
  card: "single number",
  // Power BI's newer name for the same thing, and it must be here rather than
  // fall through: an unmapped type shows the internal name to the reader.
  cardVisual: "single number",
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
  azureMap: "map",
  shapeMap: "map",
  ribbonChart: "ribbon chart",
  lineClusteredColumnComboChart: "line and column chart",
  lineStackedColumnComboChart: "line and column chart",
  hundredPercentStackedBarChart: "stacked bar chart",
  hundredPercentStackedColumnChart: "stacked column chart",
  stackedBarChart: "stacked bar chart",
  stackedColumnChart: "stacked column chart",
  areaChart100: "area chart",
  scatterChartCombo: "scatter chart",
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

/**
 * What to call a tile on the map.
 *
 * Its author's title where there is one. Where there is not, the kind of
 * visual it is -- "donut chart", "map" -- which is a description rather than an
 * invented name, and is what somebody looking at the real dashboard would call
 * it anyway.
 */
function nameOf(tile: Tile): string {
  return tile.title || visualInWords(tile.visual_type);
}

export function Dashboard({
  dialect = "duckdb",
  focus = null,
}: {
  dialect?: string;
  focus?: FocusRequest | null;
}) {
  const { data, error, retrying, reload } = useLoad<ReportPayload>(
    () => api.report([], dialect),
    [dialect],
  );
  const scope = useRef<HTMLDivElement>(null);
  const marked = useFocusTarget(focus, scope);

  const pages = useMemo(
    // A page whose visuals are all furniture -- Microsoft's sample opens on a
    // legal notice made of text boxes -- has nothing to correlate. Dropped from
    // the list rather than shown as a row reading "0 tiles", and counted below
    // so that nothing disappears silently.
    () => (data ? data.pages.filter((page) => page.tiles.length > 0) : []),
    [data],
  );

  /**
   * The KPIs, one per measure rather than one per tile.
   *
   * Grouping by tile was the obvious thing and it was wrong on real data. In
   * Microsoft's Store Sales report all three card visuals carry the same title
   * -- "Store Sales Report", the author's page header -- so a tile-led list
   * renders three identical headings and leaves the reader to work out which
   * is which from the page name. And one of those three cards shows seven
   * different measures, which a single heading cannot name at all.
   *
   * The measure is what a KPI actually *is*: the reviewer's ask was "this is
   * the formula for total sales, and this is the formula for total profit",
   * and the thing being named there is the figure, not the box drawn around
   * it. So each measure appears once, with every place it is shown listed
   * under it.
   */
  const kpis = useMemo(() => {
    const byMeasure = new Map<
      string,
      { field: TileField; shownOn: { page: string; tile: string }[] }
    >();
    for (const page of pages) {
      for (const tile of page.tiles) {
        if (!tile.is_kpi) continue;
        for (const field of tile.fields.filter((f) => f.kind === "measure")) {
          const held = byMeasure.get(field.qualified_name);
          const where = { page: page.name, tile: tile.title };
          if (held) held.shownOn.push(where);
          else byMeasure.set(field.qualified_name, { field, shownOn: [where] });
        }
      }
    }
    return [...byMeasure.values()];
  }, [pages]);

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
  const wanted = focus?.target ?? "";

  return (
    <div ref={scope} className="flex flex-col gap-5 p-4">
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
        <Stat
          label="KPIs"
          value={c.kpis}
          tone="ok"
          hint="Tiles that state a figure as a number — the headline cards — rather than drawing it as a chart."
        />
        <Stat
          label="not in this model"
          value={c.unresolved}
          tone={c.unresolved ? "review" : "neutral"}
          hint="Fields a tile is bound to that this semantic model does not contain."
        />
      </div>

      {/* The KPIs, open. This is the answer the page was built to give, and it
          is on screen the moment the page is. */}
      <section className="flex flex-col gap-2.5">
        <h2 className="font-serif text-lg font-semibold">
          The KPIs
          <span className="ml-2 font-sans text-[12px] font-normal text-faint tabular">
            {kpis.length} figures across {c.kpis} card
            {c.kpis === 1 ? "" : "s"}
          </span>
        </h2>
        <p className="max-w-prose text-[13px] text-muted">
          The figures this report states as a number rather than drawing as a chart —
          the headline cards somebody points at. Each one is named by its measure, with
          the DAX behind it and the same calculation written as SQL.
        </p>

        {/* The split, as a proportion. Two parts of one whole is a bar with
            both numbers on it, not a pie: the figures are known, so nothing is
            gained by asking the eye to read an angle instead. */}
        <div className="max-w-md rounded border border-hairline bg-ground px-3.5 py-3">
          <Meter
            value={c.kpis}
            of={c.tiles}
            label="tiles state a figure as a number"
            rest="draw their measures as charts instead."
            tone="ok"
          />
        </div>

        {kpis.length === 0 ? (
          <Empty>
            No tile on this report states a figure as a number. Every measure here is
            drawn as a chart instead, so there is nothing this report itself calls out
            as a headline figure — the formulas are all below, under the visuals that
            plot them.
          </Empty>
        ) : (
          <div className="grid gap-2.5 xl:grid-cols-2">
            {kpis.map(({ field, shownOn }) => (
              <article
                key={field.qualified_name}
                data-focus={field.name}
                className={cx(
                  "flex min-w-0 flex-col gap-2 rounded border border-ok/30 bg-ground p-3",
                  isMarked(marked, field.name) && MARK_CLASS,
                )}
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <h3 className="font-serif text-base font-semibold">{field.name}</h3>
                  <Chip tone="ok">KPI</Chip>
                  <span className="font-mono text-[10.5px] text-faint">{field.table}</span>
                </div>
                {/* Where it is shown, which is the half a tile-led list got
                    right. Named as pages rather than as tiles because in a real
                    report several cards share one title -- all three of Store
                    Sales' say "Store Sales Report" -- and the page is what
                    actually tells a reader where to look. */}
                <p className="text-[12px] text-muted">
                  On {shownOn.map((where) => where.page).join(", ")}
                </p>
                <FieldBody field={field} />
              </article>
            ))}
          </div>
        )}
      </section>

      {/* And everything else, by page, in the report's own order -- because
          that is how the person holding the dashboard knows where a tile is.
          Renaming or re-sorting them would break the only landmark they have. */}
      <section className="flex flex-col gap-2.5">
        <h2 className="font-serif text-lg font-semibold">The report, page by page</h2>
        <p className="max-w-prose text-[13px] text-muted">
          Each page drawn as its own floor plan — every tile where its author put it,
          at the size they made it. Click a rectangle to read the formula behind it.
          The figures themselves are not shown, and are not knowable from this file:
          a measure&rsquo;s value is DAX evaluated against data under the filter
          context the report supplies, and printing a number here would mean
          inventing one.
          {blank > 0 && (
            <>
              {" "}
              {blank} page{blank === 1 ? "" : "s"} {blank === 1 ? "is" : "are"} not
              listed: nothing on {blank === 1 ? "it" : "them"} but text, images or
              buttons.
            </>
          )}
        </p>

        <div className="flex flex-col gap-2">
          {pages.map((page, index) => (
            <PageCard
              key={`${page.ordinal}-${page.name}`}
              name={page.name}
              tiles={page.tiles}
              // The first page open. Somebody arriving here should see a tile
              // and its formula without clicking; the rest stay shut because
              // eighteen pages expanded is several thousand pixels of scroll,
              // and the page names are the index a reader finds one by.
              //
              // And the page holding whatever search asked for, which is a
              // different question: `marked` cannot answer it, because a tile
              // inside a shut page has not rendered and so cannot be found or
              // marked. The request itself has to open the page.
              canvas={{ width: page.width, height: page.height }}
              startOpen={index === 0 || holds(page.tiles, wanted)}
              wanted={wanted}
              marked={marked}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

/** Does one of these tiles carry the title search asked for? */
function holds(tiles: Tile[], wanted: string): boolean {
  return wanted !== "" && tiles.some((tile) => isMarked(wanted, tile.title));
}

function PageCard({
  name,
  tiles,
  canvas,
  startOpen,
  wanted,
  marked,
}: {
  name: string;
  tiles: Tile[];
  canvas: { width: number; height: number };
  startOpen: boolean;
  /** What search asked for, whether or not it has been found and marked yet. */
  wanted: string;
  marked: string | null;
}) {
  const [open, setOpen] = useState(startOpen);
  // Which tile on the map is being read. Nothing, until somebody clicks a
  // rectangle -- opening with one already chosen would put a highlight on the
  // page that nobody asked for and that says nothing about the tile.
  const [picked, setPicked] = useState<number | null>(null);
  const rows = useRef<HTMLUListElement>(null);
  const withFormula = tiles.filter((t) => t.fields.some((f) => f.kind === "measure")).length;

  // A second lookup while this page is already mounted: the initial state
  // above ran once and cannot see it.
  const asked = holds(tiles, wanted);
  useEffect(() => {
    if (asked) setOpen(true);
  }, [asked]);

  function pick(index: number) {
    setPicked(index);
    // The row is the detail; the map is the index into it. Scrolled rather
    // than expanded in place so there is one copy of a tile's formulas on the
    // page and not two saying possibly different things.
    rows.current?.children[index]?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

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
        <>
          <div className="border-t border-hairline p-3.5">
            <ReportMap
              tiles={tiles}
              canvas={canvas}
              picked={picked}
              onPick={pick}
              nameOf={nameOf}
            />
          </div>
          <ul ref={rows} className="divide-y divide-hairline border-t border-hairline">
            {tiles.map((tile, index) => (
              <TileRow
                key={`${tile.title}-${index}`}
                tile={tile}
                marked={marked}
                picked={picked === index}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function TileRow({
  tile,
  marked,
  picked = false,
}: {
  tile: Tile;
  marked: string | null;
  /** True when this is the rectangle just clicked on the map above. */
  picked?: boolean;
}) {
  return (
    <li
      data-focus={tile.title}
      className={cx(
        "px-3.5 py-3",
        (picked || isMarked(marked, tile.title)) && MARK_CLASS,
      )}
    >
      <div className="flex flex-wrap items-baseline gap-2">
        {tile.title ? (
          <h3 className="text-sm font-semibold">{tile.title}</h3>
        ) : (
          // Not invented. Power BI renders a default title from the fields at
          // display time; writing one here would put words on screen that are
          // in no file.
          <h3 className="text-sm font-semibold text-faint italic">no title set</h3>
        )}
        {/* The split a reviewer asked for by name. A KPI states its figure; a
            chart draws one. Both may read the same measure, and only the first
            is what anybody points at and calls the KPI. */}
        {tile.is_kpi && (
          <Chip tone="ok" title="States a figure as a number, rather than drawing it">
            KPI
          </Chip>
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
    <li className="min-w-0 rounded border border-hairline bg-surface px-2.5 py-2">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
          {field.role || "field"}
        </span>
        <span className="font-mono text-[11.5px]">{shown}</span>
        {field.kind === "measure" && <Chip tone="accent">measure</Chip>}
        {field.kind === "column" && <Chip tone="neutral">column</Chip>}
        {field.kind === "" && <Chip tone="review">not in this model</Chip>}
      </div>
      <FieldBody field={field} />
    </li>
  );
}

/**
 * What a field resolves to: its formula, its SQL, or why there is neither.
 *
 * Shared by the KPI cards and the tile rows, which show the same thing about
 * the same object and had drifted into showing it two ways.
 */
function FieldBody({ field }: { field: TileField }) {
  return (
    <>
      {field.kind === "" && (
        <p className="mt-1.5 text-[12px] text-muted">
          This tile is bound to a field the extracted model does not contain. Either
          it was renamed or removed after the report was built, or it is a kind of
          object this extractor does not read.
        </p>
      )}

      {field.dax && (
        <Code label={field.kind === "measure" ? "dax" : "calculated by"}>{field.dax}</Code>
      )}
      {field.sql && <Code label="the same number in sql">{field.sql}</Code>}

      {/* Said, not hidden. Which numbers cannot be reproduced as a query is
          half of what a reviewer came to find out, and a tile that simply
          stopped after its DAX would read as this tool having failed. */}
      {field.kind === "measure" && !field.sql && field.reason && (
        <p className="mt-1.5 text-[12px] text-review">
          No single SQL query stands for this: {field.reason}
        </p>
      )}
    </>
  );
}

/**
 * A block of code that never loses a character.
 *
 * `whitespace-pre-wrap` alone was not enough: a long unbroken token -- a
 * qualified column name, a URL in a comment -- has no space to wrap at, so it
 * overflowed its box and was clipped mid-expression. `break-words` lets such a
 * token break anywhere, and the scroll container catches whatever still cannot.
 */
function Code({ label, children }: { label: string; children: string }) {
  return (
    <div className="mt-1.5 min-w-0">
      <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
        {label}
      </span>
      <pre className="mt-0.5 min-w-0 overflow-x-auto rounded border border-hairline bg-ground px-2.5 py-1.5 font-mono text-[11.5px] leading-relaxed break-words whitespace-pre-wrap">
        {children}
      </pre>
    </div>
  );
}
