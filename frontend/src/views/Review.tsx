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
import { useEffect, useState } from "react";
import { api, type Requirement, type ReviewPayload, type Standing } from "@/lib/api";
import { Chip, Empty, Failure, Loading, Stat } from "@/components/primitives";
import { RichText } from "@/components/RichText";
import { cx } from "@/lib/cx";

export function Review() {
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");

  async function load() {
    const result = await api.review();
    if (result.ok) setData(result.data);
    else setError(result.message);
  }

  useEffect(() => {
    void load();
  }, []);

  async function record(id: string, verdict: string) {
    setBusy(id);
    setProblem("");
    const note =
      verdict === "rejected" || verdict === "corrected"
        ? // Asked for, not optional, on the two verdicts that assert the
          // statement is wrong. "Rejected, no reason given" leaves the next
          // person exactly where they started.
          (window.prompt(`What should it say instead? (${verdict})`) ?? "").trim()
        : "";
    if (verdict !== "accepted" && !note) {
      setBusy("");
      return;
    }
    const result = await api.decide(id, verdict, note);
    if (!result.ok) setProblem(result.message);
    else await load();
    setBusy("");
  }

  if (error) return <Failure message={error} />;
  if (!data) return <Loading what="the review queue" />;

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
          <button
            onClick={() => copyAll(data.pending, data.model)}
            className="ml-auto flex-none rounded border border-hairline px-2.5 py-1 font-mono text-[11px] text-muted hover:text-ink"
          >
            {copied ? "copied" : "copy the queue"}
          </button>
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

      {problem && <Failure message={problem} />}

      {data.count > 0 && !data.can_decide && (
        <p className="max-w-prose rounded border border-hairline bg-ground px-3.5 py-2.5 text-xs text-muted">
          This queue is read-only: the server was started without somewhere to write
          decisions. Restart it with{" "}
          <code className="font-mono text-ink">--decisions concordance-decisions.jsonl</code>{" "}
          to answer these here, with each decision bound to the definition it was made
          against.
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
              className="rounded border border-review/40 bg-ground px-3.5 py-3"
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

              <StandingLine standing={requirement.standing} />

              <p className="mt-2 border-l-2 border-review/40 pl-2.5 text-xs text-muted">
                <RichText>{requirement.rationale}</RichText>
              </p>

              {data.can_decide && (
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                  {(["accepted", "rejected", "corrected"] as const).map((verdict) => (
                    <button
                      key={verdict}
                      disabled={busy === requirement.id}
                      onClick={() => void record(requirement.id, verdict)}
                      className={cx(
                        "rounded border px-2 py-0.5 font-mono text-[11px] disabled:opacity-50",
                        verdict === "accepted"
                          ? "border-ok/40 text-ok hover:bg-ok-soft"
                          : "border-hairline text-muted hover:text-ink",
                      )}
                    >
                      {verdict}
                    </button>
                  ))}
                  {requirement.standing.status === "stale" && (
                    <span className="font-mono text-[11px] text-bad">
                      re-decide: the definition moved
                    </span>
                  )}
                </div>
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
