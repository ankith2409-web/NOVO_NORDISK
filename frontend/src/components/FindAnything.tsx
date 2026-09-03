/**
 * One box that finds anything in the model, from anywhere in the interface.
 *
 * This exists because of a complaint that was really about navigation. Asked
 * what she wanted, a reviewer said: "Make it very simple, that's what I'm
 * trying to tell... Whatever you have taken is very complex. I don't need so
 * much." She was looking at a rail of tabs when she said it.
 *
 * Cutting tabs only moves that problem around. The actual cost was that finding
 * one named thing required knowing which page keeps that *kind* of thing:
 * measures on the dataset page, tiles on the dashboard page, columns under
 * browse. Every one of those is a question about this tool's furniture, and
 * nobody opens a documentation tool to answer one. So: type the name, press
 * Enter, arrive at the thing. The tabs stay for people who want to read a whole
 * page; nobody has to use them to look something up.
 *
 * Opened with ⌘K, Ctrl-K or "/" from anywhere that is not already a text field.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SearchHit } from "@/lib/api";
import { SearchIcon } from "@/components/icons";
import { cx } from "@/lib/cx";

/**
 * Long enough that a fast typist sends one request instead of eight, short
 * enough that the list feels attached to the keyboard.
 */
const SETTLE_MS = 140;

/** What each kind is called, and how it is coloured. */
const KINDS: Record<string, { label: string; tone: string }> = {
  measure: { label: "measure", tone: "text-accent" },
  kpi: { label: "KPI", tone: "text-ok" },
  tile: { label: "tile", tone: "text-muted" },
  table: { label: "table", tone: "text-muted" },
  hierarchy: { label: "drill path", tone: "text-muted" },
  column: { label: "column", tone: "text-faint" },
  requirement: { label: "requirement", tone: "text-muted" },
};

export function FindAnything({
  open,
  onClose,
  onGo,
}: {
  open: boolean;
  onClose: () => void;
  /** Show `target` on `view`. The caller owns navigation; this owns finding. */
  onGo: (view: string, target: string, kind: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState(0);
  const box = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  // Every response carries the query it answered, and a stale one is dropped
  // rather than rendered. Without this, typing "sales" fast enough shows the
  // results for "sal" whenever that request happens to land second.
  const asked = useRef("");

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setHits([]);
    setTruncated(false);
    setCursor(0);
    box.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const wanted = query.trim();
    asked.current = wanted;
    if (!wanted) {
      setHits([]);
      setTruncated(false);
      setBusy(false);
      return;
    }
    setBusy(true);
    const timer = window.setTimeout(async () => {
      const result = await api.search(wanted);
      if (asked.current !== wanted) return;
      setBusy(false);
      if (!result.ok) {
        setHits([]);
        setTruncated(false);
        return;
      }
      setHits(result.data.results);
      setTruncated(result.data.truncated);
      setCursor(0);
    }, SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [query, open]);

  // The highlighted row is kept in view as the arrows move it. `nearest` rather
  // than `center`, so a single step down does not jump the whole list.
  useEffect(() => {
    list.current?.children[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const choose = useCallback(
    (hit: SearchHit) => {
      onGo(hit.view, hit.target, hit.kind);
      onClose();
    },
    [onGo, onClose],
  );

  if (!open) return null;

  function onKey(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
      event.preventDefault();
      setCursor((at) => (hits.length ? (at + 1) % hits.length : 0));
      return;
    }
    if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
      event.preventDefault();
      setCursor((at) => (hits.length ? (at - 1 + hits.length) % hits.length : 0));
      return;
    }
    if (event.key === "Enter" && hits[cursor]) {
      event.preventDefault();
      choose(hits[cursor]);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-ink/40 p-4 pt-[12vh]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Find anything in this model"
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[70vh] w-full max-w-xl flex-col overflow-hidden rounded-lg border border-hairline bg-ground shadow-xl animate-(--animate-rise)"
      >
        <div className="flex items-center gap-2.5 border-b border-hairline px-3.5 py-3">
          <SearchIcon size={15} className="flex-none text-faint" />
          <input
            ref={box}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKey}
            placeholder="Find a measure, table, column, KPI or tile…"
            aria-label="Find anything in this model"
            // The list is the thing being controlled, and the input is what
            // keeps focus while the arrows move through it.
            role="combobox"
            aria-expanded={hits.length > 0}
            aria-controls="find-results"
            aria-activedescendant={hits[cursor] ? `find-hit-${cursor}` : undefined}
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint"
          />
          <kbd className="hidden flex-none rounded border border-hairline px-1.5 py-0.5 font-mono text-[10px] text-faint sm:block">
            esc
          </kbd>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {query.trim() === "" ? (
            <p className="px-3.5 py-6 text-center text-[13px] text-muted">
              Type any name from the model. Measures, tables, columns, drill paths
              and the tiles on the report are all searched at once — you do not
              need to know which page keeps which.
            </p>
          ) : hits.length === 0 ? (
            <p className="px-3.5 py-6 text-center text-[13px] text-muted">
              {busy ? "Looking…" : (
                <>
                  Nothing in this model is called{" "}
                  <span className="font-mono text-ink">{query.trim()}</span>. Formulas
                  are searched too, so a measure that merely mentions it would have
                  appeared here.
                </>
              )}
            </p>
          ) : (
            <ul id="find-results" ref={list} role="listbox" className="py-1">
              {hits.map((hit, index) => {
                const kind = KINDS[hit.kind] ?? { label: hit.kind, tone: "text-muted" };
                return (
                  <li
                    key={`${hit.kind}-${hit.context}-${hit.name}-${index}`}
                    id={`find-hit-${index}`}
                    role="option"
                    aria-selected={index === cursor}
                    // `mousedown` rather than `click`: the input holds focus,
                    // and a click that first blurs it can close the dialog out
                    // from under the pointer before the choice registers.
                    onMouseDown={(event) => {
                      event.preventDefault();
                      choose(hit);
                    }}
                    onMouseMove={() => setCursor(index)}
                    className={cx(
                      "flex cursor-pointer items-baseline gap-2 px-3.5 py-2",
                      index === cursor && "bg-accent-soft",
                    )}
                  >
                    <span
                      className={cx(
                        "w-16 flex-none font-mono text-[10px] tracking-[0.06em] uppercase",
                        kind.tone,
                      )}
                    >
                      {kind.label}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-medium">
                        {hit.name}
                        {hit.context && (
                          <span className="ml-1.5 font-mono text-[11px] font-normal text-faint">
                            {hit.context}
                          </span>
                        )}
                      </span>
                      {hit.detail && (
                        <span className="block truncate font-mono text-[11px] text-muted">
                          {hit.detail}
                        </span>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {(hits.length > 0 || truncated) && (
          <div className="flex items-center justify-between gap-2 border-t border-hairline px-3.5 py-2 font-mono text-[10px] text-faint">
            <span>↑↓ to move · enter to open</span>
            {truncated && <span>more matches than shown — type a little more</span>}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * ⌘K, Ctrl-K, or "/" from anywhere that is not already a text field.
 *
 * The last exclusion is the one that matters: "/" is a perfectly ordinary
 * character, and stealing it while somebody is writing a note in the review
 * queue would be the kind of shortcut people disable the whole feature over.
 */
export function useFindShortcut(onOpen: () => void) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.tagName === "SELECT" ||
        target?.isContentEditable;

      if ((event.key === "k" || event.key === "K") && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        onOpen();
        return;
      }
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        onOpen();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onOpen]);
}
