/**
 * The model, as a tree with a detail panel.
 *
 * A tree rather than a node-link canvas, on purpose. A semantic model is
 * overwhelmingly hierarchical -- tables own columns, measures and drill paths --
 * and a tree renders that at any size without layout, overlap or zoom, none of
 * which pay their way here. The genuinely graph-shaped question, "what breaks if
 * I change this", is still answered by naming the affected objects: a list is
 * exact, sorts, and does not need tracing.
 *
 * What a list cannot carry is distance. Two hops upstream and one hop upstream
 * are indistinguishable once both are names in a column, so the selected object
 * also gets a small chain drawn around it -- its ancestry and descent, bounded,
 * and nothing else. That is a different picture from a model map, and the
 * reason the tree above is not one.
 *
 * The whole tree comes from one `/api/graph` call. Loading each table's children
 * on expand would be a request per click for data measured in kilobytes.
 */
import { useEffect, useMemo, useState } from "react";
import { api, type GraphPayload, type MeasureDetail } from "@/lib/api";
import { Chip, Failure, Fingerprint, Loading, Panel } from "@/components/primitives";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { Lineage } from "@/components/Lineage";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";

type Node = GraphPayload["nodes"][number] & {
  name?: string;
  table?: string;
  expression?: string | null;
  fingerprint?: string;
  is_system?: boolean;
  is_measure_only?: boolean;
  target_expression?: string;
  status_expression?: string;
  trend_expression?: string;
  target_description?: string | null;
  status_description?: string | null;
  description?: string | null;
};

const CHILD_KINDS = ["measure", "column", "calculated_column", "hierarchy"] as const;

const KIND_LABEL: Record<string, string> = {
  measure: "fx",
  column: "col",
  calculated_column: "calc",
  hierarchy: "drill",
};

interface TableGroup {
  name: string;
  isSystem: boolean;
  /** Holds definitions rather than data — TMDL still requires one hidden
   *  placeholder column on such a table, and saying so is what stops that
   *  column reading as real data nobody can account for. */
  isMeasureOnly: boolean;
  children: Node[];
}

function group(nodes: Node[]): TableGroup[] {
  const tables = new Map<string, TableGroup>();
  for (const node of nodes) {
    if (node.kind === "table") {
      const existing = tables.get(node.name ?? "");
      tables.set(node.name ?? "", {
        name: node.name ?? "",
        isSystem: Boolean(node.is_system),
        isMeasureOnly: Boolean(node.is_measure_only),
        children: existing?.children ?? [],
      });
    }
  }
  for (const node of nodes) {
    if (!CHILD_KINDS.includes(node.kind as (typeof CHILD_KINDS)[number])) continue;
    const owner = node.table ?? "";
    if (!tables.has(owner)) {
      tables.set(owner, {
        name: owner,
        isSystem: false,
        isMeasureOnly: false,
        children: [],
      });
    }
    tables.get(owner)!.children.push(node);
  }

  const order = (node: Node) => CHILD_KINDS.indexOf(node.kind as (typeof CHILD_KINDS)[number]);
  return [...tables.values()]
    .map((table) => ({
      ...table,
      children: [...table.children].sort(
        (a, b) => order(a) - order(b) || (a.name ?? "").localeCompare(b.name ?? ""),
      ),
    }))
    // System tables exist but are noise; they sort last rather than vanish.
    .sort((a, b) => Number(a.isSystem) - Number(b.isSystem) || a.name.localeCompare(b.name));
}

