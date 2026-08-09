/**
 * The shell.
 *
 * One persistent frame with a swappable centre, rather than separate pages. The
 * copilot in particular must not unmount when you move from measures to drift --
 * losing the conversation because you looked something up is exactly the friction
 * this tool exists to remove.
 *
 * Views that depend on something configured at launch are hidden rather than
 * shown broken: the overview reports which capabilities exist, and the rail
 * renders accordingly.
 */
import { useEffect, useState } from "react";
import {
  api,
  SNAPSHOT_MODE,
  type LoadedModel,
  type Overview as OverviewData,
} from "@/lib/api";
import { Copilot } from "@/components/Copilot";
import { Overview } from "@/views/Overview";
import { Model } from "@/views/Model";
import { Requirements } from "@/views/Requirements";
import { Drift } from "@/views/Drift";
import { Reconcile } from "@/views/Reconcile";
import { Review } from "@/views/Review";
import { cx } from "@/lib/cx";

type ViewId = "overview" | "model" | "requirements" | "drift" | "reconcile" | "review";

const VIEWS: { id: ViewId; label: string; needs?: "drift" | "reconcile" }[] = [
  { id: "overview", label: "Overview" },
  { id: "model", label: "Model" },
  { id: "requirements", label: "Requirements" },
  { id: "drift", label: "Drift", needs: "drift" },
  { id: "reconcile", label: "Reconcile", needs: "reconcile" },
  { id: "review", label: "Review" },
];

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const stored = localStorage.getItem("concordance-theme");
    if (stored === "light" || stored === "dark") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("concordance-theme", theme);
  }, [theme]);

  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")] as const;
}

/** Wide enough to dock the copilot beside the work rather than over it. */
const DOCKED = "(min-width: 1024px)";

