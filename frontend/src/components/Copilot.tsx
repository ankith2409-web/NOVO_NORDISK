/**
 * The grounded chat, rehoused.
 *
 * The one thing this adds over a plain chat box: every answer shows the tools it
 * was built from, and says so when it was built from none. An ungrounded answer
 * is not necessarily wrong -- "hello" needs no tool -- but in a tool whose whole
 * claim is that it does not invent things, the reader is entitled to know which
 * kind of answer they are looking at without having to ask.
 */
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { Chip } from "@/components/primitives";
import { cx } from "@/lib/cx";

interface Turn {
  question: string;
  answer: string;
  grounded: boolean;
  tools: string[];
  rejected: string[];
  failed?: boolean;
}

export function Copilot({ model }: { model: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const log = useRef<HTMLDivElement>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;

    setQuestion("");
    setBusy(true);
    const result = await api.ask(asked);
    setBusy(false);

    setTurns((previous) => [
      ...previous,
      result.ok
        ? {
            question: asked,
            answer: result.data.answer,
            grounded: result.data.grounded,
            tools: result.data.tool_calls.map((call) => call.name),
            rejected: result.data.rejected_calls,
          }
        : {
            question: asked,
            answer: result.message,
            grounded: false,
            tools: [],
            rejected: [],
            failed: true,
          },
    ]);
    requestAnimationFrame(() => log.current?.scrollTo({ top: log.current.scrollHeight }));
  }

  return (
    <div className="flex min-h-0 flex-col">
      <header className="flex items-center gap-2 border-b border-hairline px-3.5 py-2">
        <h2 className="font-mono text-[11px] tracking-[0.1em] text-faint uppercase">
          Copilot
        </h2>
        <span className="truncate font-mono text-[11px] text-faint">· {model}</span>
      </header>

      <div ref={log} className="min-h-0 flex-1 overflow-auto px-3.5 py-3">
        {turns.length === 0 && (
          <div className="text-sm text-muted">
            <p className="mb-2">Ask about this model. Answers come from the graph, not from memory.</p>
            <ul className="space-y-1 font-mono text-xs text-faint">
              <li>“What does OOS Rate measure?”</li>
              <li>“What breaks if I change Tests Performed?”</li>
              <li>“Which joins are inactive?”</li>
            </ul>
          </div>
        )}

        <div className="space-y-4">
          {turns.map((turn, index) => (
            <div key={index} className="space-y-1.5">
              <p className="text-sm font-medium">{turn.question}</p>
              <p
                className={cx(
                  "text-sm whitespace-pre-wrap",
                  turn.failed ? "text-bad" : "text-muted",
                )}
              >
                {turn.answer}
              </p>
              <div className="flex flex-wrap gap-1">
                {turn.tools.map((tool, position) => (
                  <Chip key={`${tool}-${position}`} tone="accent">
                    {tool}
                  </Chip>
                ))}
                {!turn.failed && !turn.grounded && turn.tools.length === 0 && (
                  <Chip tone="review" title="Answered without reading the model">
                    no tool used
                  </Chip>
                )}
                {turn.rejected.map((name, position) => (
                  <Chip
                    key={`${name}-${position}`}
                    tone="bad"
                    title="The model asked for a tool that does not exist; the request was refused."
                  >
                    refused {name}
                  </Chip>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <form onSubmit={submit} className="border-t border-hairline p-2.5">
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={busy ? "Thinking…" : "Ask about this model"}
          disabled={busy}
          aria-label="Ask about this model"
          className={cx(
            "w-full rounded border border-hairline bg-surface px-2.5 py-2",
            "text-sm placeholder:text-faint disabled:opacity-60",
          )}
        />
      </form>
    </div>
  );
}
