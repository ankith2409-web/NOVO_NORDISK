/**
 * What a feature shows when the server was started without what it needs.
 *
 * Two of the six views depend on a source the model itself cannot supply: drift
 * needs a second version to compare against, reconciliation needs a warehouse
 * to read. Previously both were simply removed from the rail when absent, which
 * is the worst of the options -- someone who has never passed those flags never
 * learns the capability exists, and the product looks like it does four things
 * instead of six.
 *
 * Showing the view with a red error box is not much better: a missing optional
 * flag is not a failure, and a red panel says the software broke.
 *
 * So the view stays, and states what it would answer, what it needs to answer
 * it, and the exact flag to add. The flag is shown rather than a whole
 * fabricated command line, because the path someone used to start their server
 * is not knowable from the browser and inventing a plausible-looking one is how
 * a person ends up pasting a command that cannot work.
 */
import { useState } from "react";
import { Button } from "@/components/primitives";

export function NotConfigured({
  title,
  answers,
  needs,
  flag,
  example,
  yields,
  alsoOn = [],
  uploaded = false,
}: {
  /** The feature's own name, matching the rail. */
  title: string;
  /** The question it exists to answer, in the user's terms. */
  answers: string;
  /** The source it cannot work without. */
  needs: string;
  /** The exact flag to add to whatever `concordance serve` command was used. */
  flag: string;
  /** A real value that flag takes, so the shape is not left to guesswork. */
  example: string;
  /** What appears here once it is configured. Concrete, not "results". */
  yields: string;
  /**
   * Other loaded models that *do* have this capability.
   *
   * These flags attach to a model, not to the server, so on a multi-model
   * server the old wording -- "not configured on this server", restart it with
   * `--compare-to` -- could be simply false: the server had that flag, pointed
   * at a different model. Someone acting on that advice restarts a working
   * server to add a flag it already has. When there are names to give, the
   * page says which model to switch to instead.
   */
  alsoOn?: string[];
  /**
   * True when the open model is one this browser uploaded.
   *
   * A third reason for the same absence, and the only one no flag can fix.
   * Drift needs a second *version* of this model and reconciliation needs a
   * warehouse built for its tables; an uploaded file is one model with neither,
   * and the configured model's warehouse is emphatically not a substitute --
   * it would compare somebody's measures against a stranger's schema and
   * report a pile of confident nonsense. Telling that reader to restart with a
   * flag is advice they cannot act on: the model only exists inside their
   * browser session, so there is no path to pass.
   */
  uploaded?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const elsewhere = alsoOn.length > 0 && !uploaded;

  async function copy() {
    try {
      await navigator.clipboard.writeText(`${flag} ${example}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused, and the flag is on screen to be typed
      // either way. Nothing here is worth an error message.
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <header>
        <h1 className="font-serif text-2xl leading-tight font-semibold">{title}</h1>
        <p className="mt-1 font-mono text-xs text-faint">
          {uploaded
            ? "not available for an uploaded model"
            : elsewhere
              ? "not configured for this model"
              : "not configured on this server"}
        </p>
      </header>

      <div className="max-w-2xl rounded border border-hairline bg-ground">
        <p className="border-b border-hairline px-3.5 py-2.5 text-sm text-ink">
          {answers}
        </p>

        <div className="flex flex-col gap-3 px-3.5 py-3">
          {uploaded ? (
            <p className="text-sm text-muted">
              It needs {needs}. You uploaded this model to your own browser session,
              which is one file and no second source — and the ones this server holds
              belong to its own models, so reading them here would compare your
              measures against a schema they were never written for. Everything else
              in this interface works on your model exactly as it does on any other.
            </p>
          ) : elsewhere ? (
            <p className="text-sm text-muted">
              It needs {needs}, which was not given for this model. It is
              configured for{" "}
              {alsoOn.map((name, index) => (
                <span key={name}>
                  {index > 0 && (index === alsoOn.length - 1 ? " and " : ", ")}
                  <code className="font-mono text-[13px] text-ink">{name}</code>
                </span>
              ))}
              , so switching model in the header shows it. To have it here too, add
              the flag for this model as well:
            </p>
          ) : (
            <p className="text-sm text-muted">
              It needs {needs}, which this server was not given. Restart it with this
              flag added to the command you used:
            </p>
          )}

          {/* Hidden for an upload rather than shown greyed out: a flag that
              cannot be applied to a model held in a browser session is not a
              next step, and offering a copy button for it invites someone to
              paste a command that cannot work. */}
          {!uploaded && (
            <div className="flex items-center gap-2 rounded border border-hairline bg-raised px-2.5 py-2">
              <code className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
                {flag} {example}
              </code>
              <Button onClick={copy} tone={copied ? "ok" : "quiet"} className="flex-none">
                {copied ? "copied" : "copy"}
              </Button>
            </div>
          )}

          <p className="text-xs text-muted">
            {uploaded
              ? `Load your model with \`concordance serve\` and pass ${flag} to get this: ${yields}`
              : yields}
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * The same absence, for a different reason.
 *
 * A shared snapshot is one captured extraction, and drift needs two versions
 * compared live, so it can never be part of one. Telling that reader to restart
 * a server with a flag would be advice they cannot act on -- they have a link,
 * not a checkout -- so the reason is stated as it actually is.
 */
export function SnapshotGap({ title, why }: { title: string; why: string }) {
  return (
    <div className="flex flex-col gap-4 p-4">
      <header>
        <h1 className="font-serif text-2xl leading-tight font-semibold">{title}</h1>
        <p className="mt-1 font-mono text-xs text-faint">not part of this snapshot</p>
      </header>
      <div className="max-w-2xl rounded border border-hairline bg-ground px-3.5 py-3">
        <p className="text-sm text-ink">{why}</p>
        <p className="mt-2 text-xs text-muted">
          Everything else on this page came from a real extraction and is exact. This
          one view needs a running server, which a static page has no way to stand in
          for.
        </p>
      </div>
    </div>
  );
}
