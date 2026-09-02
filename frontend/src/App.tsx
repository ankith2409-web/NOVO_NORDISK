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
import { Fragment, useEffect, useRef, useState } from "react";
import {
  api,
  SNAPSHOT_MODE,
  type LoadedModel,
  type Overview as OverviewData,
  type Uploaded,
  type WhoAmI,
} from "@/lib/api";
import { Button } from "@/components/primitives";
import { ModelPicker } from "@/components/ModelPicker";
import { UploadDialog } from "@/components/UploadDialog";
import { UploadIcon } from "@/components/icons";
import { Copilot } from "@/components/Copilot";
import { Intro } from "@/components/Intro";
import { FAVICON_SVG, Wordmark } from "@/components/Logo";
import { Overview } from "@/views/Overview";
import { Model } from "@/views/Model";
import { Dashboard } from "@/views/Dashboard";
import { Dataset } from "@/views/Dataset";
import { FEATURE } from "@/lib/naming";
import { Requirements } from "@/views/Requirements";
import { Drift } from "@/views/Drift";
import { Reconcile } from "@/views/Reconcile";
import { Review } from "@/views/Review";
import { cx } from "@/lib/cx";
import { recall, recallOneOf, remember } from "@/lib/remember";

type ViewId =
  | "overview"
  | "model"
  | "dashboard"
  | "dataset"
  | "requirements"
  | "drift"
  | "reconcile"
  | "review";

/**
 * The rail, in two groups.
 *
 * A reviewer said this in as many words and it went unheard for a sprint:
 * "Make it very simple, that's what I'm trying to tell. Because we don't need
 * like warehouse or to confirm drift. We just need everything to be converted
 * into tables, of course, columns, measures, of course what joins it is having,
 * and how is it related to each other... Whatever you have taken is very
 * complex. I don't need so much."
 *
 * She was looking at seven tabs when she said it. The answer was to add an
 * eighth, which is the opposite of listening. Nothing is deleted here -- the
 * other reviewer asked for drift by name, and a feature you cannot show is
 * worse than one nobody clicks -- but the four she actually asked for now sit
 * on their own, and the three she said she did not need sit under a line
 * saying so.
 *
 * The order of the first four is the order somebody meets the model in: what
 * is this, what is on its dashboard, what does it calculate, what document
 * comes out.
 */
