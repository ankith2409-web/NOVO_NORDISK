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
  type WhoAmI,
} from "@/lib/api";
import { Button } from "@/components/primitives";
import { Copilot } from "@/components/Copilot";
import { Intro } from "@/components/Intro";
import { FAVICON_SVG, Wordmark } from "@/components/Logo";
import { Overview } from "@/views/Overview";
import { Model } from "@/views/Model";
import { Requirements } from "@/views/Requirements";
import { Drift } from "@/views/Drift";
import { Review } from "@/views/Review";
import { cx } from "@/lib/cx";
import { recall, recallOneOf, remember } from "@/lib/remember";

type ViewId = "overview" | "model" | "requirements" | "drift" | "review";

const VIEWS: { id: ViewId; label: string; needs?: "drift" }[] = [
  { id: "overview", label: "Overview" },
  { id: "model", label: "Model" },
  { id: "requirements", label: "Requirements" },
  { id: "drift", label: "Drift", needs: "drift" },
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
  // Restored eagerly rather than in an effect: a first paint on the Overview
  // followed by a jump to the remembered view is a flash, not a restore.
  const [view, setViewState] = useState<ViewId>(
    () => recallOneOf("view", VIEWS.map((entry) => entry.id)) ?? "overview",
  );

  function setView(next: ViewId) {
    setViewState(next);
    remember("view", next);
  }
  const [overview, setOverview] = useState<OverviewData | null>(null);
  // Only used to draw the switcher. Which model is *active* lives in the API
  // client, so a request cannot be issued from a view that never heard about
  // the switch.
  const [loaded, setLoaded] = useState<LoadedModel[]>([]);
  const [active, setActive] = useState("");
  // Which model to ask about is only known after /api/models answers. Rendering
  // the views before then would fire every view's fetch against the default and
  // again against the restored model, and briefly show one model's figures
  // under the other's name.
  const [resolved, setResolved] = useState(false);
  // Shown once. Deliberately not waiting on the model to load: someone whose
  // server is down should still be told what they are looking at.
  const [intro, setIntro] = useState(() => recall("intro-seen") !== "yes");

  function closeIntro() {
    setIntro(false);
    remember("intro-seen", "yes");
  }
  const [theme, toggleTheme] = useTheme();

  // Set here rather than in index.html so the mark has exactly one definition.
  // A second copy pasted into the template is a second thing to update, and the
  // one that gets forgotten is always the one in the tab.
  useEffect(() => {
    const link =
      document.querySelector<HTMLLinkElement>("link[rel='icon']") ??
      document.head.appendChild(Object.assign(document.createElement("link"), {
        rel: "icon",
      }));
    link.type = "image/svg+xml";
    link.href = FAVICON_SVG;
  }, []);

  // Who the server will record decisions as. Asked once: it depends on the
  // credential this browser is holding, not on which model is open.
  const [who, setWho] = useState<WhoAmI | null>(null);
  useEffect(() => {
    if (SNAPSHOT_MODE) return;
    void (async () => {
      const result = await api.whoami();
      if (result.ok) setWho(result.data);
    })();
  }, []);

  // Measured rather than assumed, so a narrow window does not briefly render
  // the docked layout before an effect corrects it.
  const [wide, setWide] = useState(() => window.matchMedia(DOCKED).matches);
  const [showCopilot, setShowCopilot] = useState(() => {
    const stored = recall("copilot");
    // Only a deliberate choice is remembered. With none, the width decides --
    // restoring "open" onto a phone-sized window would cover the work.
    if (stored === "open" || stored === "closed") return stored === "open";
    return window.matchMedia(DOCKED).matches;
  });

  useEffect(() => {
    remember("copilot", showCopilot ? "open" : "closed");
  }, [showCopilot]);

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
    if (!resolved) return;
    void (async () => {
      const result = await api.overview();
      if (result.ok) setOverview(result.data);
    })();
  }, [active, resolved]);

  useEffect(() => {
    void (async () => {
      const result = await api.models();
      // Resolved either way: if the server cannot be reached, the views still
      // have to mount so the one message worth showing -- how to start it --
      // reaches the screen.
      setResolved(true);
      if (!result.ok) return;
      setLoaded(result.data.models);
      // Validated against what this server loaded. The same browser is used
      // against different servers, and a remembered name this one never heard
      // of would point every request at nothing.
      const names = result.data.models.map((entry) => entry.name);
      const restored = recallOneOf("model", names) ?? result.data.default;
      api.use(restored);
      setActive(restored);
    })();
  }, []);

  function switchTo(name: string) {
    if (name === active) return;
    api.use(name);
    remember("model", name);
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
  //
  // Read from `loaded` rather than from `overview`. Both carry the same
  // capability flags, but `overview` is cleared to null on every model switch
  // and refetched, and the old test treated "not known yet" as "configured" --
  // so switching to a model without a comparison baseline lit Drift up as
  // available for as long as the fetch took, then silently dropped it to
  // `off`. The registry answers once, covers every model at the same time,
  // and is never nulled, so the rail can be right from the first paint.
  const activeCapabilities = loaded.find((entry) => entry.name === active)?.capabilities;

  function configured(entry: (typeof VIEWS)[number]): boolean {
    if (!entry.needs) return true;
    // Still genuinely unknown -- before /api/models answers, nothing is known
    // about any model. Claiming "off" here would be as wrong as claiming "on".
    if (!activeCapabilities) return true;
    return activeCapabilities[entry.needs];
  }

  /**
   * The other loaded models that *do* have a capability the active one lacks.
   *
   * Drift attaches to the model it was configured against, not to the
   * server, so on a multi-model server "this server was not given that flag"
   * can be flatly untrue -- it was given, just bound elsewhere. Handing the
   * view the names lets it say which, instead of sending someone to restart
   * a server that already has what they want.
   */
  function othersWith(capability: "drift"): string[] {
    return loaded
      .filter((entry) => entry.name !== active && entry.capabilities[capability])
      .map((entry) => entry.name);
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
        <Wordmark />
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
          <Button
            onClick={() => setShowCopilot((open) => !open)}
            aria-expanded={showCopilot}
            aria-controls="copilot"
            tone={showCopilot ? "selected" : "quiet"}
          >
            copilot
          </Button>
          {/* Shown before a decision is recorded rather than after. Finding
              out you signed something off as the wrong identity is a thing to
              learn beforehand. */}
          {who?.identified && (
            <span
              className="hidden max-w-[12rem] truncate font-mono text-[11px] text-muted sm:inline"
              title={`Review decisions are recorded as ${who.person}`}
            >
              {who.person}
            </span>
          )}
          {who?.identified && who.auth0 && (
            <Button
              onClick={() => {
                window.location.href = "/signed-out";
              }}
              aria-label={`Sign out ${who.person}`}
            >
              sign out
            </Button>
          )}
          <Button onClick={() => setIntro(true)}>guide</Button>
          <Button
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? "light" : "dark"}
          </Button>
        </div>
      </header>

      {/* Covers the header too, so `aria-modal` describes what is actually the
          case rather than claiming an inertness the layout does not provide. */}
      {intro && <Intro overview={overview} onClose={closeIntro} />}

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
                "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
                "pointer-coarse:min-h-11",
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
          {!resolved && <p className="p-4 font-mono text-xs text-faint">Connecting…</p>}
          {resolved && view === "overview" && <Overview overview={overview} />}
          {resolved && view === "model" && <Model />}
          {resolved && view === "requirements" && <Requirements />}
          {resolved && view === "drift" && <Drift alsoOn={othersWith("drift")} />}
          {resolved && view === "review" && <Review />}
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
