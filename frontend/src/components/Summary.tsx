/**
 * The AI-generated narrative on top of a drift or reconcile report.
 *
 * Deliberately opt-in and separate from the evidence it sits above: generating
 * it costs a real request to a language model, so it is fetched only when
 * asked for, never on page load. And it is never the only place a finding
 * appears -- everything it describes is still shown, in full, underneath.
 * The label and disclaimer are not decoration; they are the difference
 * between this being read as a finding and being read as what it is, a
 * summary that should be checked against the report it was written from.
 */
import { useState } from "react";
import type { Summary as SummaryPayload } from "@/lib/api";

export function Summary({
  fetch,
}: {
  fetch: () => Promise<{ ok: true; data: { summary?: SummaryPayload } } | { ok: false; status: number; message: string }>;
}) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "failed">("idle");
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [failure, setFailure] = useState("");

  async function generate() {
    setState("loading");
    const result = await fetch();
    if (!result.ok) {
      setFailure(result.message);
      setState("failed");
      return;
    }
    const found = result.data.summary;
    if (!found || !found.text) {
      setFailure(found?.error ?? "No summary was returned.");
      setState("failed");
      return;
    }
    setSummary(found);
    setState("done");
  }

  if (state === "idle") {
    return (
      <button
        onClick={() => void generate()}
        className="self-start rounded border border-hairline px-2.5 py-1 font-mono text-[11px] text-muted hover:text-ink"
      >
        ✨ Generate AI summary
      </button>
    );
  }

  if (state === "loading") {
    return <p className="font-mono text-[11px] text-faint">Asking the model for a summary…</p>;
  }

  if (state === "failed") {
    return (
      <p className="font-mono text-[11px] text-faint">
        No AI summary: {failure}
      </p>
    );
  }

  return (
    <div className="rounded border border-accent/30 bg-accent-soft px-3.5 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[10px] tracking-[0.08em] text-accent uppercase">
          AI-generated summary
        </span>
        {summary?.provider && (
          <span className="font-mono text-[10px] text-faint">{summary.provider}</span>
        )}
      </div>
      <p className="mt-1 text-sm">{summary?.text}</p>
      <p className="mt-1.5 font-mono text-[10.5px] text-faint">
        {summary?.disclaimer ?? "Verify against the evidence below."}
      </p>
    </div>
  );
}
