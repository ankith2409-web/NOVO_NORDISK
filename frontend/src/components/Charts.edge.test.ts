/**
 * What the charts do when the data is degenerate.
 *
 * Every chart here is fed numbers out of somebody's model, and a model is
 * under no obligation to be reasonable. A split can come back with one group,
 * or none; a measure can be zero everywhere, or negative; a store list can be
 * a single shop, which gives a map a bounding box of zero width and the
 * projection a chance to divide by it. A location can sit on the antimeridian,
 * where a tile index wraps, or at a pole, where Mercator runs to infinity.
 *
 * None of that may throw. A chart that renders nothing is a bad chart; a chart
 * that takes the page down with it loses the reader everything else on the
 * page, including the figures that were fine.
 *
 * These are the shapes a normal fixture never produces, written by
 * deliberately trying to break the thing rather than by waiting for a model to
 * do it.
 */

import { describe, expect, it } from "vitest";
import {
  arrange,
  brief,
  chartFor,
  degrees,
  exact,
  fitZoom,
  gridStep,
  mercatorY,
  placeLabels,
  ranks,
  scaleBar,
  tileAt,
} from "./Charts";

const slice = (label: string, value: number) => ({ label, value, order: "" });

describe("a split with too few groups to be one", () => {
  it("survives a single group", () => {
    const one = [slice("a", 5)];
    expect(() => ranks(one)).not.toThrow();
    expect(() => arrange(one, "largest")).not.toThrow();
    expect(() => chartFor(one)).not.toThrow();
  });

  it("survives no groups at all", () => {
    expect(() => ranks([])).not.toThrow();
    expect(() => arrange([], "time")).not.toThrow();
    expect(() => chartFor([])).not.toThrow();
  });
});

describe("values a measure can honestly return", () => {
  it("survives every group being zero", () => {
    // A ranking with no spread, and a denominator of zero waiting to happen.
    const flat = [slice("a", 0), slice("b", 0)];
    expect(() => ranks(flat)).not.toThrow();
    expect(chartFor(flat)).toBeTruthy();
  });

  it("survives a measure that goes both ways", () => {
    const signed = [slice("a", -5), slice("b", 3)];
    expect(() => ranks(signed)).not.toThrow();
    expect(chartFor(signed)).toBeTruthy();
  });

  it("abbreviates anything a float can hold", () => {
    for (const value of [0, -0, 1e-9, -1e12, 1e15, NaN, Infinity, -Infinity]) {
      expect(() => brief(value)).not.toThrow();
      expect(() => exact(value)).not.toThrow();
    }
  });
});

describe("a map of one place", () => {
  it("picks a finite zoom for a bounding box of no size", () => {
    // One store: north equals south, east equals west, and the span the fit
    // divides by is zero.
    const same = { minLat: 41.8, maxLat: 41.8, minLon: -87.6, maxLon: -87.6 };
    const zoom = fitZoom(same, { width: 460, height: 296 });
    expect(Number.isFinite(zoom)).toBe(true);
    expect(zoom).toBeGreaterThanOrEqual(0);
  });

  it("never offers a grid or a scale bar of zero", () => {
    // Both are divided by; both are drawn as a length.
    expect(gridStep(0)).toBeGreaterThan(0);
    expect(gridStep(-1)).toBeGreaterThan(0);
    expect(scaleBar(0)).toBeGreaterThan(0);
  });
});

describe("the edges of the world", () => {
  it("writes a coordinate at either pole and either side", () => {
    for (const value of [0, 90, -90, 180, -180]) {
      expect(() => degrees(value, "lat", 0.01)).not.toThrow();
    }
  });

  it("never addresses a tile outside the world, anywhere", () => {
    for (const [lat, lon] of [
      [90, 180],
      [-90, -180],
      [85.05, 179.99],
    ]) {
      for (const zoom of [0, 3, 18]) {
        const tile = tileAt(lat, lon, zoom);
        expect(tile.x).toBeGreaterThanOrEqual(0);
        expect(tile.y).toBeGreaterThanOrEqual(0);
        expect(tile.x).toBeLessThan(2 ** zoom);
        expect(tile.y).toBeLessThan(2 ** zoom);
      }
    }
  });

  it("keeps the projection inside the unit square", () => {
    for (const lat of [90, -90, 89.999, -89.999, 0]) {
      const y = mercatorY(lat);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(1);
    }
  });
});

describe("placing labels with nothing to place them on", () => {
  it("returns nothing for nothing", () => {
    expect(placeLabels([], { width: 100, height: 100 })).toEqual([]);
  });

  it("survives a box with no room in it", () => {
    expect(() =>
      placeLabels([{ x: 0, y: 0, r: 0, width: 0 }], { width: 0, height: 0 }),
    ).not.toThrow();
  });
});
