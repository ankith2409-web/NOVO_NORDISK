/**
 * Everything the system is not confident about, in one place.
 *
 * These are statements derived from structure alone. The model does not say what
 * an inactive relationship is *for*, so a sentence claiming to know is an
 * inference, and inferences belong in front of a person before a document goes
 * anywhere.
 *
 * There is deliberately no Accept or Reject control here. Nothing behind this
 * screen records a decision -- there is no write endpoint -- and a button that
 * looked like it filed an approval while doing nothing would be worse than no
 * button at all in a tool whose entire claim is that it does not overstate what
 * it knows. What it does instead is make the queue easy to take somewhere a
 * decision can actually be recorded.
 */
import { useEffect, useState } from "react";
import { api, type Requirement, type ReviewPayload } from "@/lib/api";
import { Chip, Empty, Failure, Loading } from "@/components/primitives";
import { RichText } from "@/components/RichText";

export function Review() {
  const [data, setData] = useState<ReviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    void (async () => {
      const result = await api.review();
      if (result.ok) setData(result.data);
      else setError(result.message);
    })();
  }, []);

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

              <p className="mt-2 border-l-2 border-review/40 pl-2.5 text-xs text-muted">
                <RichText>{requirement.rationale}</RichText>
              </p>

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

/** The clipboard copy is read as plain text, so the object-name marks come off. */
function plain(text: string): string {
  return text.replace(/\*\*(.+?)\*\*/g, "$1");
}
