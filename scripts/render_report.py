#!/usr/bin/env python3
"""Render the extracted semantic graphs as one readable HTML document.

Reads data/out/*.json -- the literal output of `concordance extract` -- and
formats it for a human. Nothing here is retyped by hand: every table name,
DAX expression, and relationship comes straight from the JSON, so the report
cannot drift from what the extractor actually found.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "out"
MODELS = ["Sales_Returns_Sample", "AdventureWorks_Sales", "Supply_Chain_Sample"]


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def load(name: str) -> dict:
    return json.loads((OUT_DIR / f"{name}.json").read_text())


def nodes_of(graph: dict, kind: str) -> list[dict]:
    return [n for n in graph["nodes"] if n.get("kind") == kind]


def edges_of(graph: dict, kind: str) -> list[dict]:
    return [e for e in graph["edges"] if e.get("kind") == kind]


def node_by_id(graph: dict, node_id: str) -> dict | None:
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n
    return None


def measure_deps(graph: dict, measure_id: str) -> list[str]:
    return sorted(
        e["target"].split(":", 1)[1]
        for e in graph["edges"]
        if e["source"] == measure_id and e.get("kind") == "references"
    )


def render_model(graph: dict) -> str:
    model = graph["model"]
    tables = sorted(nodes_of(graph, "table"), key=lambda t: t["name"])
    measures = sorted(nodes_of(graph, "measure"), key=lambda m: (m["table"], m["name"]))
    calc_cols = sorted(nodes_of(graph, "calculated_column"), key=lambda c: (c["table"], c["name"]))
    hierarchies = sorted(nodes_of(graph, "hierarchy"), key=lambda h: (h["table"], h["name"]))
    joins = sorted(edges_of(graph, "joins"), key=lambda e: (e["source"], e["target"]))
    unresolved = graph.get("unresolved", [])
    gaps = graph.get("coverage_gaps", [])

    anchor = model["name"]
    parts: list[str] = []

    parts.append(f'<section class="model" id="{esc(anchor)}">')
    parts.append(f'<h2 class="model-title">{esc(model["name"].replace("_", " "))}</h2>')
    parts.append(f'<p class="model-meta">source: <code>{esc(Path(model["source_path"]).name)}</code> &middot; '
                  f'extractor: <code>{esc(model["source_type"])}</code></p>')

    # -- stat strip -----------------------------------------------------
    parts.append('<div class="stats">')
    stat_items = [
        ("Tables", model["summary"]["user_tables"], f'of {model["summary"]["tables"]} total'),
        ("Columns", model["summary"]["columns"], f'{model["summary"]["calculated_columns"]} calculated'),
        ("Measures", model["summary"]["measures"], "DAX-defined"),
        ("Relationships", len(joins), "joins between tables"),
        ("Hierarchies", model["summary"]["user_hierarchies"], f'of {model["summary"]["hierarchies"]} total'),
    ]
    for label, value, sub in stat_items:
        parts.append(f'<div class="stat"><div class="stat-value">{esc(value)}</div>'
                      f'<div class="stat-label">{esc(label)}</div>'
                      f'<div class="stat-sub">{esc(sub)}</div></div>')
    parts.append('</div>')

    # -- coverage / integrity banner -------------------------------------
    if unresolved or gaps:
        parts.append('<div class="banner warn">')
        if unresolved:
            parts.append(f'<p><strong>{len(unresolved)} unresolved reference'
                          f'{"s" if len(unresolved) != 1 else ""}</strong> -- '
                          f'objects the model refers to that could not be matched:</p><ul>')
            for u in unresolved:
                parts.append(f'<li><code>{esc(u["source"])}</code> &rarr; '
                              f'<code>{esc(u["target"])}</code> &mdash; {esc(u["reason"])}</li>')
            parts.append('</ul>')
        if gaps:
            parts.append(f'<p><strong>{len(gaps)} feature type'
                          f'{"s" if len(gaps) != 1 else ""} present but not extracted:</strong></p><ul>')
            for g in gaps:
                parts.append(f'<li>{esc(g["count"])} &times; {esc(g["feature"])} &mdash; {esc(g["reason"])}</li>')
            parts.append('</ul>')
        parts.append('</div>')
    else:
        parts.append('<div class="banner ok"><p>No unresolved references. '
                      'No unextracted model features detected.</p></div>')

    # -- tables -----------------------------------------------------------
    parts.append('<h3>Tables</h3>')
    parts.append('<div class="table-wrap"><table><thead><tr>'
                  '<th>Table</th><th>Type</th><th class="num">Columns</th>'
                  '<th class="num">Measures</th></tr></thead><tbody>')
    for t in tables:
        col_count = sum(1 for c in graph["nodes"]
                         if c.get("kind") in ("column", "calculated_column") and c.get("table") == t["name"])
        m_count = sum(1 for m in measures if m["table"] == t["name"])
        kind = "system" if t.get("is_system") else ("measure-only" if t.get("is_measure_only") else "data")
        parts.append(f'<tr><td>{esc(t["name"])}</td><td><span class="pill {kind}">{kind}</span></td>'
                      f'<td class="num">{col_count}</td><td class="num">{m_count}</td></tr>')
    parts.append('</tbody></table></div>')

    # -- relationships ------------------------------------------------------
    if joins:
        parts.append('<h3>Relationships (joins)</h3>')
        parts.append('<div class="table-wrap"><table><thead><tr>'
                      '<th>From</th><th>To</th><th>Cardinality</th><th>Cross filter</th><th>Active</th>'
                      '</tr></thead><tbody>')
        for j in joins:
            from_t = j["source"].split(":", 1)[1]
            to_t = j["target"].split(":", 1)[1]
            active = '<span class="pill ok-pill">active</span>' if j.get("is_active") else '<span class="pill warn-pill">inactive</span>'
            parts.append(f'<tr><td><code>{esc(from_t)}[{esc(j["from_column"])}]</code></td>'
                          f'<td><code>{esc(to_t)}[{esc(j["to_column"])}]</code></td>'
                          f'<td>{esc(j["cardinality"])}</td><td>{esc(j["cross_filter"])}</td>'
                          f'<td>{active}</td></tr>')
        parts.append('</tbody></table></div>')

    # -- hierarchies --------------------------------------------------------
    if hierarchies:
        parts.append('<h3>Hierarchies</h3>')
        parts.append('<div class="table-wrap"><table><thead><tr>'
                      '<th>Table</th><th>Name</th><th>Drill path</th><th>Type</th></tr></thead><tbody>')
        for h in hierarchies:
            kind = "system" if any(t["name"] == h["table"] and t.get("is_system") for t in tables) else "business"
            parts.append(f'<tr><td>{esc(h["table"])}</td><td>{esc(h["name"])}</td>'
                          f'<td>{esc(h.get("path",""))}</td><td><span class="pill {kind}">{kind}</span></td></tr>')
        parts.append('</tbody></table></div>')

    # -- measures, grouped by table ------------------------------------------
    if measures:
        parts.append(f'<h3>Measures ({len(measures)})</h3>')
        by_table: dict[str, list[dict]] = {}
        for m in measures:
            by_table.setdefault(m["table"], []).append(m)
        for table_name in sorted(by_table):
            parts.append(f'<h4 class="measure-group">{esc(table_name)}</h4>')
            for m in by_table[table_name]:
                deps = measure_deps(graph, m["id"])
                fp = esc(m.get("fingerprint", ""))[:12]
                parts.append('<div class="measure">')
                parts.append(f'<div class="measure-head"><span class="measure-name">{esc(m["name"])}</span>'
                              f'<span class="fp" title="fingerprint">{fp}</span></div>')
                parts.append(f'<pre class="dax"><code>{esc(m.get("expression","").strip())}</code></pre>')
                if deps:
                    parts.append(f'<div class="deps">depends on: {", ".join(f"<code>{esc(d)}</code>" for d in deps)}</div>')
                parts.append('</div>')

    # -- calculated columns ---------------------------------------------------
    if calc_cols:
        parts.append(f'<h3>Calculated columns ({len(calc_cols)})</h3>')
        for c in calc_cols:
            parts.append('<div class="measure">')
            parts.append(f'<div class="measure-head"><span class="measure-name">{esc(c["table"])}[{esc(c["name"])}]</span></div>')
            parts.append(f'<pre class="dax"><code>{esc(c.get("expression","").strip())}</code></pre>')
            parts.append('</div>')

    parts.append('</section>')
    return "\n".join(parts)


def build_nav() -> str:
    items = []
    for name in MODELS:
        graph = load(name)
        s = graph["model"]["summary"]
        items.append(
            f'<a href="#{esc(name)}" class="nav-item">'
            f'<span class="nav-name">{esc(name.replace("_"," "))}</span>'
            f'<span class="nav-meta">{s["measures"]} measures &middot; {s["tables"]} tables</span></a>'
        )
    return "\n".join(items)


TEMPLATE = """<title>Semantic Layer Export &mdash; Concordance</title>
<style>
:root {{
  --paper: #F1F3F5; --paper-2: #E7EBEE; --card: #FFFFFF;
  --ink: #14202B; --ink-2: #37464F; --graphite: #5C6B74; --graphite-2: #8896A0;
  --rule: #D3DBE0;
  --accent: #2C5985; --accent-dim: #DCE8F2;
  --ok: #2E7D5B; --ok-bg: #E4F3EC;
  --warn: #9A6B1E; --warn-bg: #FBF0DD;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  --serif: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper: #0F1417; --paper-2: #161C20; --card: #171E22;
    --ink: #E6EDF0; --ink-2: #C2CDD2; --graphite: #93A2A9; --graphite-2: #6C7A80;
    --rule: #2A343A; --accent: #6FA8D8; --accent-dim: #1B2C3A;
    --ok: #5FBE93; --ok-bg: #14261F; --warn: #D7A94E; --warn-bg: #2C2413;
  }}
}}
:root[data-theme="dark"] {{
  --paper: #0F1417; --paper-2: #161C20; --card: #171E22;
  --ink: #E6EDF0; --ink-2: #C2CDD2; --graphite: #93A2A9; --graphite-2: #6C7A80;
  --rule: #2A343A; --accent: #6FA8D8; --accent-dim: #1B2C3A;
  --ok: #5FBE93; --ok-bg: #14261F; --warn: #D7A94E; --warn-bg: #2C2413;
}}
* {{ box-sizing: border-box; }}
body {{ background: var(--paper); color: var(--ink-2); font-family: var(--sans);
  line-height: 1.6; font-size: 15px; -webkit-font-smoothing: antialiased; }}
