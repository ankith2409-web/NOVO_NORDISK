/**
 * The line between two panes, and the grip that moves it.
 *
 * Drawn as a hairline and hit as a 9px target. Those are different numbers on
 * purpose: a divider thick enough to grab comfortably is thick enough to read
 * as a gutter, and a gutter between every pane makes a dense tool look
 * padded. So the visible rule stays one pixel and the pointer target extends
 * either side of it, which is the same trick a window manager uses.
 *
 * The grip only appears on hover or focus. A permanent one would put three
 * dots down the side of the page whether or not anybody ever intends to drag
 * it; absent, the affordance is discovered by approaching it, which is where
 * a reader's cursor already is when they want it.
 */
import { GripIcon } from "@/components/icons";
import type { Resizable } from "@/lib/useResizable";
import { cx } from "@/lib/cx";

export function Divider({ pane }: { pane: Resizable }) {
  return (
    <div
      {...pane.handle}
      className={cx(
        "group relative z-10 -mx-1 w-[9px] flex-none cursor-col-resize",
        "focus-visible:outline-none",
      )}
    >
      {/* The rule itself, centred in the target. */}
      <span
        aria-hidden
        className={cx(
          "pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2",
          "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
          pane.dragging
            ? "bg-accent"
            : "bg-hairline group-hover:bg-edge group-focus-visible:bg-accent",
        )}
      />
      <span
        aria-hidden
        className={cx(
          "pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2",
          "rounded-full bg-surface text-faint opacity-0 transition-opacity",
          "duration-(--duration-feedback) ease-(--ease-standard)",
          "group-hover:opacity-100 group-focus-visible:opacity-100",
          pane.dragging && "opacity-100 text-accent",
        )}
      >
        <GripIcon size={13} />
      </span>
    </div>
  );
}
