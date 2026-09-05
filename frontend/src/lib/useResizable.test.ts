/**
 * The two rules a draggable pane has to keep.
 *
 * A pane that can reach zero can be lost by accident, with nothing left on
 * screen to drag back -- so every path that sets a width goes through one
 * clamp, and that clamp is tested here rather than trusted.
 *
 * And a width read back from storage is not trustworthy input. It may have
 * been written by an older build with different bounds, edited by hand, or
 * simply not be there. The rule is that a width outside the current range is
 * *discarded* rather than squeezed into it: it was chosen against limits that
 * no longer apply, so it is not evidence of what this reader wants now.
 */

import { describe, expect, it } from "vitest";
import { STEP, clamp, storedWidth } from "./useResizable";

describe("clamping a pane", () => {
  it("keeps a width that is already in range", () => {
    expect(clamp(200, 132, 320)).toBe(200);
  });

  it("never lets a pane reach zero", () => {
    expect(clamp(0, 132, 320)).toBe(132);
    expect(clamp(-500, 132, 320)).toBe(132);
  });

  it("never lets a pane take the whole window", () => {
    expect(clamp(5000, 132, 320)).toBe(320);
  });

  it("holds the bounds themselves", () => {
    expect(clamp(132, 132, 320)).toBe(132);
    expect(clamp(320, 132, 320)).toBe(320);
  });

  it("returns whole pixels, because a fractional width blurs a hairline", () => {
    expect(Number.isInteger(clamp(200.6, 132, 320))).toBe(true);
    expect(clamp(200.6, 132, 320)).toBe(201);
  });
});

describe("the remembered width", () => {
  it("opens at the default on a first visit", () => {
    expect(storedWidth(null, 132, 320, 176)).toBe(176);
  });

  it("restores a width that is still legal", () => {
    expect(storedWidth("240", 132, 320, 176)).toBe(240);
  });

  it("discards a width from a build with different bounds", () => {
    // Not clamped to 320: 900 was chosen against limits that no longer apply,
    // so it says nothing about what this reader wants now.
    expect(storedWidth("900", 132, 320, 176)).toBe(176);
    expect(storedWidth("40", 132, 320, 176)).toBe(176);
  });

  it("survives a value that is not a number at all", () => {
    for (const junk of ["", "wide", "NaN", "{}", "12px"]) {
      expect(storedWidth(junk, 132, 320, 176)).toBe(176);
    }
  });

  it("takes the bounds as inclusive", () => {
    expect(storedWidth("132", 132, 320, 176)).toBe(132);
    expect(storedWidth("320", 132, 320, 176)).toBe(320);
  });
});

describe("the keyboard step", () => {
  it("moves far enough to be worth pressing and not so far as to overshoot", () => {
    // Small enough to place a divider precisely, large enough that crossing a
    // 190px range does not take twelve presses.
    expect(STEP).toBeGreaterThanOrEqual(8);
    expect(STEP).toBeLessThanOrEqual(32);
  });
});
