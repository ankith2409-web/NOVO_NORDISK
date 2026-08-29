/**
 * Reading your own Power BI model, without a terminal.
 *
 * Everything else here documents whatever the server was started with, which is
 * the right shape for a deployment and the wrong one for the first five minutes
 * with the tool: the answer to "does this work on *my* model" was previously
 * "clone the repo, install Python, and pass a path". This is that answer,
 * reduced to dropping a file on a box.
 *
 * The upload is held for this browser session and nothing else. That is a
 * deliberate limit rather than a shortcut -- a .pbix is somebody's proprietary
 * business logic, and a demo server that kept it, or showed it to the next
 * visitor, would be a worse failure than not having the feature. It is said on
 * screen, not just in the code, because a person deciding whether to upload a
 * confidential model needs to know before they drop it, not after.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type LoadedModel, type Uploaded } from "@/lib/api";
import { Button, Failure } from "@/components/primitives";
import { TrashIcon, UploadIcon } from "@/components/icons";
import { cx } from "@/lib/cx";

/** Mirrors `upload.ACCEPTED` on the server. Shown, not just enforced. */
const ACCEPTS = ".pbix,.zip,.tmdl";

export function UploadDialog({
  loaded,
  onClose,
  onLoaded,
  onForgotten,
}: {
  /** Everything the switcher currently holds, so uploads can be listed here. */
  loaded: LoadedModel[];
  onClose: () => void;
  /** Called once the server has read the file, with the name it gave it. */
  onLoaded: (result: Uploaded) => void;
  onForgotten: (name: string) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [problem, setProblem] = useState<{ status: number; message: string } | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const busy = progress !== null;

  const mine = loaded.filter((entry) => entry.uploaded);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  const send = useCallback(
    async (file: File) => {
      setProblem(null);
      // Starts at 0 rather than at the first progress event, so the box shows
      // it is working from the moment the file is handed over. A large .pbix
      // spends a visible beat being read before a single byte is on the wire.
      setProgress(0);
      const result = await api.upload(file, setProgress);
      setProgress(null);
      if (!result.ok) {
        setProblem({ status: result.status, message: result.message });
        return;
      }
      onLoaded(result.data);
    },
    [onLoaded],
  );

  function choose(files: FileList | null) {
    const file = files?.[0];
    // One at a time. Reading several would mean several parses, several
    // names to disambiguate and one progress bar that means nothing -- and
    // nobody has ever wanted to document four models at the same instant.
    if (file) void send(file);
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-4"
      onClick={() => !busy && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-title"
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-full w-full max-w-lg flex-col overflow-hidden rounded border border-hairline bg-ground shadow-xl animate-(--animate-rise)"
      >
        <header className="border-b border-hairline px-4 py-3">
          <h2 id="upload-title" className="font-serif text-lg font-semibold">
            Open your own model
          </h2>
          <p className="mt-1 text-xs text-muted">
            Everything in this interface — the measures, the requirements, the SQL,
            the documents — will be derived from your file instead.
          </p>
        </header>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          <div
            onDragOver={(event) => {
              event.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              if (!busy) choose(event.dataTransfer.files);
            }}
            className={cx(
              "flex flex-col items-center gap-2 rounded border-2 border-dashed px-4 py-8 text-center",
              "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
              dragging ? "border-accent bg-accent-soft" : "border-hairline bg-surface",
              busy && "opacity-60",
            )}
          >
            <UploadIcon size={22} className="text-faint" />
            <p className="text-sm text-ink">
              Drop a file here, or{" "}
              <button
                type="button"
                disabled={busy}
                onClick={() => picker.current?.click()}
                className="font-medium text-accent underline underline-offset-2 disabled:no-underline disabled:opacity-60"
              >
                choose one
              </button>
            </p>
            <p className="font-mono text-[11px] text-faint">
              .pbix &nbsp;·&nbsp; .zip of a .pbip or .SemanticModel folder &nbsp;·&nbsp; .tmdl
            </p>
            <input
              ref={picker}
              type="file"
              accept={ACCEPTS}
              className="sr-only"
              onChange={(event) => {
                choose(event.target.files);
                // Cleared so choosing the same file twice fires a second
                // change event. Without this, re-uploading after a failure
                // silently does nothing.
                event.target.value = "";
              }}
            />
          </div>

          {busy && (
            <div className="mt-3">
              <div className="flex items-baseline justify-between text-[11px] text-muted">
                <span>
                  {progress! < 1 ? "Sending…" : "Reading the model…"}
                </span>
                <span className="font-mono tabular-nums">
                  {Math.round(progress! * 100)}%
                </span>
              </div>
              <div className="mt-1 h-1 overflow-hidden rounded bg-raised">
                <div
                  className="h-full bg-accent transition-[width] duration-(--duration-feedback)"
                  style={{ width: `${Math.max(2, progress! * 100)}%` }}
                />
              </div>
              {/* The bar reaches 100% when the bytes are sent, and the parse
                  happens after that. Saying so is the difference between a
                  pause that looks finished and one that looks broken. */}
              {progress! >= 1 && (
                <p className="mt-1 text-[11px] text-faint">
                  Parsing happens after the upload, so this pause is the model being read.
                </p>
              )}
            </div>
          )}

          {problem && !busy && (
            <div className="mt-3">
              <Failure message={problem.message} status={problem.status} what="that file" />
            </div>
          )}

          <p className="mt-4 rounded border border-hairline bg-surface px-3 py-2 text-[11px] leading-relaxed text-muted">
            Your file is read into memory, never written to this server&rsquo;s disk, and
            is visible only to this browser. It is gone when the server restarts, and
            you can remove it before then. Review decisions cannot be recorded against
            an uploaded model, because it does not outlive the session they would be
            filed under.
          </p>

          {mine.length > 0 && (
            <section className="mt-4">
              <h3 className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
                open in this browser
              </h3>
              <ul className="mt-1.5 flex flex-col gap-1">
                {mine.map((entry) => (
                  <li
                    key={entry.name}
                    className="flex items-center gap-2 rounded border border-hairline bg-surface px-2.5 py-1.5"
                  >
                    <span className="min-w-0 flex-1 truncate text-sm">{entry.name}</span>
                    <span className="flex-none font-mono text-[11px] text-faint">
                      {entry.measures}m · {entry.tables}t · {entry.source_format}
                    </span>
                    <button
                      type="button"
                      onClick={() => onForgotten(entry.name)}
                      aria-label={`Remove ${entry.name}`}
                      title={`Remove ${entry.name} from this browser`}
                      className="flex-none rounded p-1 text-faint hover:bg-raised hover:text-bad"
                    >
                      <TrashIcon size={13} />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-hairline px-4 py-3">
          <Button onClick={onClose} disabled={busy}>
            {mine.length > 0 ? "Done" : "Cancel"}
          </Button>
        </footer>
      </div>
    </div>
  );
}
