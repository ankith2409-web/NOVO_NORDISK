/**
 * The generated BRD and FRD, with every statement bound to what it came from.
 *
 * Two rules the layout enforces. Confidence is visible on every row rather than
 * summarised at the top, because a document whose weakest sentence looks exactly
 * like its strongest invites someone to rely on an inference. And the evidence
 * is one click away on every requirement, never a separate report -- provenance
 * that costs a page load is provenance nobody checks.
 */
import { useState } from "react";
import { recallOneOf, remember } from "@/lib/remember";
import {
  api,
  SNAPSHOT_MODE,
  type Confidence,
  type Requirement,
  type RequirementsPayload,
} from "@/lib/api";
import {
  Button,
  Chip,
  ConfidenceChip,
  Empty,
  Failure,
  Loading,
  Stat,
  controlClasses,
} from "@/components/primitives";
import { RichText } from "@/components/RichText";
import { ChevronIcon, DownloadIcon } from "@/components/icons";
import { cx } from "@/lib/cx";
import { useLoad } from "@/lib/useLoad";

type Kind = "business" | "functional";

export function Requirements() {
  // Remembered because the two documents are read in different sittings --
  // coming back to a BRD review and landing on the FRD is a small, repeated
  // annoyance. The confidence filter deliberately is not: it is a momentary
  // narrowing, and restoring it would hide requirements without saying so.
  const [kind, setKindState] = useState<Kind>(
    () => recallOneOf<Kind>("requirements-kind", ["business", "functional"]) ?? "business",
  );

  function setKind(next: Kind) {
    setKindState(next);
    remember("requirements-kind", next);
  }
  const { data, error, retrying, reload } = useLoad<RequirementsPayload>(
    () => api.requirements(kind),
    [kind],
  );
  const [only, setOnly] = useState<Confidence | null>(null);

  const shown = data?.requirements.filter((r) => !only || r.confidence === only) ?? [];

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="font-serif text-2xl leading-tight font-semibold">
            {kind === "business" ? "Business requirements" : "Functional requirements"}
          </h1>
          <p className="mt-1 font-mono text-xs text-faint">
            Derived from the model by code, not written by a language model.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1">
          {(["business", "functional"] as Kind[]).map((option) => (
            <Button
              key={option}
              onClick={() => setKind(option)}
              aria-pressed={kind === option}
              tone={kind === option ? "selected" : "quiet"}
            >
              {option === "business" ? "BRD" : "FRD"}
            </Button>
          ))}
          <span className="mx-1 h-4 w-px bg-hairline" />
          <Download kind={kind} />
        </div>
      </header>

      {error && (
        <Failure
          message={error.message}
          status={error.status}
          what="the requirements"
          onRetry={reload}
          retrying={retrying}
        />
      )}
      {!data && !error && <Loading what="the requirements" rows={4} />}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            <Stat label="Requirements" value={data.counts.total} />
            <Stat label="Stated by the model" value={data.counts.high} hint="High confidence" />
            <Stat
              label="From a structural rule"
              value={data.counts.medium}
              hint="Medium confidence"
            />
            <Stat
              label="Inferred — needs a person"
              value={data.counts.low}
              tone={data.counts.low > 0 ? "review" : "neutral"}
              hint="Low confidence"
            />
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
              show
            </span>
            {([null, "high", "medium", "low"] as (Confidence | null)[]).map((level) => (
              <Button
                key={level ?? "all"}
                onClick={() => setOnly(level)}
                aria-pressed={only === level}
                tone={only === level ? "selected" : "quiet"}
              >
                {level ?? "all"}
              </Button>
            ))}
          </div>

          {shown.length === 0 ? (
            <Empty
              hint={
                only === "low"
                  ? "Nothing here was inferred — every statement in this document is either declared by the model or follows from a structural rule."
                  : `The document has ${data.counts.total} requirement${data.counts.total === 1 ? "" : "s"}, none at this confidence level.`
              }
              action={{ label: "Show all", onClick: () => setOnly(null) }}
            >
              No requirements at that confidence level.
            </Empty>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {shown.map((requirement) => (
                <Row key={requirement.id} requirement={requirement} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Getting the document out of the tool.
 *
 * The whole product derives a BRD and an FRD; until this existed they could
 * only be read on screen or regenerated from a terminal, which meant the one
 * artefact the work exists to produce was the one thing the interface could
 * not hand you.
 *
 * Real anchors with `download`, not fetch-then-blob: the browser already knows
 * how to name a file, show progress and put it in the right folder, and the
 * server already says what it is called. Word sits alongside Markdown because
 * a requirements document in a regulated setting is circulated for signature,
 * and that is a .docx.
 */
function Download({ kind }: { kind: Kind }) {
  // A snapshot is a static capture with no server behind it, so there is
  // nothing to render the document. Saying that beats a link that 404s.
  if (SNAPSHOT_MODE) return null;

  return (
    <span className="flex items-center gap-1">
      <span className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
        save
      </span>
      {(
        [
          { format: "md", label: ".md" },
          { format: "docx", label: ".docx" },
        ] as const
      ).map(({ format, label }) => (
        <a
          key={format}
          href={api.documentUrl(kind, format)}
          download
          className={controlClasses("quiet")}
          title={`Download the ${kind === "business" ? "BRD" : "FRD"} as ${label}`}
        >
          <DownloadIcon size={11} className="flex-none" />
          {label}
        </a>
      ))}
    </span>
  );
}

function Row({ requirement }: { requirement: Requirement }) {
  const [open, setOpen] = useState(false);

  return (
    <li
      className={cx(
        "rounded border bg-ground",
        requirement.needs_review ? "border-review/40" : "border-hairline",
      )}
    >
      <div className="flex items-start gap-2.5 px-3.5 py-2.5">
        <code className="mt-0.5 flex-none font-mono text-[11px] text-faint">
          {requirement.id}
        </code>
        <div className="min-w-0 flex-1">
          <p className={cx("text-sm", requirement.needs_review && "text-muted")}>
            <RichText>{requirement.statement}</RichText>
          </p>
          <p className="mt-1 text-xs text-muted">
            <RichText>{requirement.rationale}</RichText>
          </p>
        </div>
        <div className="flex flex-none flex-col items-end gap-1">
          <ConfidenceChip level={requirement.confidence} />
          <Chip>{requirement.category}</Chip>
        </div>
      </div>

      {requirement.evidence.length > 0 && (
        <div className="border-t border-hairline">
          <button
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className={cx(
              "flex w-full items-center gap-1.5 px-3.5 py-1.5 text-left",
              "font-mono text-[11px] text-faint transition-colors",
              "duration-(--duration-feedback) ease-(--ease-standard) hover:text-ink",
            )}
          >
            <ChevronIcon
              size={12}
              className={cx(
                "flex-none transition-transform",
                "duration-(--duration-feedback) ease-(--ease-standard)",
                open && "rotate-90",
              )}
            />
            bound to {requirement.evidence.length} object
            {requirement.evidence.length === 1 ? "" : "s"}
          </button>

          {open && (
            <ul className="divide-y divide-hairline border-t border-hairline">
              {requirement.evidence.map((evidence) => (
                <li key={evidence.node_id} className="px-3.5 py-2">
                  <code className="font-mono text-[11.5px]">{evidence.node_id}</code>
                  {evidence.detail && (
                    <pre className="mt-1 overflow-x-auto font-mono text-[11px] whitespace-pre-wrap text-muted">
                      {evidence.detail}
                    </pre>
                  )}
                  {/* The hash is what ties this sentence to that object. If the
                      object changes, this requirement is the one to revisit. */}
                  <code
                    title={`SHA-256 · ${evidence.fingerprint}`}
                    className="mt-1 block font-mono text-[10px] text-faint"
                  >
                    {evidence.short_fingerprint}
                  </code>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}