.wrap {{ max-width: 76rem; margin: 0 auto; padding: 0 1.5rem 6rem; }}
header.top {{ border-bottom: 1px solid var(--rule); padding: 3rem 0 2rem; margin-bottom: 2.5rem; }}
.eyebrow {{ font-family: var(--mono); font-size: 0.72rem; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--graphite); margin-bottom: 1rem; }}
h1 {{ font-family: var(--serif); font-size: 2.25rem; color: var(--ink); font-weight: 600;
  letter-spacing: -0.01em; text-wrap: balance; margin-bottom: 0.75rem; }}
.lede {{ max-width: 46rem; color: var(--graphite); font-size: 1.02rem; }}
.lede code {{ font-family: var(--mono); }}

nav.toc {{ display: grid; gap: 0.75rem; grid-template-columns: repeat(auto-fit, minmax(15rem,1fr));
  margin-bottom: 3.5rem; }}
.nav-item {{ display: flex; flex-direction: column; gap: 0.25rem; background: var(--card);
  border: 1px solid var(--rule); border-radius: 6px; padding: 1rem 1.2rem; text-decoration: none; }}
.nav-name {{ font-weight: 650; color: var(--ink); font-size: 1rem; }}
.nav-meta {{ font-family: var(--mono); font-size: 0.75rem; color: var(--graphite); }}

