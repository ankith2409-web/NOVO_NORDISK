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
import {
  api,
  type BreakdownPayload,
  type DashboardPayload,
  type MapPayload,
  type MeasureValue,
  type ReportFilterPayload,
  type ReportPayload,
  type Tile,
  type TileField,
  type ValuesPayload,
} from "@/lib/api";
import {
  Atlas,
  Bars,
  Columns,
  Donut,
  Tabular,
  arrange,
  canOrderByTime,
  chartFor,
  exact,
  type Order,
} from "@/components/Charts";
import {
  Chip,
  Empty,
  Failure,
  Loading,
  Stat,
  controlClasses,
} from "@/components/primitives";
import { ChevronIcon } from "@/components/icons";
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
  // The figures, run against the model's own rows. Loaded separately from the
  // report because it is the slower of the two by an order of magnitude -- the
  // first call for a model reads a million rows into a query engine -- and the
  // page has plenty to show while it is still working.
  const { data: computed } = useLoad<ValuesPayload>(() => api.values(), []);
  const [picked, setPicked] = useState("");
  const scope = useRef<HTMLDivElement>(null);
  const marked = useFocusTarget(focus, scope);

  const figures = useMemo(() => {
    const by = new Map<string, MeasureValue>();
    for (const value of computed?.values ?? []) by.set(value.measure, value);
    return by;
  }, [computed]);

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

  const opened = kpis.find((entry) => entry.field.name === picked);

  // The charts follow the cards. Nothing picked means the first card that has
  // a figure -- so the section is populated the moment the page is, rather
  // than being an empty frame waiting for a click nobody knows to make.
  const charting =
    opened?.field.name ??
    kpis.find((entry) => figures.get(entry.field.name)?.value != null)?.field.name ??
    "";
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

      {/* Why a figure here can differ from the same card in Power BI, said
          where the two get compared rather than buried in a footnote. This is
          the first question the report raises the moment somebody opens the
          .pbix beside the tool. */}
      {data.filters.length > 0 && <ReportFilters filters={data.filters} />}

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

        {kpis.length === 0 ? (
          <Empty>
            No tile on this report states a figure as a number. Every measure here is
            drawn as a chart instead, so there is nothing this report itself calls out
            as a headline figure — the formulas are all below, under the visuals that
            plot them.
          </Empty>
        ) : (
          <>
            {/* The card row. Clicking one opens it below rather than expanding
                it in place: the cards are a row somebody scans across, and a
                card that grew to hold a SQL query would push the rest of them
                off the line being scanned. */}
            <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-4">
              {kpis.map(({ field, shownOn }) => (
                <KpiCard
                  key={field.qualified_name}
                  field={field}
                  places={shownOn.length}
                  figure={figures.get(field.name)}
                  loading={!computed}
                  selected={picked === field.name}
                  marked={isMarked(marked, field.name)}
                  onPick={() =>
                    setPicked((was) => (was === field.name ? "" : field.name))
                  }
                />
              ))}
            </div>

            {/* Said once, under the row, rather than on every card. */}
            {computed && (
              <p className="text-[11.5px] text-muted">
                {computed.available ? (
                  <>
                    Every figure above was computed by running that measure&rsquo;s own
                    SQL against the {computed.rows.toLocaleString()} rows this file
                    carries — open a card to see the query that produced it. Shown as
                    the query returned them: Power BI renders a ratio like 0.42 as
                    42.29% using a format string this file does not expose, so
                    reinterpreting one here would be a guess.
                  </>
                ) : (
                  computed.reason
                )}
              </p>
            )}

            {/* The opened card, in full. One at a time, so there is one place
                on the page where a formula is being read. */}
            {opened && (
              <article className="flex min-w-0 flex-col gap-2 rounded border border-accent bg-ground p-3.5">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <h3 className="font-serif text-base font-semibold">
                    {opened.field.name}
                  </h3>
                  <Chip tone="ok">KPI</Chip>
                  <span className="font-mono text-[10.5px] text-faint">
                    {opened.field.table}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPicked("")}
                    className="ml-auto text-[11.5px] text-accent underline underline-offset-2"
                  >
                    close
                  </button>
                </div>
                {/* Where it is shown. Named as pages rather than as tiles
                    because in a real report several cards share one title --
                    all three of Store Sales' say "Store Sales Report" -- and
                    the page is what actually tells a reader where to look. */}
                <p className="text-[12px] text-muted">
                  On {opened.shownOn.map((where) => where.page).join(", ")}
                </p>
                <FieldBody field={opened.field} />
              </article>
            )}

            {charting && <Breakdowns measure={charting} picked={Boolean(opened)} />}
            {charting && <Where measure={charting} />}
          </>
        )}
      </section>

      {/* And everything else, by page, in the report's own order -- because
          that is how the person holding the dashboard knows where a tile is.
          Renaming or re-sorting them would break the only landmark they have. */}
      <section className="flex flex-col gap-2.5">
        <h2 className="font-serif text-lg font-semibold">Everything else on the report</h2>
        <p className="max-w-prose text-[13px] text-muted">
          The same measures drawn as charts, and the tiles that show columns
          directly. Grouped by page, in the order the report puts them in.
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
  startOpen,
  wanted,
  marked,
}: {
  name: string;
  tiles: Tile[];
  startOpen: boolean;
  /** What search asked for, whether or not it has been found and marked yet. */
  wanted: string;
  marked: string | null;
}) {
  const [open, setOpen] = useState(startOpen);
  const withFormula = tiles.filter((t) => t.fields.some((f) => f.kind === "measure")).length;

  // A second lookup while this page is already mounted: the initial state
  // above ran once and cannot see it.
  const asked = holds(tiles, wanted);
  useEffect(() => {
    if (asked) setOpen(true);
  }, [asked]);


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
            <TileRow key={`${tile.title}-${index}`} tile={tile} marked={marked} />
          ))}
        </ul>
      )}
    </section>
  );
}

