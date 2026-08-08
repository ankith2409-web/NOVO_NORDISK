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

export default function App() {
  const [view, setView] = useState<ViewId>("overview");
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [theme, toggleTheme] = useTheme();

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
          <button
            onClick={toggleTheme}
            className="rounded border border-hairline px-2 py-1 font-mono text-[11px] text-muted hover:text-ink"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? "light" : "dark"}
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
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
          {view === "overview" && <Overview />}
          {view === "model" && <Model />}
          {view !== "overview" && view !== "model" && (
            <p className="p-4 font-mono text-xs text-faint">{view} — not built yet.</p>
          )}
        </main>

        <aside className="hidden w-80 flex-none border-l border-hairline bg-ground lg:flex lg:flex-col">
          <Copilot model={overview?.model ?? ""} />
        </aside>
      </div>
    </div>
  );
}
