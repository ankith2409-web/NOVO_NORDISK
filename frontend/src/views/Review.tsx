/**
 * Everything the system is not confident about, in one place.
 *
 * These are statements derived from structure alone. The model does not say what
 * an inactive relationship is *for*, so a sentence claiming to know is an
 * inference, and inferences belong in front of a person before a document goes
 * anywhere.
 *
 * This screen used to have no Accept or Reject, on the grounds that nothing
 * behind it recorded a decision and a button that looked like it filed an
 * approval while storing nothing would be worse than no button at all. That
 * reasoning was right and its premise is now false: there is a write endpoint,
 * backed by an append-only log.
 *
 * The controls still only appear when this particular server was started with
 * somewhere to write, which is the same argument applied to the same standard --
 * `can_decide` false means the queue is read-only and says so.
 *
 * What makes a decision worth recording is that it is bound to the fingerprints
 * of what it was made about. Change the DAX behind a measure and the sign-off
 * stops applying by itself, and this screen reports that as `stale` rather than
 * quietly carrying an old approval onto logic nobody has seen. An ordinary
 * approved column does the opposite by design.
 */
import { useState } from "react";
import { api, type Requirement, type ReviewPayload, type Standing } from "@/lib/api";
import { Button, Chip, Empty, Failure, Loading, Stat } from "@/components/primitives";
import { DecisionDialog, type Verdict } from "@/components/DecisionDialog";
import { DecisionBar } from "@/components/DecisionBar";
import { RichText } from "@/components/RichText";
import { CheckIcon } from "@/components/icons";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";

