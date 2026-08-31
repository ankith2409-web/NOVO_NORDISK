/**
 * Choosing which model you are looking at, and dropping the ones you are not.
 *
 * This was a `<select>` of `name · 12m · 8t` lines. Native selects are the
 * right default and were the wrong one here, for three reasons that all showed
 * up in use:
 *
 *  - Every row read identically, so the two kinds of model -- served by this
 *    deployment, and uploaded into this browser tab -- looked like the same
 *    thing. They are not: an uploaded model has no baseline to compare
 *    against, no warehouse, and no signable review queue, and someone who
 *    cannot see which they picked reads those absences as the tool being
 *    broken.
 *  - Whether a model *has* drift or a warehouse was invisible until you
 *    switched to it and found the tab greyed out. That belongs in the choice,
 *    not after it.
 *  - Removing an uploaded model was buried inside the upload dialog, which is
 *    the last place you look when what you want is to get rid of something.
 *    Delete now sits on the row for the thing being deleted.
 *
 * A select cannot carry any of that -- its options hold text and nothing else.
 * So this is a listbox: the same keyboard contract (arrows, Home/End, Enter,
 * Escape, type-ahead), with rows that can say more than a line of text.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { Button, Chip } from "@/components/primitives";
import { ChevronIcon, TrashIcon } from "@/components/icons";
import type { LoadedModel } from "@/lib/api";
import { cx } from "@/lib/cx";

/** How long a pause ends type-ahead, matching the platform convention. */
const TYPEAHEAD_MS = 700;

