/**
 * Arriving at the thing you searched for, rather than at the page it is on.
 *
 * Two behaviours here are not obvious and both were found by driving a real
 * browser rather than by reasoning about the code:
 *
 * The retry. A view often has to render twice before the target exists -- the
 * dashboard has to open the report page a tile sits on, and the tile only
 * mounts on the render after that. A single lookup lands in the gap, finds
 * nothing, and the search result silently drops the reader at the top of the
 * page. That failure looks exactly like success from the code's point of view,
 * which is why it is pinned here.
 *
 * And the timestamp. Looking the same name up twice is two arrivals, and a
 * hook keyed only on the name would not notice the second one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

import { isMarked, useFocusTarget } from "./useFocusTarget";

/** Run `n` animation frames. The hook looks again on each one. */
async function frames(n: number) {
  for (let i = 0; i < n; i++) {
    await act(async () => {
      vi.advanceTimersByTime(16);
      await Promise.resolve();
    });
  }
}

function plant(name: string) {
  const node = document.createElement("div");
  node.dataset.focus = name;
  node.scrollIntoView = vi.fn();
  document.body.append(node);
  return node;
}

beforeEach(() => {
  vi.useFakeTimers();
  // jsdom has no rAF loop of its own that fake timers drive, so it is mapped
  // onto a timeout -- which is what `advanceTimersByTime` above then moves.
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) =>
    setTimeout(() => fn(performance.now()), 16) as unknown as number,
  );
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useFocusTarget", () => {
  it("marks and scrolls to a target that is already on the page", async () => {
    const node = plant("Total Sales");
    const { result } = renderHook(() =>
      useFocusTarget({ target: "Total Sales", at: 1 }),
    );

    await frames(2);
    expect(result.current).toBe("Total Sales");
    expect(node.scrollIntoView).toHaveBeenCalled();
  });

  it("keeps looking for a target the view has not rendered yet", async () => {
    const { result } = renderHook(() => useFocusTarget({ target: "Late Tile", at: 1 }));

    await frames(3);
    expect(result.current).toBeNull();

    // The view opens the section holding it, three frames in.
    const node = plant("Late Tile");
    await frames(3);

    expect(result.current).toBe("Late Tile");
    expect(node.scrollIntoView).toHaveBeenCalled();
  });

  it("gives up rather than spinning on a target that never appears", async () => {
    const { result } = renderHook(() => useFocusTarget({ target: "Nowhere", at: 1 }));
    await frames(30);
    expect(result.current).toBeNull();

    // And it has genuinely stopped, not merely still found nothing: an element
    // arriving long after the request is not what that request asked for. An
    // unbounded loop would scroll the page half a minute after the click,
    // under whoever is now reading something else.
    plant("Nowhere");
    await frames(10);
    expect(result.current).toBeNull();
  });

  it("clears the mark after a moment, so it says here rather than what", async () => {
    plant("Total Sales");
    const { result } = renderHook(() =>
      useFocusTarget({ target: "Total Sales", at: 1 }),
    );
    await frames(2);
    expect(result.current).toBe("Total Sales");

    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
    });
    expect(result.current).toBeNull();
  });

  it("treats the same name asked for twice as two arrivals", async () => {
    plant("Total Sales");
    const { result, rerender } = renderHook(
      (focus: { target: string; at: number }) => useFocusTarget(focus),
      { initialProps: { target: "Total Sales", at: 1 } },
    );

    await frames(2);
    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
    });
    expect(result.current).toBeNull();

    // Same name, looked up again. Keyed on the name alone this would not fire.
    rerender({ target: "Total Sales", at: 2 });
    await frames(2);
    expect(result.current).toBe("Total Sales");
  });

  it("does nothing at all when nothing was asked for", async () => {
    plant("Total Sales");
    const { result } = renderHook(() => useFocusTarget(null));
    await frames(3);
    expect(result.current).toBeNull();
  });

  it("matches without regard to case, because a person typing is not copying", async () => {
    const node = plant("Total Sales");
    const { result } = renderHook(() =>
      useFocusTarget({ target: "total sales", at: 1 }),
    );
    await frames(2);
    expect(result.current).toBe("total sales");
    expect(node.scrollIntoView).toHaveBeenCalled();
  });
});

describe("isMarked", () => {
  it("is false for nothing marked, whatever the name", () => {
    expect(isMarked(null, "Total Sales")).toBe(false);
  });

  it("compares without case, matching how the target was found", () => {
    expect(isMarked("total sales", "Total Sales")).toBe(true);
    expect(isMarked("Total Sales", "Total Sales")).toBe(true);
  });

  it("does not match a name that merely contains the mark", () => {
    expect(isMarked("Sales", "Total Sales")).toBe(false);
  });
});
