/**
 * Arriving at the thing you searched for, rather than at the page it is on.
 *
 * A search result that drops somebody at the top of a page of forty measures
 * has answered "which page" and left "where on it" to them, which is most of
 * the work. So the destination is scrolled to and marked, briefly, in the one
 * place the eye is already going.
 *
 * The mark fades rather than persisting: it says "here", and a highlight that
 * stayed would then be saying something about the object itself.
 */
import { useEffect, useState } from "react";

/** Long enough to be seen after the scroll settles, short enough not to linger. */
const HELD_MS = 2200;

/** Frames to keep looking for a target a view has not rendered yet. */
const TRIES = 12;

export interface FocusRequest {
  target: string;
  /** When it was asked for. Two lookups of the same name are two arrivals. */
  at: number;
}

/**
 * Scroll `focus.target` into view within `root`, and return what is marked.
 *
 * Elements opt in with `data-focus="<name>"`. Matching is case-insensitive
 * because a person typing a name is not copying it, and the search that sent
 * them here was not case-sensitive either.
 *
 * The lookup is deferred by a frame: a view that expands a section in response
 * to the same request has not rendered it yet when this effect first runs, and
 * scrolling to an element that does not exist would silently do nothing.
 */
export function useFocusTarget(
  focus: FocusRequest | null,
  root?: React.RefObject<HTMLElement | null>,
): string | null {
  const [marked, setMarked] = useState<string | null>(null);

  useEffect(() => {
    if (!focus?.target) {
      setMarked(null);
      return;
    }
    let cleared = 0;
    let frame = 0;
    let tries = 0;

    // Retried for a few frames rather than looked for once. A view often has
    // to render twice before the target exists -- the dashboard opens the
    // report page holding a tile, and the tile itself only mounts on the
    // render after that -- and a single lookup lands in the gap and silently
    // finds nothing. Bounded, so a target that genuinely is not on the page
    // stops rather than spinning.
    const look = () => {
      const scope: ParentNode = root?.current ?? document;
      const wanted = focus.target.toLowerCase();
      const found = Array.from(
        scope.querySelectorAll<HTMLElement>("[data-focus]"),
      ).find((node) => (node.dataset.focus ?? "").toLowerCase() === wanted);

      if (!found) {
        if (++tries < TRIES) {
          frame = requestAnimationFrame(look);
          return;
        }
        setMarked(null);
        return;
      }
      found.scrollIntoView({ block: "center", behavior: "smooth" });
      setMarked(focus.target);
      cleared = window.setTimeout(() => setMarked(null), HELD_MS);
    };
    frame = requestAnimationFrame(look);

    return () => {
      cancelAnimationFrame(frame);
      if (cleared) window.clearTimeout(cleared);
    };
  }, [focus?.target, focus?.at, root]);

  return marked;
}

/** True when `name` is the thing just arrived at. */
export function isMarked(marked: string | null, name: string): boolean {
  return marked !== null && marked.toLowerCase() === name.toLowerCase();
}

/** The ring drawn around it. One definition, so every view marks alike. */
export const MARK_CLASS = "ring-2 ring-accent ring-offset-2 ring-offset-surface";
