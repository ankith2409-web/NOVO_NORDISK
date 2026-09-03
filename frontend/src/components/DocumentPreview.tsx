/**
 * The document, on screen, laid out as a document.
 *
 * The save buttons beside this hand over a file; until this existed, deciding
 * whether that file was worth circulating meant saving it, opening it in
 * something else, and coming back. That is three steps to answer a question
 * about the thing already on the page.
 *
 * It renders the same Markdown the `.md` button saves, fetched from the same
 * URL with the same parameters. That is the point rather than a convenience:
 * one renderer means the two cannot drift apart. The .docx is Word's rendering
 * of the same content, so its typography differs and its words do not.
 *
 * Set in the serif face at a reading measure rather than in the interface's own
 * type, because it is a document and it is read like one.
 */
import { useEffect, useRef } from "react";
import { Button, Failure, Loading } from "@/components/primitives";
import { RichText } from "@/components/RichText";
import { splitBlocks, type Block } from "@/lib/blocks";
import { api } from "@/lib/api";
import { useLoad } from "@/lib/useLoad";
import { cx } from "@/lib/cx";

export function DocumentPreview({
  kind,
  title,
  sql,
  onClose,
}: {
  kind: "business" | "functional";
  /** What the document is called, for the dialog's heading. */
  title: string;
  /** Passed straight through, so what is read is what would be saved. */
  sql?: { grain: string[]; dialect: string };
  onClose: () => void;
}) {
  const dialect = sql?.dialect ?? "";
  const grain = (sql?.grain ?? []).join(" ");
  const { data, error, retrying, reload } = useLoad<string>(
    () => api.documentText(kind, sql),
    [kind, dialect, grain],
  );
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Focus moves into the dialog, so Escape and Tab both behave and a screen
    // reader is told it opened rather than left reading the page behind it.
    panel.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-4"
      onClick={onClose}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="preview-title"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded border border-hairline bg-ground shadow-xl outline-none animate-(--animate-rise)"
      >
        <header className="flex flex-none items-center gap-3 border-b border-hairline px-4 py-3">
          <h2 id="preview-title" className="font-serif text-lg font-semibold">
            {title}
          </h2>
          <Button onClick={onClose} className="ml-auto">
            Close
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-auto bg-surface px-4 py-5">
          {/* A page on a desk. The lighter ground and the narrow measure are
              what make this read as the document rather than as another panel
              of the interface around it. */}
          <article className="mx-auto max-w-[46rem] rounded border border-hairline bg-ground px-6 py-7 sm:px-9 sm:py-10">
            {error && (
              <Failure
                message={error.message}
                status={error.status}
                what="the document"
                onRetry={reload}
                retrying={retrying}
              />
            )}
            {!data && !error && <Loading what="the document" rows={6} />}
            {data && <Rendered source={data} />}
          </article>
        </div>
      </div>
    </div>
  );
}

function Rendered({ source }: { source: string }) {
  const blocks = splitBlocks(source);
  return (
    <div className="flex flex-col gap-3.5">
      {blocks.map((block, at) => (
        <One key={at} block={block} />
      ))}
    </div>
  );
}

/** The heading scale, in one place so the levels stay in proportion. */
const HEADING = {
  1: "font-serif text-[26px] leading-tight font-semibold",
  2: "mt-4 border-b border-hairline pb-1.5 font-serif text-[19px] font-semibold",
  3: "mt-2 font-mono text-[12.5px] font-semibold tracking-[0.02em] text-accent",
  4: "mt-2 font-serif text-[15px] font-semibold",
  5: "mt-2 font-serif text-[14px] font-semibold",
  6: "mt-2 font-serif text-[13px] font-semibold",
} as const;

function One({ block }: { block: Block }) {
  switch (block.kind) {
    case "heading": {
      const Tag = `h${block.level}` as "h1";
      return <Tag className={HEADING[block.level]}>{block.text}</Tag>;
    }

    case "paragraph":
      return (
        <p className="text-[13.5px] leading-relaxed text-ink">
          {block.lines.map((line, at) => (
            <span key={at}>
              {at > 0 && <br />}
              <RichText>{line}</RichText>
            </span>
          ))}
        </p>
      );

    case "quote":
      return (
        <blockquote className="border-l-2 border-accent bg-accent-soft px-3 py-2 text-[12.5px] leading-relaxed text-ink">
          {block.lines.map((line, at) => (
            <span key={at}>
              {at > 0 && " "}
              <RichText>{line}</RichText>
            </span>
          ))}
        </blockquote>
      );

    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag
          className={cx(
            "flex flex-col gap-1 pl-5 text-[13.5px] leading-relaxed text-ink",
            block.ordered ? "list-decimal" : "list-disc",
          )}
        >
          {block.items.map((item, at) => (
            <li key={at}>
              <RichText>{item}</RichText>
            </li>
          ))}
        </Tag>
      );
    }

    case "code":
      // Its own scroll container, never the page's: a generated SQL query is
      // wider than any measure worth reading prose at, and letting it push the
      // article would make the whole document scroll sideways.
      return (
        <pre className="overflow-x-auto rounded border border-hairline bg-surface px-3 py-2.5">
          <code className="font-mono text-[11px] leading-relaxed whitespace-pre text-ink">
            {block.text}
          </code>
        </pre>
      );

    case "table":
      return (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr>
                {block.head.map((cell, at) => (
                  <th
                    key={at}
                    className="border-b border-edge px-2 py-1.5 text-left font-medium text-muted"
                  >
                    <RichText>{cell}</RichText>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, at) => (
                <tr key={at}>
                  {row.map((cell, index) => (
                    <td
                      key={index}
                      className="border-b border-hairline px-2 py-1.5 align-top text-ink"
                    >
                      <RichText>{cell}</RichText>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}