export function Review() {
  const { data, error, retrying, reload } = useLoad<ReviewPayload>(() => api.review(), []);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState("");
  // Kept apart from `error`. A queue that failed to load leaves nothing on
  // screen; a decision that failed to record leaves the queue perfectly
  // readable and only that one write undone, and collapsing the two would
  // blank the page over a recoverable write.
  const [problem, setProblem] = useState<{ status: number; message: string } | null>(null);
  // The two verdicts that assert the statement is wrong ask for a note first,
  // and that asking is a screen rather than `window.prompt`: the note is a
  // sentence, and it is written while looking at the sentence it replaces.
  const [asking, setAsking] = useState<{ requirement: Requirement; verdict: Verdict } | null>(
    null,
  );
  // Which row just recorded, so the write is acknowledged rather than only
  // implied by the list quietly rearranging itself. Accepting used to be
  // entirely silent: the one action a reviewer takes most often gave the least
  // evidence it had happened.
  const [justDecided, setJustDecided] = useState("");

  async function send(id: string, verdict: string, note: string) {
    setBusy(id);
    setProblem(null);
    const result = await api.decide(id, verdict, note);
    if (!result.ok) setProblem({ status: result.status, message: result.message });
    else {
      // Only closed on success. A failed write leaves the dialog open with the
      // note still in it, so a reviewer never loses what they typed to a
      // network error they can simply retry.
      setAsking(null);
      setJustDecided(id);
      setTimeout(() => setJustDecided((current) => (current === id ? "" : current)), 2400);
      reload();
    }
    setBusy("");
  }

  function record(requirement: Requirement, verdict: string) {
    if (verdict === "accepted") {
      void send(requirement.id, verdict, "");
      return;
    }
    setProblem(null);
    setAsking({ requirement, verdict: verdict as Verdict });
  }

  if (error)
    return (
      <Failure
        message={error.message}
        status={error.status}
        what="the review queue"
        onRetry={reload}
        retrying={retrying}
      />
    );
  if (!data) return <Loading what="the review queue" rows={3} />;

  function copyAll(pending: Requirement[], model: string) {
    const text = [
      `Concordance — statements awaiting confirmation (${model})`,
      "",
      ...pending.flatMap((requirement) => [
        `${requirement.id}  [${requirement.category}]`,
        `  ${plain(requirement.statement)}`,
        `  Why it needs a person: ${plain(requirement.rationale)}`,
        ...requirement.evidence.map(
          (evidence) => `  Bound to: ${evidence.node_id}  ${evidence.short_fingerprint}`,
        ),
        "",
      ]),
    ].join("\n");
    void navigator.clipboard?.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-wrap items-start gap-3">
        <div className="min-w-0">
          <h1 className="font-serif text-2xl leading-tight font-semibold">
            Awaiting confirmation
          </h1>
          <p className="mt-1 max-w-prose text-sm text-muted">
            Each of these was inferred from the model's structure rather than stated by
            it. They are in the generated documents, marked as low confidence — someone
            who knows the process needs to confirm or correct them.
          </p>
        </div>
        {data.count > 0 && (
          <Button
            onClick={() => copyAll(data.pending, data.model)}
            tone={copied ? "ok" : "quiet"}
            className="ml-auto flex-none"
          >
            {copied ? "copied" : "copy the queue"}
          </Button>
        )}
      </header>

      {data.count > 0 && (
        <div className="grid grid-cols-3 gap-2.5 sm:max-w-md">
          <Stat label="Open" value={data.open} tone={data.open > 0 ? "review" : "neutral"} />
          <Stat label="Decided" value={data.decided} tone={data.decided > 0 ? "ok" : "neutral"} />
          <Stat
            label="Stale"
            value={data.stale}
            tone={data.stale > 0 ? "bad" : "neutral"}
            hint="Decided once, but what the statement rests on has changed since"
          />
        </div>
      )}

      {problem && !asking && (
        <Failure
          message={problem.message}
          status={problem.status}
          what="that decision"
        />
      )}

      {/* Two reasons the queue can be read-only, and they need different
          sentences. Telling someone to restart with --decisions when the
          server already has it -- because the model in front of them is one
          they uploaded -- sends them to fix a flag that is already set. */}
      {data.count > 0 && !data.can_decide && (
        <p className="max-w-prose rounded border border-hairline bg-ground px-3.5 py-2.5 text-xs text-muted">
          {data.uploaded ? (
            <>
              This queue is read-only because this model was uploaded to this
              browser session. A decision is bound to the definition it was made
              against and outlives the sitting it was made in — an uploaded model
              does not, so there is nowhere honest to file one. Open it with{" "}
              <code className="font-mono text-ink">concordance serve</code> to review
              it for real.
            </>
          ) : (
            <>
              This queue is read-only: the server was started without somewhere to write
              decisions. Restart it with{" "}
              <code className="font-mono text-ink">--decisions concordance-decisions.jsonl</code>{" "}
              to answer these here, with each decision bound to the definition it was made
              against.
            </>
          )}
        </p>
      )}

      {data.count === 0 ? (
        <Empty>
          Nothing is waiting. Every requirement in this model either restates something
          the model declares outright or follows from a structural rule.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {data.pending.map((requirement) => (
            <li
              key={requirement.id}
              className={cx(
                "rounded border bg-ground px-3.5 py-3",
                "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
                // The row confirms its own write. Held briefly rather than
                // permanently: it answers "did that land?", which stops being
                // a question a couple of seconds later, and the standing line
                // above carries the durable record.
                justDecided === requirement.id
                  ? "border-ok/50 bg-ok-soft"
                  : "border-review/40",
              )}
            >
              <div className="flex items-start gap-2.5">
                <code className="mt-0.5 flex-none font-mono text-[11px] text-faint">
                  {requirement.id}
                </code>
                <p className="min-w-0 flex-1 text-sm">
                  <RichText>{requirement.statement}</RichText>
                </p>
                <Chip tone="review">{requirement.category}</Chip>
              </div>

              {justDecided === requirement.id ? (
                <p
                  role="status"
                  className="mt-2 flex items-center gap-1.5 font-mono text-[11px] text-ok animate-(--animate-rise)"
                >
                  <CheckIcon size={13} />
                  Recorded against this statement&rsquo;s fingerprint
                </p>
              ) : (
                <StandingLine standing={requirement.standing} />
              )}

              <p className="mt-2 border-l-2 border-review/40 pl-2.5 text-xs text-muted">
                <RichText>{requirement.rationale}</RichText>
              </p>

              {data.can_decide && (
                <DecisionBar
                  busy={busy === requirement.id}
                  stale={requirement.standing.status === "stale"}
                  decided={requirement.standing.status === "decided"}
                  onDecide={(verdict) => record(requirement, verdict)}
                />
              )}

              {requirement.evidence.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {requirement.evidence.map((evidence) => (
                    <li
                      key={evidence.node_id}
                      className="flex flex-wrap items-baseline gap-2 font-mono text-[11px]"
                    >
                      <span className="text-muted">{evidence.node_id}</span>
                      <span
                        title={`SHA-256 · ${evidence.fingerprint}`}
                        className="text-faint"
                      >
                        {evidence.short_fingerprint}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}

      {asking && (
        <DecisionDialog
          requirement={asking.requirement}
          verdict={asking.verdict}
          busy={busy === asking.requirement.id}
          problem={problem}
          onCancel={() => setAsking(null)}
          onConfirm={(note) => void send(asking.requirement.id, asking.verdict, note)}
        />
      )}
    </div>
  );
}

/** What was decided, and whether it still covers the current definition. */
function StandingLine({ standing }: { standing: Standing }) {
  if (standing.status === "open") return null;

  const stale = standing.status === "stale";
  return (
    <p
      className={cx(
        "mt-2 font-mono text-[11px]",
        stale ? "text-bad" : "text-ok",
      )}
    >
      {standing.verdict}
      {standing.author_claimed ? ` by ${standing.author_claimed}` : ""}
      {/* The name is worth very different amounts depending on this, so the
          two are never shown the same way. Verified means the server resolved
          the reviewer's own token; unverified means they typed the name. */}
      {standing.author_claimed &&
        (standing.author_verified ? (
          <span className="text-ok" title="Resolved from this reviewer's own access token">
            {" "}
            ✓ verified
          </span>
        ) : (
          <span className="text-faint" title="Self-declared: this server does not identify reviewers">
            {" "}
            (unverified)
          </span>
        ))}
      {standing.at ? ` · ${standing.at.slice(0, 10)}` : ""}
      {stale && " · no longer covers this definition, which has changed since"}
      {standing.note ? ` · "${standing.note}"` : ""}
      {standing.history.length > 1 && ` · ${standing.history.length} decisions on record`}
    </p>
  );
}

/** The clipboard copy is read as plain text, so the object-name marks come off. */
function plain(text: string): string {
  return text.replace(/\*\*(.+?)\*\*/g, "$1");
}
