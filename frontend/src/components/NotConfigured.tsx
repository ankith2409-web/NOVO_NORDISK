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

export function NotConfigured({
  title,
  answers,
  needs,
  flag,
  example,
  yields,
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
}) {
  const [copied, setCopied] = useState(false);

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
        <p className="mt-1 font-mono text-xs text-faint">not configured on this server</p>
      </header>

      <div className="max-w-2xl rounded border border-hairline bg-ground">
        <p className="border-b border-hairline px-3.5 py-2.5 text-sm text-ink">
          {answers}
        </p>

        <div className="flex flex-col gap-3 px-3.5 py-3">
          <p className="text-sm text-muted">
            It needs {needs}, which this server was not given. Restart it with this
            flag added to the command you used:
          </p>

          <div className="flex items-center gap-2 rounded border border-hairline bg-raised px-2.5 py-2">
            <code className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
              {flag} {example}
            </code>
            <button
              onClick={copy}
              className="flex-none rounded border border-hairline px-1.5 py-0.5 font-mono text-[11px] text-muted hover:text-ink"
            >
              {copied ? "copied" : "copy"}
            </button>
          </div>

          <p className="text-xs text-muted">{yields}</p>
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
