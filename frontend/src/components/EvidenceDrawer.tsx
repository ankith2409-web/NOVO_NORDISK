/**
 * Why a fingerprint is proof, shown rather than asserted.
 *
 * A hash on its own is a demand for trust. The claim this tool makes is
 * specific -- that reformatting a definition leaves the fingerprint alone while
 * changing what it computes moves it -- and that claim is only checkable if the
 * canonical form sits next to the hash. So the drawer shows the whole chain:
 * what the author wrote, what that reduces to, and what was hashed.
 *
 * The middle step is the one that matters and the one every other tool omits.
 * Someone can read the canonical form, see that casing and whitespace are gone
 * while the string literal "Failed" survives with its capital F, and understand
 * without being told why renaming a variable is silent and changing a filter is
 * not.
 */
import { useState } from "react";
import { Chip } from "@/components/primitives";
import { cx } from "@/lib/cx";

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
      className="rounded border border-hairline px-1.5 py-0.5 font-mono text-[10px] text-faint hover:text-ink"
      aria-label={`Copy ${label}`}
    >
      {copied ? "copied" : "copy"}
    </button>
  );
}

function Step({
  index,
  title,
  note,
  children,
  action,
}: {
  index: number;
  title: string;
  note: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <li className="grid grid-cols-[1.5rem_1fr] gap-x-2.5">
      <span className="pt-0.5 text-center font-mono text-[11px] text-faint tabular">
        {index}
      </span>
      <div className="min-w-0">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-[13px] font-semibold">{title}</h4>
          {action}
        </div>
        <p className="mt-0.5 mb-1.5 text-xs text-muted">{note}</p>
        {children}
      </div>
    </li>
  );
}

function Code({ children, tone }: { children: string; tone?: "accent" }) {
  return (
    <pre
      className={cx(
        "overflow-x-auto rounded border border-hairline bg-surface px-2.5 py-2",
        "font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap",
        tone === "accent" && "border-accent/30",
      )}
    >
      {children}
    </pre>
  );
}

export function EvidenceDrawer({
  expression,
  canonical,
  fingerprint,
  subject,
}: {
  expression: string;
  canonical: string;
  fingerprint: string;
  subject: string;
}) {
  const reformattingOnly = canonical && expression.trim() !== canonical;

  return (
    <section className="rounded border border-hairline bg-ground">
      <header className="flex items-center justify-between gap-3 border-b border-hairline px-3.5 py-2">
        <h3 className="font-mono text-[11px] tracking-[0.1em] text-faint uppercase">
          Evidence
        </h3>
        <Chip tone="accent">SHA-256</Chip>
      </header>

      <ol className="space-y-3.5 px-3.5 py-3">
        <Step
          index={1}
          title="What the author wrote"
          note={`The DAX stored in the model for ${subject}.`}
          action={<CopyButton value={expression} label="the expression" />}
        >
          <Code>{expression}</Code>
        </Step>

        <Step
          index={2}
          title="What it reduces to"
          note={
            reformattingOnly
              ? "Casing, whitespace and comments are gone; string literals keep their exact case, because they are data rather than syntax."
              : "This expression was already in canonical form."
          }
        >
          <Code tone="accent">{canonical}</Code>
        </Step>

        <Step
          index={3}
          title="What was hashed"
          note="Taken over step 2, never step 1 — which is why reformatting is silent and changing a filter is not."
          action={<CopyButton value={fingerprint} label="the fingerprint" />}
        >
          <code className="block overflow-x-auto rounded border border-hairline bg-surface px-2.5 py-2 font-mono text-[11px] break-all text-muted">
            {fingerprint}
          </code>
        </Step>
      </ol>
    </section>
  );
}
