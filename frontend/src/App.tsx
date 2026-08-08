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
import { api, SNAPSHOT_MODE, type Overview as OverviewData } from "@/lib/api";
import { Copilot } from "@/components/Copilot";
import { Overview } from "@/views/Overview";
import { Model } from "@/views/Model";
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
  }, []);

  const available = VIEWS.filter(
    (entry) => !entry.needs || overview?.capabilities[entry.needs],
  );

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
        <span className="truncate font-mono text-xs text-muted">
          {overview?.model ?? "connecting…"}
        </span>
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
          {available.map((entry) => (
            <button
              key={entry.id}
              onClick={() => setView(entry.id)}
              aria-current={view === entry.id ? "page" : undefined}
              className={cx(
                "rounded px-2.5 py-1.5 text-left text-sm",
                view === entry.id
                  ? "bg-accent-soft font-medium text-accent"
                  : "text-muted hover:bg-raised hover:text-ink",
              )}
            >
              {entry.label}
            </button>
          ))}
        </nav>

        <main className="min-h-0 flex-1 overflow-auto">
          {view === "overview" && <Overview overview={overview} />}
          {view === "model" && <Model />}
          {view !== "overview" && view !== "model" && (
            <p className="p-4 font-mono text-xs text-faint">{view} — not built yet.</p>
          )}
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
            model={overview?.model ?? ""}
            onClose={wide ? undefined : () => setShowCopilot(false)}
          />
        </aside>
      </div>
    </div>
  );
}
