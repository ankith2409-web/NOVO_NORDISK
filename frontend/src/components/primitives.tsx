/**
 * The small pieces every view shares.
 *
 * The rule these encode: a state is never signalled by colour alone. Every
 * verdict and confidence chip carries its word too. Roughly one in twelve men
 * has some colour vision deficiency, audit packs get printed in monochrome, and
 * a projector in a bright room flattens the muted palette this tool uses on
 * purpose. A word costs a few pixels and survives all three.
 */
import type { ReactNode } from "react";
import type { Confidence, Verdict } from "@/lib/api";
import { cx } from "@/lib/cx";

const TONE = {
  ok: "bg-ok-soft text-ok",
  review: "bg-review-soft text-review",
  bad: "bg-bad-soft text-bad",
  accent: "bg-accent-soft text-accent",
  neutral: "bg-raised text-muted",
} as const;

export type Tone = keyof typeof TONE;

export function Chip({
  tone = "neutral",
  children,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5",
        "font-mono text-[11px] leading-4 tracking-wide whitespace-nowrap",
        TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

const VERDICT_TONE: Record<Verdict, Tone> = {
  consistent: "ok",
  review: "review",
  divergent: "bad",
};

/** Deliberately spelled out: "divergent" is the claim, the colour only echoes it. */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  return <Chip tone={VERDICT_TONE[verdict]}>{verdict}</Chip>;
}

const CONFIDENCE_TONE: Record<Confidence, Tone> = {
  high: "ok",
  medium: "review",
  low: "bad",
};

const CONFIDENCE_MEANING: Record<Confidence, string> = {
  high: "Stated outright by the model — the document can assert it.",
  medium: "Follows from a structural rule applied to a real object.",
  low: "Inferred from structure alone. Needs a person to confirm.",
};

export function ConfidenceChip({ level }: { level: Confidence }) {
  return (
    <Chip tone={CONFIDENCE_TONE[level]} title={CONFIDENCE_MEANING[level]}>
      {level}
    </Chip>
  );
}

/** A hash is only meaningful next to what it was taken over — see EvidenceDrawer. */
export function Fingerprint({ value, full }: { value: string; full?: string }) {
  return (
    <code
      title={full ? `SHA-256 · ${full}` : undefined}
      className="font-mono text-[11px] text-faint tabular"
    >
      {value}
    </code>
  );
}

export function Panel({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cx(
        "flex min-h-0 flex-col rounded border border-hairline bg-ground",
        className,
      )}
    >
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-3.5 py-2">
          <h2 className="font-mono text-[11px] tracking-[0.1em] text-faint uppercase">
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: number | string;
  tone?: Tone;
  hint?: string;
}) {
  const emphasis =
    tone === "bad" ? "text-bad" : tone === "review" ? "text-review" : "text-ink";
  return (
    <div className="rounded border border-hairline bg-ground px-3.5 py-3" title={hint}>
      <div className={cx("text-2xl leading-none font-semibold tabular", emphasis)}>
        {value}
      </div>
      <div className="mt-1.5 font-mono text-[10px] tracking-[0.09em] text-faint uppercase">
        {label}
      </div>
    </div>
  );
}

/** Failures arrive as values carrying a message meant for a person. Show it. */
export function Failure({ message, hint }: { message: string; hint?: string[] }) {
  return (
    <div className="m-3 rounded border border-bad/40 bg-bad-soft px-3.5 py-3">
      <p className="text-sm text-bad">{message}</p>
      {hint && hint.length > 0 && (
        <p className="mt-1.5 font-mono text-xs text-muted">
          Did you mean: {hint.join(", ")}
        </p>
      )}
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="p-4 font-mono text-xs text-faint">Reading {what}…</p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="p-4 text-sm text-muted">{children}</p>;
}