const VIEWS: {
  id: ViewId;
  label: string;
  needs?: "drift" | "reconcile";
  /** "more" items sit below the divider. */
  group?: "more";
}[] = [
  { id: "overview", label: "Overview" },
  // The only page that starts from what a person is actually looking at -- a
  // tile with a title on it -- and works back to the definition.
  { id: "dashboard", label: "Dashboard" },
  { id: "dataset", label: FEATURE.dataset.tab },
  { id: "requirements", label: "Documents" },

  // Below the line. Everything still works; none of it is the first thing to
  // look at.
  { id: "model", label: "Browse objects", group: "more" },
  { id: "drift", label: FEATURE.drift.tab, needs: "drift", group: "more" },
  { id: "reconcile", label: FEATURE.reconcile.tab, needs: "reconcile", group: "more" },
  { id: "review", label: FEATURE.review.tab, group: "more" },
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
  // The model this server was started on, kept so removing an uploaded model
  // has somewhere to land. A ref rather than state: nothing renders from it,
  // and it is written once.
  const defaultModel = useRef("");
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
      defaultModel.current = result.data.default;
      const restored = recallOneOf("model", names) ?? result.data.default;
      api.use(restored);
      setActive(restored);
    })();
  }, []);

  const [uploading, setUploading] = useState(false);

  /**
   * Adopt a model the server has just read, and move to it.
   *
   * Switching immediately is the whole point: somebody who uploads a file wants
   * to look at it, and leaving them on the model they started with -- with a
   * new name quietly added to a dropdown -- would read as the upload having
   * failed.
   */
  function adopt(result: Uploaded) {
    setLoaded((held) => [
      // Filtered first so re-uploading under a name that was evicted and
      // reissued cannot leave two rows claiming the same model.
      ...held.filter((entry) => entry.name !== result.name && entry.name !== result.replaced),
      {
        name: result.name,
        source_format: result.source_format,
        measures: result.measures,
        tables: result.tables,
        // Neither is configurable for an upload: drift needs a second version
        // of this model and reconciliation needs a warehouse built for it.
        capabilities: { drift: false, reconcile: false },
        uploaded: true,
      },
    ]);
    setUploading(false);
    switchTo(result.name);
  }

  async function forget(name: string) {
    const result = await api.forget(name);
    if (!result.ok) return;
    setLoaded((held) => held.filter((entry) => entry.name !== name));
    // Only when the model being dropped is the one on screen. Leaving the
    // views mounted against a model the server has just forgotten would show
    // its figures under a name that no longer resolves.
    if (name === active) switchTo(defaultModel.current);
  }

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
  // so switching to a model without a warehouse lit Reconcile up as available
  // for as long as the fetch took, then silently dropped it to `off`. The
  // registry answers once, covers every model at the same time, and is never
  // nulled, so the rail can be right from the first paint.
  const activeEntry = loaded.find((entry) => entry.name === active);
  const activeCapabilities = activeEntry?.capabilities;
  const activeIsUploaded = activeEntry?.uploaded ?? false;

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
   * Drift and reconciliation attach to the model they were configured against,
   * not to the server, so on a multi-model server "this server was not given
   * that flag" can be flatly untrue -- it was given, just bound elsewhere.
   * Handing the view the names lets it say which, instead of sending someone
   * to restart a server that already has what they want.
   */
  function othersWith(capability: "drift" | "reconcile"): string[] {
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

      {/* `flex-wrap`, because this row cannot shrink below its contents: the
          action group on the right is five controls with fixed labels, and at
          720px it ran 39px past the viewport, scrolling every tab sideways.
          Hiding controls was the alternative and the worse one -- they are all
          reachable features, and a narrow window is not a reason to remove
          them. Wrapping to a second line costs one row of height and nothing
          else. */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-hairline bg-ground px-3 py-2">
        <Wordmark />
        <span className="h-4 w-px bg-hairline" />
        {loaded.length > 1 ? (
          <ModelPicker
            loaded={loaded}
            active={active}
            onSwitch={switchTo}
            // Absent in the snapshot build, which has no server to forget
            // anything and one model to forget it from.
            onForget={SNAPSHOT_MODE ? undefined : (name) => void forget(name)}
          />
        ) : (
          <span className="truncate font-mono text-xs text-muted">
            {overview?.model ?? "connecting…"}
          </span>
        )}
        {/* Beside the switcher rather than in a menu: this is the answer to
            "does it work on my model", which is the first question anyone
            has, and burying it would mean it is never asked.

            Labelled "Open your model" rather than "your model" for two
            reasons, the second of which bites: a control should say what it
            does, and a bare noun phrase does not; and "your model" contains
            the nav tab "Model" as a substring, so anything matching a control
            by name -- voice control, a screen reader's element list, a test --
            can land on this button while aiming at that tab. Found exactly
            that way, by a sweep that clicked "Model" and got this dialog. */}
        {!SNAPSHOT_MODE && (
          <Button onClick={() => setUploading(true)} title="Read a .pbix or .pbip of your own">
            <UploadIcon size={11} className="flex-none" />
            Open your model
          </Button>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-x-2 gap-y-1.5">
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

      {uploading && (
        <UploadDialog
          loaded={loaded}
          onClose={() => setUploading(false)}
          onLoaded={adopt}
          onForgotten={(name) => void forget(name)}
        />
      )}

      <div className="relative flex min-h-0 flex-1">
        {/* w-44, not w-36. An uploaded model has neither optional capability,
            so it is the first state where an "off" badge sits beside a long
            label -- and the badge takes exactly enough room that the widest of
            them truncated at w-36. Measured rather than eyeballed, and set
            wide enough to leave headroom instead of landing on the next
            one-pixel margin. */}
        <nav className="flex w-44 flex-none flex-col gap-0.5 border-r border-hairline bg-ground p-2">
          {VIEWS.map((entry, index) => (
            <Fragment key={entry.id}>
              {/* One line, once, before the first secondary item. Labelled, so
                  it reads as a deliberate second tier rather than as a gap. */}
              {entry.group === "more" && VIEWS[index - 1]?.group !== "more" && (
                <p className="mt-3 mb-1 px-2.5 font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                  also here
                </p>
              )}
            <button
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
              {/* Wrapped and held on one line. As a bare text node this was an
                  anonymous flex item free to shrink below its own width, so a
                  two-word label broke across two lines as soon as the "off"
                  badge appeared beside it -- and the badge was then stranded
                  on the first line beside half a name. */}
              <span className="truncate whitespace-nowrap">{entry.label}</span>
              {/* Dimmed rather than hidden or disabled: still reachable, and
                  reading the view is how someone finds out what to pass. */}
              {!configured(entry) && (
                <span className="flex-none font-mono text-[10px] text-faint">off</span>
              )}
            </button>
            </Fragment>
          ))}
        </nav>

        {/* Keyed on the model: every view loads on mount, so without this a
            switch would leave the previous model's tables and requirements on
            screen under the new model's name. */}
        <main key={active} className="min-h-0 flex-1 overflow-auto">
          {!resolved && <p className="p-4 font-mono text-xs text-faint">Connecting…</p>}
          {resolved && view === "overview" && <Overview overview={overview} />}
          {resolved && view === "model" && <Model />}
          {resolved && view === "dashboard" && <Dashboard />}
          {resolved && view === "dataset" && <Dataset />}
          {resolved && view === "requirements" && <Requirements />}
          {resolved && view === "drift" && (
            <Drift alsoOn={othersWith("drift")} uploaded={activeIsUploaded} />
          )}
          {resolved && view === "reconcile" && (
            <Reconcile alsoOn={othersWith("reconcile")} uploaded={activeIsUploaded} />
          )}
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
