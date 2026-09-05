/**
 * A pane the reader can size for themselves.
 *
 * The layout shipped with two fixed widths: a 176px nav and a 320px copilot.
 * Both are reasonable defaults and neither is right for everyone -- a long
 * table of DAX wants the copilot narrow or gone, and a conversation about a
 * measure wants it wide. A fixed pane makes that a choice the reader cannot
 * make.
 *
 * Three details are what separate a drag handle that feels solid from one that
 * feels like a bug.
 *
 * The pointer is captured. Without `setPointerCapture` the drag stops the
 * moment the cursor outruns the 6px handle, which on any quick movement is
 * immediately, and the pane sticks halfway.
 *
 * Text selection is suppressed for the duration. Dragging across a page
 * otherwise highlights every paragraph it crosses, and the reader is left
 * holding a blue page.
 *
 * And the width is clamped to a range, so a pane can be made narrow but never
 * lost. A control that can be dragged to zero is a control that can be dragged
 * to zero by accident, with nothing left on screen to drag back.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface Resizable {
  width: number;
  /** Spread onto the divider element. */
  handle: {
    onPointerDown: (event: React.PointerEvent<HTMLElement>) => void;
    onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => void;
    onDoubleClick: () => void;
    role: "separator";
    tabIndex: 0;
    "aria-orientation": "vertical";
    "aria-valuenow": number;
    "aria-valuemin": number;
    "aria-valuemax": number;
    "aria-label": string;
  };
  dragging: boolean;
  reset: () => void;
}

/** How far one arrow-key press moves a divider. */
export const STEP = 16;

/**
 * A width held inside its range.
 *
 * Separated out and exported because this is the safety property, not a
 * detail: a pane that can be dragged to zero can be lost by accident, with
 * nothing left on screen to drag back. Everything that can change a width --
 * the pointer, the arrow keys, and a value read back from storage -- goes
 * through here.
 */
export function clamp(width: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(width)));
}

/**
 * The width to open at, given whatever was stored last time.
 *
 * `raw` is whatever `localStorage` held, which is to say anything at all: a
 * width written by an older build with different bounds, a value edited by
 * hand, `null` on a first visit, or `"NaN"`. A stored width outside the
 * current range is discarded rather than clamped into it -- it was chosen
 * against different limits, so it is not evidence of what this reader wants
 * now, and the default is the better guess.
 */
export function storedWidth(
  raw: string | null,
  min: number,
  max: number,
  initial: number,
): number {
  const found = Number(raw);
  if (raw === null || raw === "" || !Number.isFinite(found)) return initial;
  return found >= min && found <= max ? found : initial;
}

export function useResizable({
  key,
  initial,
  min,
  max,
  /** Which way the pane grows as the pointer moves right. */
  edge = "start",
  label,
}: {
  /** Where the chosen width is remembered, per pane. */
  key: string;
  initial: number;
  min: number;
  max: number;
  edge?: "start" | "end";
  label: string;
}): Resizable {
  const [width, setWidth] = useState(() => {
    // Wrapped: a browser with site data blocked throws on read, and a layout
    // that cannot render because it could not remember a width is a worse
    // failure than one that opens at its default.
    try {
      return storedWidth(localStorage.getItem(key), min, max, initial);
    } catch {
      /* site data blocked; the default is fine */
      return initial;
    }
  });
  const [dragging, setDragging] = useState(false);
  const from = useRef({ x: 0, width: 0 });

  const hold = useCallback(
    (next: number) => {
      const held = clamp(next, min, max);
      setWidth(held);
      return held;
    },
    [min, max],
  );

  useEffect(() => {
    try {
      localStorage.setItem(key, String(width));
    } catch {
      /* a width that cannot be remembered is still a width that works */
    }
  }, [key, width]);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      // Captured, or the drag ends as soon as the cursor outruns the handle.
      event.currentTarget.setPointerCapture(event.pointerId);
      from.current = { x: event.clientX, width };
      setDragging(true);

      const move = (moved: PointerEvent) => {
        const travelled = moved.clientX - from.current.x;
        hold(from.current.width + (edge === "start" ? travelled : -travelled));
      };
      const stop = () => {
        setDragging(false);
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", stop);
        document.body.style.removeProperty("user-select");
        document.body.style.removeProperty("cursor");
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", stop);
      // Or the drag highlights every paragraph it crosses.
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
    },
    [width, hold, edge],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      // A divider that only answers to a pointer is a divider half the people
      // using this cannot move.
      const towards = edge === "start" ? 1 : -1;
      if (event.key === "ArrowLeft") hold(width - STEP * towards);
      else if (event.key === "ArrowRight") hold(width + STEP * towards);
      else if (event.key === "Home") hold(min);
      else if (event.key === "End") hold(max);
      else return;
      event.preventDefault();
    },
    [width, hold, edge, min, max],
  );

  const reset = useCallback(() => setWidth(initial), [initial]);

  return {
    width,
    dragging,
    reset,
    handle: {
      onPointerDown,
      onKeyDown,
      onDoubleClick: reset,
      role: "separator",
      tabIndex: 0,
      "aria-orientation": "vertical",
      "aria-valuenow": width,
      "aria-valuemin": min,
      "aria-valuemax": max,
      "aria-label": label,
    },
  };
}