section.model {{ margin-bottom: 4.5rem; padding-top: 1.5rem; border-top: 1px solid var(--rule); }}
.model-title {{ font-family: var(--serif); font-size: 1.7rem; color: var(--ink); font-weight: 600;
  margin-bottom: 0.35rem; }}
.model-meta {{ font-family: var(--mono); font-size: 0.8rem; color: var(--graphite); margin-bottom: 1.5rem; }}
.model-meta code {{ background: var(--paper-2); padding: 0.1em 0.4em; border-radius: 3px; }}

.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(8.5rem,1fr)); gap: 1px;
  background: var(--rule); border: 1px solid var(--rule); border-radius: 6px; overflow: hidden; margin-bottom: 1.5rem; }}
.stat {{ background: var(--card); padding: 0.9rem 1rem; }}
.stat-value {{ font-family: var(--serif); font-size: 1.6rem; color: var(--accent); font-variant-numeric: tabular-nums; }}
.stat-label {{ font-size: 0.78rem; color: var(--ink-2); font-weight: 600; margin-top: 0.15rem; }}
.stat-sub {{ font-size: 0.7rem; color: var(--graphite-2); }}

.banner {{ border-radius: 6px; padding: 0.9rem 1.2rem; margin-bottom: 1.75rem; font-size: 0.88rem; }}
.banner.ok {{ background: var(--ok-bg); color: var(--ok); }}
.banner.warn {{ background: var(--warn-bg); color: var(--warn); }}
.banner p {{ margin: 0 0 0.4rem; }}
.banner ul {{ margin: 0; padding-left: 1.2rem; }}
.banner li {{ margin-bottom: 0.25rem; }}
.banner code {{ font-family: var(--mono); background: rgba(0,0,0,0.06); padding: 0.05em 0.35em; border-radius: 3px; }}