export function Model() {
  const { data: graph, error, retrying, reload } = useLoad<GraphPayload>(() => api.graph(), []);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Node | null>(null);
  const [showKpis, setShowKpis] = useState(false);

  const groups = useMemo(() => (graph ? group(graph.nodes as Node[]) : []), [graph]);

  /**
   * The KPIs, which the tree above cannot show.
   *
   * A KPI is not a child of a table in the way a column or a measure is -- it
   * decorates one measure and says what the business calls an acceptable value
   * of it. Nesting it under a table would put it beside the measure it judges
   * and make the two look like the same kind of thing, which is the confusion
   * this panel exists to prevent: the measure says what the number *is*, the
   * KPI says what counts as good, and they change independently.
   */
  const kpis = useMemo(
    () =>
      ((graph?.nodes ?? []) as Node[])
        .filter((node) => node.kind === "kpi")
        .sort((a, b) => (a.name ?? "").localeCompare(b.name ?? "")),
    [graph],
  );

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return groups;
    return groups
      .map((table) => ({
        ...table,
        children: table.children.filter((child) =>
          (child.name ?? "").toLowerCase().includes(needle),
        ),
      }))
      .filter(
        (table) => table.children.length > 0 || table.name.toLowerCase().includes(needle),
      );
  }, [groups, filter]);

  // A filtered tree that still needs expanding hides its own results.
  const expanded = filter.trim() ? new Set(shown.map((t) => t.name)) : open;

  if (error)
    return (
      <Failure
        message={error.message}
        status={error.status}
        what="the semantic graph"
        onRetry={reload}
        retrying={retrying}
      />
    );
  if (!graph) return <Loading what="the semantic graph" rows={5} />;

  return (
    <div className="flex h-full min-h-0">
      <div className="flex w-64 flex-none flex-col border-r border-hairline">
        <div className="border-b border-hairline p-2">
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter objects"
            aria-label="Filter objects"
            className="w-full rounded border border-hairline bg-surface px-2 py-1.5 text-[13px] placeholder:text-faint"
          />
        </div>

        {/* Above the tree rather than inside it. A KPI belongs to the model,
            not to one table's list of children, and putting it at the top is
            also the only way anyone finds out the model has any: the previous
            build read KPIs, fingerprinted them and wrote them into the
            documents, and showed them on no screen at all. */}
        <button
          onClick={() => {
            setShowKpis(true);
            setSelected(null);
          }}
          aria-current={showKpis ? "true" : undefined}
          className={cx(
            "flex w-full flex-none items-center gap-1.5 border-b border-hairline px-2 py-1.5 text-left text-[13px]",
            showKpis ? "bg-accent-soft text-accent" : "text-muted hover:bg-raised hover:text-ink",
          )}
        >
          <span className="w-7 flex-none font-mono text-[9px] text-faint">kpi</span>
          <span className="truncate">Targets and thresholds</span>
          <span className="ml-auto font-mono text-[10px] text-faint tabular">
            {kpis.length}
          </span>
        </button>

        <div className="min-h-0 flex-1 overflow-auto py-1">
          {shown.map((table) => (
            <div key={table.name}>
              <button
                onClick={() =>
                  setOpen((previous) => {
                    const next = new Set(previous);
                    if (next.has(table.name)) next.delete(table.name);
                    else next.add(table.name);
                    return next;
                  })
                }
                className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[13px] hover:bg-raised"
              >
                <span className="w-2.5 font-mono text-[10px] text-faint">
                  {expanded.has(table.name) ? "−" : "+"}
                </span>
                <span className={cx("truncate", table.isSystem && "text-faint")}>
                  {table.name}
                </span>
                {table.isMeasureOnly && (
                  <span
                    title="Holds definitions rather than data"
                    className="font-mono text-[9px] text-accent"
                  >
                    fx
                  </span>
                )}
                <span className="ml-auto font-mono text-[10px] text-faint tabular">
                  {table.children.length}
                </span>
              </button>

              {expanded.has(table.name) &&
                table.children.map((child) => (
                  <button
                    key={child.id}
                    onClick={() => {
                      setSelected(child);
                      setShowKpis(false);
                    }}
                    aria-current={selected?.id === child.id ? "true" : undefined}
                    className={cx(
                      "flex w-full items-center gap-1.5 py-1 pr-2 pl-6 text-left text-[13px]",
                      selected?.id === child.id
                        ? "bg-accent-soft text-accent"
                        : "text-muted hover:bg-raised hover:text-ink",
                    )}
                  >
                    <span className="w-7 flex-none font-mono text-[9px] text-faint">
                      {KIND_LABEL[child.kind] ?? child.kind}
                    </span>
                    <span className="truncate">{child.name}</span>
                  </button>
                ))}
            </div>
          ))}
          {shown.length === 0 && (
            <p className="p-3 text-xs text-muted">Nothing matches “{filter}”.</p>
          )}
        </div>

        <footer className="border-t border-hairline px-2 py-1.5 font-mono text-[10px] text-faint tabular">
          {graph.stats.nodes} objects · {graph.stats.edges} edges
        </footer>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {showKpis ? (
          <Kpis
            kpis={kpis}
            model={graph.model.name}
            onSelectMeasure={(id) => {
              const found = (graph.nodes as Node[]).find((node) => node.id === id);
              if (found) {
                setSelected(found);
                setShowKpis(false);
              }
            }}
          />
        ) : selected ? (
          <Detail
            node={selected}
            graph={graph}
            onSelect={(id) => {
              const found = (graph.nodes as Node[]).find((node) => node.id === id);
              if (found) setSelected(found);
            }}
          />
        ) : (
          <p className="p-4 text-sm text-muted">
            Select an object to see how it is defined and what depends on it.
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Every KPI in the model, with the DAX behind each threshold.
 *
 * Three expressions, shown separately rather than as the one joined string the
 * graph node also carries. That string is what the fingerprint covers and what
 * drift reports as the object's detail, so it exists for a reason -- but taking
 * it apart again here would be a second parse of text this codebase just
 * finished assembling, and the two would eventually disagree.
 *
 * Blank thresholds are named as blank rather than omitted. "This KPI has no
 * trend rule" is a finding for whoever is reviewing whether the report's
 * red/amber/green means anything; a row quietly missing from a list is not.
 */
function Kpis({
  kpis,
  model,
  onSelectMeasure,
}: {
  kpis: Node[];
  model: string;
  onSelectMeasure: (id: string) => void;
}) {
  if (kpis.length === 0)
    return (
      <div className="p-4">
        <h2 className="font-serif text-lg font-semibold">Targets and thresholds</h2>
        <p className="mt-1.5 max-w-prose text-sm text-muted">
          {model} defines no KPIs. A measure says what a number is; a KPI attaches a
          target to it and a rule for when the report shows that number as good, at
          risk or bad. This model states the numbers without stating what counts as
          an acceptable value of them, so nothing here can be read as a target
          anybody agreed to.
        </p>
        <p className="mt-2 max-w-prose text-xs text-faint">
          In Power BI these are set on a measure under <em>Add KPI</em>. If this model
          is meant to have them, they are missing from the file rather than from this
          page.
        </p>
      </div>
    );

  return (
    <div className="flex flex-col gap-3 p-4">
      <header>
        <h2 className="font-serif text-lg font-semibold">Targets and thresholds</h2>
        <p className="mt-1 max-w-prose text-sm text-muted">
          What {model} treats as an acceptable value, not what it calculates. Each of
          these decorates a measure: the measure gives the figure, the rules below
          decide the colour the report shows it in.
        </p>
      </header>

      {kpis.map((kpi) => (
        <article key={kpi.id} className="rounded border border-hairline bg-ground">
          <header className="flex flex-wrap items-center gap-2 border-b border-hairline px-3.5 py-2">
            <h3 className="text-sm font-semibold">{kpi.name}</h3>
            <Chip tone="accent">kpi</Chip>
            <button
              onClick={() => onSelectMeasure(`measure:${kpi.table}[${kpi.name}]`)}
              className="font-mono text-[11px] text-accent underline-offset-2 hover:underline"
              title="Open the measure this KPI judges"
            >
              {kpi.table}[{kpi.name}]
            </button>
            <span className="ml-auto">
              <Fingerprint
                value={(kpi.fingerprint ?? "").slice(0, 12)}
                full={kpi.fingerprint}
              />
            </span>
          </header>

          {kpi.description && (
            <p className="border-b border-hairline px-3.5 py-2 text-[12.5px] text-muted">
              {kpi.description}
            </p>
          )}

          <dl className="divide-y divide-hairline">
            {(
              [
                {
                  label: "target",
                  says: "the figure this is measured against",
                  expression: kpi.target_expression,
                  described: kpi.target_description,
                },
                {
                  label: "status",
                  says: "which values count as good, at risk or bad",
                  expression: kpi.status_expression,
                  described: kpi.status_description,
                },
                {
                  label: "trend",
                  says: "whether it is moving the right way",
                  expression: kpi.trend_expression,
                  described: null,
                },
              ] as const
            ).map((rule) => (
              <div key={rule.label} className="px-3.5 py-2.5">
                <dt className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                    {rule.label}
                  </span>
                  <span className="text-[11.5px] text-faint">{rule.says}</span>
                  {rule.described && (
                    <span className="text-[11.5px] text-muted">· {rule.described}</span>
                  )}
                </dt>
                <dd className="mt-1">
                  {rule.expression ? (
                    <pre className="overflow-x-auto rounded border border-hairline bg-surface px-2.5 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap">
                      {rule.expression}
                    </pre>
                  ) : (
                    <p className="text-[12px] text-faint">
                      Not set. The model attaches no {rule.label} rule to this KPI.
                    </p>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </article>
      ))}
    </div>
  );
}

function Detail({
  node,
  graph,
  onSelect,
}: {
  node: Node;
  graph: GraphPayload;
  onSelect: (id: string) => void;
}) {
  const [measure, setMeasure] = useState<MeasureDetail | null>(null);
  const [impact, setImpact] = useState<string[] | null>(null);
  type Trouble = { status: number; message: string; didYouMean?: string[] };
  const [impactProblem, setImpactProblem] = useState<Trouble | null>(null);
  const [problem, setProblem] = useState<Trouble | null>(null);

  useEffect(() => {
    setMeasure(null);
    setImpact(null);
    setImpactProblem(null);
    setProblem(null);

    void (async () => {
      const affected = await api.impact(node.name ?? "");
      // A swallowed failure would leave this panel loading for ever, which
      // reads as "still working" rather than "this did not answer".
      if (affected.ok) setImpact(affected.data.would_be_affected);
      else
        setImpactProblem({ status: affected.status, message: affected.message });
    })();

    if (node.kind !== "measure") return;
    void (async () => {
      const result = await api.measure(node.name ?? "");
      if (result.ok) setMeasure(result.data);
      else
        setProblem({
          status: result.status,
          message: result.message,
          didYouMean: result.didYouMean,
        });
    })();
  }, [node]);

  return (
    <div className="flex flex-col gap-3 p-4">
      <header>
        <div className="flex items-center gap-2">
          <h2 className="font-serif text-xl font-semibold">{node.name}</h2>
          <Chip>{node.kind.replace("_", " ")}</Chip>
        </div>
        {node.table && (
          <p className="mt-0.5 font-mono text-xs text-faint">in {node.table}</p>
        )}
      </header>

      {problem && (
        <Failure
          message={problem.message}
          status={problem.status}
          hint={problem.didYouMean}
          what="this measure"
        />
      )}

      {measure?.description && <p className="text-sm text-muted">{measure.description}</p>}

      {measure?.behaviours && measure.behaviours.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {measure.behaviours.map((behaviour) => (
            <Chip key={behaviour.label} tone="accent" title={behaviour.meaning}>
              {behaviour.label}
            </Chip>
          ))}
        </div>
      )}

      {measure && (
        <EvidenceDrawer
          subject={measure.name}
          expression={measure.expression}
          canonical={measure.canonical}
          fingerprint={measure.fingerprint_full}
        />
      )}

      {!measure && node.expression && (
        <Panel title="Definition">
          <pre className="overflow-x-auto px-3.5 py-2.5 font-mono text-[11.5px] whitespace-pre-wrap">
            {node.expression}
          </pre>
        </Panel>
      )}

      {/* The chain, then the two lists. The lists answer "what" exactly; the
          chain answers "in what order, and how far away", which a flat list of
          names cannot -- a column two hops upstream reads as a peer of the
          measure one hop upstream once both are just names in a column. */}
      <Panel title="Lineage">
        <Lineage graph={graph} focus={node.id} onSelect={onSelect} />
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        {measure && (
          <Panel title={`Reads (${measure.depends_on.length})`}>
            {measure.depends_on.length === 0 ? (
              <p className="p-3 text-xs text-muted">
            Nothing — this one is calculated directly from columns, not from other
            measures.
          </p>
            ) : (
              <ul className="divide-y divide-hairline">
                {measure.depends_on.map((id) => (
                  <li key={id} className="px-3.5 py-1.5 font-mono text-[11.5px] text-muted">
                    {id}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}

        {/* The graph-shaped question, answered as a list. This is what a
            reviewer actually needs before changing something. */}
        <Panel
          title={impact ? `Breaks if changed (${impact.length})` : "Breaks if changed"}
        >
          {impactProblem ? (
            <Failure
              message={impactProblem.message}
              status={impactProblem.status}
              what="the impact list"
            />
          ) : impact === null ? (
            <Loading what="impact" />
          ) : impact.length === 0 ? (
            <p className="p-3 text-xs text-muted">
              Nothing else reads this. Safe to change in isolation.
            </p>
          ) : (
            <ul className="divide-y divide-hairline">
              {impact.map((id) => (
                <li key={id} className="px-3.5 py-1.5 font-mono text-[11.5px] text-muted">
                  {id}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