export default function App() {
  const [view, setView] = useState<ViewId>("overview");
  const [overview, setOverview] = useState<OverviewData | null>(null);
  // Only used to draw the switcher. Which model is *active* lives in the API
  // client, so a request cannot be issued from a view that never heard about
  // the switch.
  const [loaded, setLoaded] = useState<LoadedModel[]>([]);
  const [active, setActive] = useState("");
  const [theme, toggleTheme] = useTheme();

  // Measured rather than assumed, so a narrow window does not briefly render
  // the docked layout before an effect corrects it.
  const [wide, setWide] = useState(() => window.matchMedia(DOCKED).matches);
  const [showCopilot, setShowCopilot] = useState(() => window.matchMedia(DOCKED).matches);

  useEffect(() => {
    const query = window.matchMedia(DOCKED);
    const onChange = (event: MediaQueryListEvent) => setWide(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  // Escape dismisses the copilot only while it floats over the work. Docked, it
  // is part of the layout rather than something covering it, and closing it out
  // from under someone who pressed Escape to clear a text field would be a
  // surprise.
  useEffect(() => {
    if (wide || !showCopilot) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowCopilot(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [wide, showCopilot]);

  // Fetched once here and handed down. Letting the overview view fetch it too
  // meant the same request went out twice on every load.
  useEffect(() => {
    void (async () => {
      const result = await api.overview();
      if (result.ok) setOverview(result.data);
    })();
  }, [active]);

  useEffect(() => {
    void (async () => {
      const result = await api.models();
      if (result.ok) {
        setLoaded(result.data.models);
        setActive(result.data.default);
      }
    })();
  }, []);

  function switchTo(name: string) {
    if (name === active) return;
    api.use(name);
    // Cleared rather than left showing the previous model's figures while the
    // next ones load. Stale-but-plausible numbers under a new model name is
    // the one thing this interface must never do.
    setOverview(null);
    setActive(name);
  }

  // Every view is listed, including the two that need a flag this server may
  // not have been given. Removing them was worse than showing them unconfigured:
  // someone who has never passed those flags never discovers the capability, and
  // the product appears to do four things instead of six. The view itself
  // explains what to pass.
  function configured(entry: (typeof VIEWS)[number]): boolean {
    return !entry.needs || !overview || overview.capabilities[entry.needs];
  }

  return (
    <div className="flex h-full flex-col bg-surface">
      {/* Said plainly and permanently. A shared build that looked live would
          invite someone to trust a number that is only as fresh as the capture. */}
      {SNAPSHOT_MODE && (
        <div className="border-b border-review/40 bg-review-soft px-3 py-1.5 text-center text-[12px] text-review">
          Static snapshot of a real extraction — every figure below came from the
          model, but nothing here is live and the copilot needs a running server.
        </div>
      )}

      <header className="flex items-center gap-3 border-b border-hairline bg-ground px-3 py-2">
        <span className="font-serif text-sm font-semibold">Concordance</span>
        <span className="h-4 w-px bg-hairline" />
        {loaded.length > 1 ? (
          <label className="flex items-center gap-1.5">
            <span className="sr-only">Model</span>
            <select
              value={active}
              onChange={(event) => switchTo(event.target.value)}
              className="max-w-[14rem] truncate rounded border border-hairline bg-ground px-1.5 py-0.5 font-mono text-xs text-ink"
            >
              {loaded.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.name} · {entry.measures}m · {entry.tables}t
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="truncate font-mono text-xs text-muted">
            {overview?.model ?? "connecting…"}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {overview && (
            <span className="hidden font-mono text-[11px] text-faint sm:inline">
              {overview.measures} measures · {overview.relationships} joins
            </span>
          )}
          {/* Always present. The copilot was previously hidden outright below
              1024px, which removed a core feature on a narrow window with
              nothing on screen to suggest it existed. */}
          <button
            onClick={() => setShowCopilot((open) => !open)}
            aria-expanded={showCopilot}
            aria-controls="copilot"
            className={cx(
              "rounded border px-2 py-1 font-mono text-[11px]",
              showCopilot
                ? "border-accent/40 bg-accent-soft text-accent"
                : "border-hairline text-muted hover:text-ink",
            )}
          >
            copilot
          </button>
          <button
            onClick={toggleTheme}
            className="rounded border border-hairline px-2 py-1 font-mono text-[11px] text-muted hover:text-ink"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? "light" : "dark"}
          </button>
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1">
        <nav className="flex w-36 flex-none flex-col gap-0.5 border-r border-hairline bg-ground p-2">
          {VIEWS.map((entry) => (
            <button
              key={entry.id}
              onClick={() => setView(entry.id)}
              aria-current={view === entry.id ? "page" : undefined}
              title={
                configured(entry) ? undefined : `${entry.label} needs a flag this server was not given`
              }
              className={cx(
                "flex items-center justify-between gap-1 rounded px-2.5 py-1.5 text-left text-sm",
                view === entry.id
                  ? "bg-accent-soft font-medium text-accent"
                  : "text-muted hover:bg-raised hover:text-ink",
              )}
            >
              {entry.label}
              {/* Dimmed rather than hidden or disabled: still reachable, and
                  reading the view is how someone finds out what to pass. */}
              {!configured(entry) && (
                <span className="font-mono text-[10px] text-faint">off</span>
              )}
            </button>
          ))}
        </nav>

        {/* Keyed on the model: every view loads on mount, so without this a
            switch would leave the previous model's tables and requirements on
            screen under the new model's name. */}
        <main key={active} className="min-h-0 flex-1 overflow-auto">
          {view === "overview" && <Overview overview={overview} />}
          {view === "model" && <Model />}
          {view === "requirements" && <Requirements />}
          {view === "drift" && <Drift />}
          {view === "reconcile" && <Reconcile />}
          {view === "review" && <Review />}
        </main>

        {/* Stays mounted whether or not it is on screen: closing the panel must
            not throw away the conversation. Docked beside the work when there is
            room, over it when there is not. */}
        <aside
          id="copilot"
          className={cx(
            "flex-none flex-col border-l border-hairline bg-ground",
            showCopilot ? "flex" : "hidden",
            wide ? "w-80" : "absolute inset-y-0 right-0 z-10 w-80 max-w-[85%] shadow-xl",
          )}
        >
          <Copilot
            key={active}
            model={overview?.model ?? ""}
            onClose={wide ? undefined : () => setShowCopilot(false)}
          />
        </aside>
      </div>
    </div>
  );
}