export function ModelPicker({
  loaded,
  active,
  onSwitch,
  onForget,
}: {
  loaded: LoadedModel[];
  active: string;
  onSwitch: (name: string) => void;
  /** Absent when this build cannot drop models (the static snapshot). */
  onForget?: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // The model a second click of the bin actually removes. Deleting a model is
  // cheap to undo -- upload it again -- but not free, and a bin sitting one
  // pixel from the row you meant to click deserves a beat. Inline rather than
  // a dialog: a modal over a dropdown means two layers to escape from.
  const [confirming, setConfirming] = useState("");
  const [cursor, setCursor] = useState(0);
  const listId = useId();
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const typed = useRef({ text: "", at: 0 });

  const activeEntry = loaded.find((entry) => entry.name === active);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    // `pointerdown`, not `click`: a click that starts inside the list and ends
    // outside it (a drag over a long name) should not count as dismissal.
    window.addEventListener("pointerdown", onPointer);
    return () => window.removeEventListener("pointerdown", onPointer);
  }, [open]);

  useEffect(() => {
    if (open) return;
    setConfirming("");
    typed.current = { text: "", at: 0 };
  }, [open]);

  // Before paint, so the list never renders scrolled to the top and then jumps.
  useLayoutEffect(() => {
    if (!open) return;
    const at = Math.max(0, loaded.findIndex((entry) => entry.name === active));
    setCursor(at);
    list.current?.querySelectorAll("li")[at]?.scrollIntoView({ block: "nearest" });
    (list.current?.querySelectorAll<HTMLElement>("[data-row]")[at])?.focus();
  }, [open, active, loaded]);

  function choose(name: string) {
    onSwitch(name);
    setOpen(false);
  }

  function move(to: number) {
    const next = Math.min(loaded.length - 1, Math.max(0, to));
    setCursor(next);
    list.current?.querySelectorAll<HTMLElement>("[data-row]")[next]?.focus();
  }

  function onKey(event: React.KeyboardEvent) {
    const keys: Record<string, number> = {
      ArrowDown: cursor + 1,
      ArrowUp: cursor - 1,
      Home: 0,
      End: loaded.length - 1,
    };
    if (event.key in keys) {
      event.preventDefault();
      move(keys[event.key]);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      root.current?.querySelector("button")?.focus();
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(loaded[cursor].name);
      return;
    }
    // Type-ahead. A server with five models is a list you scan; a server with
    // twenty is one you type at.
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey) {
      const now = Date.now();
      const text =
        (now - typed.current.at < TYPEAHEAD_MS ? typed.current.text : "") +
        event.key.toLowerCase();
      typed.current = { text, at: now };
      const hit = loaded.findIndex((entry) => entry.name.toLowerCase().startsWith(text));
      if (hit >= 0) move(hit);
    }
  }

  return (
    <div ref={root} className="relative">
      <Button
        onClick={() => setOpen((was) => !was)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        title="Choose which model to look at"
        className="max-w-[15rem]"
      >
        {/* `leading-normal`, overriding the control's `leading-none`: model
            names carry underscores, and a line box the exact height of the
            glyphs clips the descender off every one of them -- "Supply_Chain"
            read as "Supply Chain" in a screenshot. */}
        <span className="min-w-0 truncate leading-normal">{activeEntry?.name ?? active}</span>
        {activeEntry?.uploaded && (
          <span className="flex-none text-[10px] text-accent">yours</span>
        )}
        <ChevronIcon
          size={11}
          className={cx(
            // The set's chevron points right; a dropdown's points down, and
            // up while its list is showing.
            "flex-none transition-transform duration-(--duration-feedback)",
            open ? "-rotate-90" : "rotate-90",
          )}
        />
      </Button>

      {open && (
        <ul
          id={listId}
          ref={list}
          role="listbox"
          aria-label="Loaded models"
          onKeyDown={onKey}
          className={cx(
            "absolute top-full left-0 z-30 mt-1 max-h-[24rem] w-[22rem] max-w-[calc(100vw-1.5rem)]",
            "overflow-y-auto rounded border border-hairline bg-ground p-1 shadow-lg",
          )}
        >
          {loaded.map((entry, index) => (
            <Row
              key={entry.name}
              entry={entry}
              selected={entry.name === active}
              cursored={index === cursor}
              confirming={confirming === entry.name}
              onChoose={() => choose(entry.name)}
              onDelete={
                // Only uploaded models can be dropped. A model the server was
                // started on is not this browser's to remove, and offering a
                // bin that then refuses would be worse than not offering one.
                onForget && entry.uploaded
                  ? () => {
                      if (confirming === entry.name) {
                        onForget(entry.name);
                        setConfirming("");
                      } else {
                        setConfirming(entry.name);
                      }
                    }
                  : undefined
              }
              onCancelDelete={() => setConfirming("")}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({
  entry,
  selected,
  cursored,
  confirming,
  onChoose,
  onDelete,
  onCancelDelete,
}: {
  entry: LoadedModel;
  selected: boolean;
  cursored: boolean;
  confirming: boolean;
  onChoose: () => void;
  onDelete?: () => void;
  onCancelDelete: () => void;
}) {
  return (
    <li
      role="option"
      aria-selected={selected}
      className={cx(
        "flex items-start gap-2 rounded px-2 py-1.5",
        cursored && "bg-raised",
        selected && "bg-accent-soft",
      )}
    >
      <button
        type="button"
        data-row
        onClick={onChoose}
        // The row itself is the option; this button exists to make it
        // clickable and focusable without nesting interactive elements inside
        // an element that already carries `role="option"`.
        tabIndex={-1}
        className="min-w-0 flex-1 text-left outline-none"
      >
        <span className="flex items-baseline gap-1.5">
          <span className="min-w-0 truncate text-[13px]">{entry.name}</span>
          {entry.uploaded && (
            <span
              className="flex-none font-mono text-[10px] text-accent"
              title="Uploaded into this browser, held in memory, gone when the server restarts"
            >
              yours
            </span>
          )}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] text-faint">
          <span>
            {entry.measures} measures · {entry.tables} tables · {entry.source_format}
          </span>
          {/* Said on the row rather than discovered after switching. Both are
              per-model, not per-server: a deployment can hold a baseline for
              one model and a warehouse for another. */}
          {entry.capabilities.drift && <Chip tone="neutral">drift</Chip>}
          {entry.capabilities.reconcile && <Chip tone="neutral">warehouse</Chip>}
        </span>
      </button>

      {onDelete && (
        <span className="flex flex-none items-center gap-1">
          {confirming && (
            <>
              <span className="font-mono text-[10px] text-bad">remove?</span>
              <button
                type="button"
                onClick={onCancelDelete}
                tabIndex={-1}
                className="rounded px-1 py-0.5 font-mono text-[10px] text-muted hover:bg-raised"
              >
                no
              </button>
            </>
          )}
          <button
            type="button"
            onClick={onDelete}
            tabIndex={-1}
            aria-label={confirming ? `Confirm removing ${entry.name}` : `Remove ${entry.name}`}
            title={
              confirming
                ? `Remove ${entry.name} from this browser`
                : `Remove ${entry.name}. It is held in memory only, so this frees it; the file on your machine is untouched.`
            }
            className={cx(
              "rounded p-1",
              confirming ? "bg-bad-soft text-bad" : "text-faint hover:bg-raised hover:text-bad",
            )}
          >
            <TrashIcon size={13} />
          </button>
        </span>
      )}
    </li>
  );
}