function TileRow({ tile, marked }: { tile: Tile; marked: string | null }) {
  return (
    <li
      data-focus={tile.title}
      className={cx("px-3.5 py-3", isMarked(marked, tile.title) && MARK_CLASS)}
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

/**
 * A figure, sized for reading rather than for precision.
 *
 * Two rules, both from what the real values turned out to be. Large ones are
 * abbreviated because `45,184,553.69` on a card is a number nobody reads to the
 * end -- `45.18M` is the same figure at the size the card is. Small ones keep
 * significant digits instead of decimal places: `Sales Per Sq Ft` is 0.00094,
 * and rounding it to two decimals renders a real measure as `0.00`, which reads
 * as "this is zero" rather than "this is small".
 *
 * Nothing is reinterpreted. Power BI shows `Gross Margin %` as `42.29%` because
 * the measure carries a format string; that string is not in what this file's
 * reader exposes, so the ratio is shown as the ratio it is.
 */
function readable(value: number): string {
  const size = Math.abs(value);
  if (size === 0) return "0";
  if (size >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (size >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (size >= 10_000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (size >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return Number(value.toPrecision(2)).toString();
}

/**
 * One headline figure, as a card.
 *
 * The number leads and the name sits under it, which is the way round every
 * dashboard does it and the opposite of a definition list. A measure that could
 * not be computed says so in the number's place rather than showing a zero --
 * a zero here would be indistinguishable from a real one.
 */
function KpiCard({
  field,
  places,
  figure,
  loading,
  selected,
  marked,
  onPick,
}: {
  field: TileField;
  /** How many report pages show it. */
  places: number;
  figure: MeasureValue | undefined;
  loading: boolean;
  selected: boolean;
  marked: boolean;
  onPick: () => void;
}) {
  const value = figure?.value ?? null;

  return (
    <button
      type="button"
      onClick={onPick}
      aria-pressed={selected}
      data-focus={field.name}
      className={cx(
        "flex min-w-0 flex-col gap-0.5 rounded border bg-ground p-3 text-left",
        "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
        selected ? "border-accent bg-accent-soft" : "border-hairline hover:border-edge",
        marked && MARK_CLASS,
      )}
    >
      <span className="flex min-h-7 items-baseline">
        {loading ? (
          <span className="text-[13px] text-faint">computing…</span>
        ) : value !== null ? (
          <span
            className="truncate font-mono text-[22px] leading-none font-semibold tabular"
            // The full figure, unabbreviated, for anyone who needs the digits
            // the card does not have room for.
            title={value.toLocaleString(undefined, { maximumFractionDigits: 4 })}
          >
            {readable(value)}
          </span>
        ) : (
          <span className="text-[13px] text-review">no single figure</span>
        )}
      </span>
      <span className="truncate text-[13px] font-medium">{field.name}</span>
      <span className="truncate font-mono text-[10.5px] text-faint">
        {field.table} · on {places} page{places === 1 ? "" : "s"}
      </span>
    </button>
  );
}

/**
 * The filters the report applies to itself, before any of its numbers exist.
 *
 * This began as a bug report that was not a bug: Power BI showed 387.1K for
 * `Net Sales` where this tool showed 1.2M, on the same file, for the same
 * measure. Both were right. Microsoft's report carries a report-level filter
 * pinning every page to June, so the card is that measure inside that filter,
 * while the figure here is the measure over every row in the file.
 *
 * Nothing was wrong except that the tool knew only one of the two questions
 * had been asked and said nothing about the other -- which, on a tool whose
 * whole claim is that a number can be checked, is the worst kind of silence.
 * So the report's filters are read out and shown beside the figures they
 * explain. It is also the project's own thesis restated: a measure has no
 * value until a filter context is named.
 */
function ReportFilters({ filters }: { filters: ReportFilterPayload[] }) {
  const [open, setOpen] = useState(false);
  const everywhere = filters.filter((f) => f.scope === "report");
  const perPage = filters.filter((f) => f.scope !== "report");

  return (
    <section className="rounded border border-review/40 bg-review-soft px-3.5 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h2 className="text-[13px] font-medium text-ink">
          This report filters itself before it computes anything
        </h2>
        {perPage.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen((was) => !was)}
            className="ml-auto text-[11.5px] text-accent underline underline-offset-2"
          >
            {open ? "hide the per-page filters" : `all ${filters.length}`}
          </button>
        )}
      </div>

      {everywhere.length > 0 ? (
        <p className="mt-1.5 max-w-prose text-[12.5px] text-ink">
          Every page is narrowed to{" "}
          {everywhere.map((filter, at) => (
            <span key={filter.text}>
              {at > 0 && ", "}
              <span className="font-mono text-[11.5px] font-medium">{filter.text}</span>
            </span>
          ))}
          . The figures on this page are those same measures over <em>every</em> row in
          the file, so a card opened in Power BI will read lower. Both are right, and
          they answer different questions.
        </p>
      ) : (
        <p className="mt-1.5 max-w-prose text-[12.5px] text-ink">
          Individual pages narrow what they show, so a card in Power BI can differ from
          the same measure computed here over every row in the file.
        </p>
      )}

      {open && perPage.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 border-t border-review/30 pt-2">
          {perPage.map((filter) => (
            <li key={`${filter.page}-${filter.text}`} className="text-[11.5px]">
              <span className="font-mono text-faint">{filter.page}</span>{" "}
              <span className={filter.readable ? "text-muted" : "text-review"}>
                {filter.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * What one KPI is made of, drawn.
 *
 * The card above states a total; these state the parts, from the same rows and
 * the same translated SQL, so a bar and the card agree by construction rather
 * than by coincidence -- the parts sum to the total, and the query that
 * produced them is one click away.
 *
 * Which dimensions appear is decided on the server by measuring the data, not
 * by reading column names, because in a real model the names lie in both
 * directions: `DM_Pic_fl` holds Flickr URLs and `Segment` holds 1,415 of them.
 */
//: The arrangements offered, in the order a reader is likely to want them.
//: `time` is offered only where the data can support it, which is decided per
//: chart rather than for the section -- `Calendar[Month]` can be put in date
//: order and `Store[Chain]` cannot.
const ORDERS: { value: Order; label: string }[] = [
  { value: "largest", label: "Largest first" },
  { value: "smallest", label: "Smallest first" },
  { value: "label", label: "A\u2013Z" },
  { value: "time", label: "In date order" },
];

function Breakdowns({ measure, picked }: { measure: string; picked: boolean }) {
  // Sorting is the reader's view of the same numbers, so it stays in the
  // browser and costs no round trip. The year is not: it changes which rows are
  // aggregated, which has to happen in the query or the answer is wrong.
  const [order, setOrder] = useState<Order>("largest");
  const [year, setYear] = useState<number | null>(null);
  // Cutting by a period is a different query, not a different view of one, so
  // it goes to the server like the year does.
  const [period, setPeriod] = useState<string | null>(null);

  const { data, error } = useLoad<DashboardPayload>(
    () => api.dashboard(measure, year, period),
    [measure, year, period],
  );

  if (error) return null;

  return (
    <section className="flex flex-col gap-2.5 rounded border border-hairline bg-surface p-3.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <h3 className="font-serif text-base font-semibold">
          What {measure} is made of
        </h3>
        <span className="text-[11.5px] text-faint">
          {picked ? "the card you opened" : "pick a card above to change this"}
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1.5 text-[11.5px] text-muted">
            <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
              order
            </span>
            <select
              value={order}
              onChange={(event) => setOrder(event.target.value as Order)}
              className={controlClasses("quiet")}
            >
              {ORDERS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          {/* Same rule as the year: offered only where the calendar really
              falls into that many buckets, so a model whose timestamps are all
              midnight is never offered "by hour". */}
          {data && data.periods.length > 0 && (
            <label className="flex items-center gap-1.5 text-[11.5px] text-muted">
              <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                over time
              </span>
              <select
                value={period ?? ""}
                onChange={(event) => setPeriod(event.target.value || null)}
                className={controlClasses("quiet")}
              >
                <option value="">Not by date</option>
                {data.periods.map((option) => (
                  <option key={option} value={option}>
                    by {option}
                  </option>
                ))}
              </select>
            </label>
          )}

          {/* Shown only where a year filter is real. A model whose calendar no
              relationship reaches cannot be filtered by year, and a disabled
              control that never works is worse than no control. */}
          {data && data.years.length > 0 && (
            <label className="flex items-center gap-1.5 text-[11.5px] text-muted">
              <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                year
              </span>
              <select
                value={year ?? ""}
                onChange={(event) =>
                  setYear(event.target.value ? Number(event.target.value) : null)
                }
                className={controlClasses("quiet")}
              >
                <option value="">All years</option>
                {data.years.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      {!data ? (
        <Loading what={`the splits of ${measure}`} rows={2} />
      ) : !data.available ? (
        <Empty>{data.reason}</Empty>
      ) : (
        <>
          {/* `items-start`, so a two-bar panel stays two bars tall instead of
              being stretched to match a nine-bar one beside it. */}
          {/* Time first and full width. It is the cut every reader asks for
              before any other, and a series squeezed into a half column loses
              the shape that is the whole reason to draw it. */}
          {data.over_time && (
            <Panel
              breakdown={data.over_time}
              measure={measure}
              heading={data.over_time.by}
              // Always chronological. A time series ranked by size is a bar
              // chart of months in a meaningless sequence.
              order="time"
            />
          )}
          {period && !data.over_time && (
            <Empty>
              {measure} could not be cut by {period} against this file&rsquo;s calendar.
            </Empty>
          )}

          <div className="grid items-start gap-3.5 lg:grid-cols-2">
            {data.breakdowns.map((breakdown) => (
              <Panel
                key={breakdown.by}
                breakdown={breakdown}
                measure={measure}
                order={order}
              />
            ))}
          </div>
          <p className="text-[11.5px] text-muted">
            Every chart here was produced by running {measure}&rsquo;s own SQL grouped by
            that column, against the rows this file carries. Where the measure adds up, the
            parts sum to the figure on the card, and a group beyond the tenth is folded
            into one slice carrying its value rather than dropped so the total still
            holds. Where it does not — an average, a ratio — the chart says so rather
            than printing a total that means nothing.
            {data.year != null && (
              <>
                {" "}
                Restricted to {data.year}: the year is applied in the query, before
                anything is added up, so every figure here is that year&rsquo;s.
              </>
            )}
            {data.dimensions.length > data.breakdowns.length && (
              <>
                {" "}
                {data.dimensions.length} columns in this model could be charted this way;
                the {data.breakdowns.length} above are one per table, so they show
                different angles rather than the same list drawn four times.
              </>
            )}
          </p>
        </>
      )}
    </section>
  );
}

/**
 * The measure on a map, or the reason there is none.
 *
 * Kept as its own section rather than folded in with the splits, because it
 * answers a question none of them can and fails for a reason none of them
 * share: a model either records where its rows happened or it does not, and no
 * amount of data makes coordinates appear. When it does not, the refusal is
 * the content -- a reader who is told "this file carries no coordinates" knows
 * something, and a reader shown nothing concludes the feature is broken.
 */
function Where({ measure }: { measure: string }) {
  const { data, error } = useLoad<MapPayload>(
    () => api.atlas(measure),
    [measure],
  );
  const [showSql, setShowSql] = useState(false);

  if (error) return null;

  return (
    <section className="flex flex-col gap-2.5 rounded border border-hairline bg-surface p-3.5">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 className="font-serif text-base font-semibold">Where {measure} happened</h3>
        {data?.available && (
          <span className="font-mono text-[10.5px] text-faint">
            {data.table}[{data.label_column}]
          </span>
        )}
        {data?.available && data.sql && (
          <button
            type="button"
            onClick={() => setShowSql((was) => !was)}
            className="ml-auto text-[11px] text-accent underline underline-offset-2"
          >
            {showSql ? "hide the query" : "the query"}
          </button>
        )}
      </div>

      {!data ? (
        <Loading what={`where ${measure} happened`} rows={2} />
      ) : !data.available ? (
        <Empty>{data.reason}</Empty>
      ) : (
        <>
          <div className="max-w-xl">
            <Atlas
              places={data.places}
              measure={measure}
              label={data.label_column || "location"}
            />
          </div>
          <p className="text-[11.5px] text-muted">
            Every point sits at the latitude and longitude this file records for it, and
            its area is {measure} there — run as {measure}&rsquo;s own SQL grouped by{" "}
            {data.table}[{data.label_column}]. There is no basemap under it because the
            page loads nothing from the network; what you are looking at came entirely
            out of the model.
          </p>
          {showSql && data.sql && <Code label="SQL">{`${data.sql}`}</Code>}
        </>
      )}
    </section>
  );
}

/** One chart, with the query that produced it behind a disclosure. */
function Panel({
  breakdown,
  measure,
  order,
  heading,
}: {
  breakdown: BreakdownPayload;
  measure: string;
  order: Order;
  /** Overrides "by {column}". A time cut is headed by the period, not by the
   *  date column it was cut from, because "by Date" is what the reader was
   *  trying to get away from. */
  heading?: string;
}) {
  const [showSql, setShowSql] = useState(false);
  // A chart whose groups have no place in time cannot honour "in date order",
  // so it keeps the ranking and says so, rather than silently ignoring the
  // control or shuffling into an order that means nothing.
  const timeable = canOrderByTime(breakdown.slices);
  const applied: Order = order === "time" && !timeable ? "largest" : order;
  const slices = arrange(breakdown.slices, applied);
  const kind = chartFor(slices, breakdown.additive, breakdown.column);
  // Which reading of the split is showing. Kept per panel rather than for the
  // section: a reader checking one figure exactly still wants the shape of the
  // others, and forcing every panel to switch together would take that away.
  const [as, setAs] = useState<"visual" | "tabular">("visual");

  return (
    <article className="flex min-w-0 flex-col gap-2 rounded border border-hairline bg-ground">
      {/* A rule in the accent across the top, and the column centred under it.
          Both are borrowed from the report this documents: it is the shape a
          Power BI reader already knows a panel by. */}
      <div className="h-[3px] rounded-t bg-accent" aria-hidden />
      <div className="flex flex-wrap items-baseline gap-x-2 px-3 pt-1">
        <h4 className="text-[13px] font-medium">{heading ?? `by ${breakdown.column}`}</h4>
        <span className="font-mono text-[10.5px] text-faint">{breakdown.table}</span>
        <button
          type="button"
          onClick={() => setShowSql((was) => !was)}
          className="ml-auto text-[11px] text-accent underline underline-offset-2"
        >
          {showSql ? "hide the query" : "the query"}
        </button>
      </div>
      <div className="flex flex-col gap-2 px-3 pb-3">

      {as === "tabular" ? (
        <Tabular
          slices={slices}
          by={breakdown.column}
          measure={measure}
          additive={breakdown.additive}
        />
      ) : kind === "donut" ? (
        <Donut slices={slices} by={breakdown.column} measure={measure} />
      ) : kind === "columns" ? (
        <Columns
          slices={slices}
          by={breakdown.column}
          measure={measure}
          additive={breakdown.additive}
        />
      ) : (
        <Bars
          slices={slices}
          by={breakdown.column}
          measure={measure}
          additive={breakdown.additive}
        />
      )}

      <p className="text-[11px] text-faint tabular">
        {breakdown.slices.length} group{breakdown.slices.length === 1 ? "" : "s"}
        {breakdown.additive ? ` · totals ${exact(breakdown.total)}` : ""}
        {breakdown.folded > 0 && ` · last slice folds ${breakdown.folded} smaller`}
      </p>
      {/* Said where the misreading would happen, not once at the bottom of the
          page: the sum of a set of averages is a number this chart can produce
          and nothing in the model means. */}
      {order === "time" && !timeable && (
        <p className="text-[11px] text-review">
          Ranked by size — these groups are not points in time, so there is no date
          order to put them in.
        </p>
      )}
      {!breakdown.additive && (
        <p className="text-[11px] text-review">
          These parts do not add up to a whole — {measure} is an average or a ratio, so
          the groups compare against each other but cannot be summed.
        </p>
      )}

      {showSql && <Code label="SQL">{breakdown.sql}</Code>}
      </div>

      {/* The switch sits at the foot of the panel, where the report this
          documents puts it. A chart answers "which is bigger"; the table
          answers "what is it, exactly" -- and in a documentation tool the
          exact figure is often the whole question. */}
      <div
        role="group"
        aria-label={`How to show ${measure} by ${breakdown.column}`}
        className="flex items-center justify-center gap-1 border-t border-hairline px-3 py-1.5"
      >
        {(["visual", "tabular"] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setAs(option)}
            aria-pressed={as === option}
            className={cx(
              "rounded-full px-3 py-0.5 text-[11px] capitalize",
              "transition-colors duration-(--duration-feedback)",
              as === option
                ? "bg-accent text-ground font-medium"
                : "text-muted hover:bg-raised",
            )}
          >
            {option}
          </button>
        ))}
      </div>
    </article>
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