h3 {{ font-size: 1.05rem; color: var(--ink); font-weight: 650; margin: 2rem 0 0.9rem; }}
h4.measure-group {{ font-family: var(--mono); font-size: 0.82rem; letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--accent); margin: 1.75rem 0 0.75rem; padding-bottom: 0.35rem; border-bottom: 1px dashed var(--rule); }}

.table-wrap {{ overflow-x: auto; margin-bottom: 1rem; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.86rem; min-width: 32rem; }}
th {{ text-align: left; font-family: var(--mono); font-size: 0.68rem; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--graphite-2); font-weight: 500; padding: 0 0.75rem 0.5rem 0;
  border-bottom: 1px solid var(--rule); white-space: nowrap; }}
td {{ padding: 0.55rem 0.75rem 0.55rem 0; border-bottom: 1px solid var(--rule); color: var(--ink-2); }}
td:first-child {{ color: var(--ink); font-weight: 560; }}
td.num {{ font-variant-numeric: tabular-nums; text-align: right; }}
td code {{ font-family: var(--mono); font-size: 0.82em; }}

.pill {{ font-family: var(--mono); font-size: 0.68rem; padding: 0.1em 0.5em; border-radius: 3px;
  border: 1px solid currentColor; white-space: nowrap; }}
.pill.data {{ color: var(--graphite); }}
.pill.system {{ color: var(--graphite-2); }}
.pill.measure-only {{ color: var(--accent); }}
.pill.business {{ color: var(--accent); }}
.pill.ok-pill {{ color: var(--ok); }}
.pill.warn-pill {{ color: var(--warn); }}

.measure {{ background: var(--card); border: 1px solid var(--rule); border-radius: 6px;
  padding: 0.85rem 1.1rem; margin-bottom: 0.7rem; }}
.measure-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.5rem; }}
.measure-name {{ font-weight: 620; color: var(--ink); font-size: 0.92rem; }}
.fp {{ font-family: var(--mono); font-size: 0.68rem; color: var(--graphite-2); }}
pre.dax {{ background: var(--paper-2); border-radius: 4px; padding: 0.6rem 0.8rem; overflow-x: auto;
  margin: 0 0 0.5rem; }}
pre.dax code {{ font-family: var(--mono); font-size: 0.82rem; color: var(--ink-2); white-space: pre-wrap;
  word-break: break-word; }}
.deps {{ font-family: var(--mono); font-size: 0.72rem; color: var(--graphite); }}
.deps code {{ background: var(--paper-2); padding: 0.05em 0.35em; border-radius: 3px; }}

footer {{ border-top: 1px solid var(--rule); margin-top: 3rem; padding-top: 1.25rem;
  font-family: var(--mono); font-size: 0.72rem; color: var(--graphite-2); line-height: 1.8; }}
</style>
<div class="wrap">
  <header class="top">
    <div class="eyebrow">Concordance &middot; extracted semantic layer &middot; generated, not hand-written</div>
    <h1>Your three Power BI models, fully documented</h1>
    <p class="lede">Every table, join, hierarchy, measure and DAX expression below was read directly out of the
      <code>.pbix</code> files you provided by the extraction engine itself &mdash; this page is rendered
      straight from <code>concordance extract</code>'s JSON output, so nothing here was retyped by hand.</p>
  </header>
  <nav class="toc">
{nav}
  </nav>
{sections}
  <footer>
    Source files: Sales_Returns_Sample.pbix &middot; AdventureWorks_Sales.pbix &middot; Supply_Chain_Sample.pbix
    (Microsoft's public Power BI samples).<br>
    Generated by <code>scripts/render_report.py</code> from <code>concordance extract</code> output &mdash;
    re-run either to refresh this document from the current extraction code.
  </footer>
</div>
"""


def main() -> None:
    sections = "\n".join(render_model(load(name)) for name in MODELS)
    nav = build_nav()
    out = TEMPLATE.format(nav=nav, sections=sections)
    dest = ROOT / "data" / "out" / "semantic-layer-export.html"
    dest.write_text(out, encoding="utf-8")
    print(f"wrote {dest}  ({dest.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
