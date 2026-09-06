/**
 * Reading and writing the address bar.
 *
 * These exist because the bug they prevent is invisible in use: a link that
 * resolves to the right page on the machine that made it and the wrong one
 * everywhere else. The parsing is where that goes wrong, so it is what is
 * asserted -- round trips, encodings, and the shapes a person types by hand.
 */

import { describe, expect, it } from "vitest";

import { readRoute, routeToHash } from "./route";

describe("reading an address", () => {
  it("reads the view and the model", () => {
    expect(readRoute("#/dashboard?model=StoreSales")).toEqual({
      view: "dashboard",
      model: "StoreSales",
    });
  });

  it("reads a view with no model named", () => {
    expect(readRoute("#/requirements")).toEqual({ view: "requirements" });
  });

  it("decodes a model name with a space in it", () => {
    // `Sales & Returns` is a real shape: the sample models are named by their
    // files, and a file name may contain anything a filesystem allows.
    expect(readRoute("#/model?model=Sales%20%26%20Returns").model).toBe(
      "Sales & Returns",
    );
  });

  it("does not turn a plus into a space", () => {
    // `URLSearchParams` would. A model called `A+B` is not a model called
    // `A B`, and pointing the interface at the wrong one is exactly the class
    // of error this whole project is against.
    expect(readRoute("#/overview?model=A%2BB").model).toBe("A+B");
  });

  it("tolerates the forms a person types by hand", () => {
    expect(readRoute("#dashboard")).toEqual({ view: "dashboard" });
    expect(readRoute("#/dashboard")).toEqual({ view: "dashboard" });
    expect(readRoute("")).toEqual({});
    expect(readRoute("#")).toEqual({});
    expect(readRoute("#/")).toEqual({});
  });

  it("ignores a query it does not recognise", () => {
    expect(readRoute("#/dashboard?theme=dark&model=X")).toEqual({
      view: "dashboard",
      model: "X",
    });
  });
});

describe("writing an address", () => {
  it("round-trips every part", () => {
    for (const route of [
      { view: "dashboard", model: "StoreSales" },
      { view: "model", model: "Sales & Returns" },
      { view: "overview", model: "A+B" },
      { view: "review", model: "one two three" },
    ]) {
      expect(readRoute(routeToHash(route))).toEqual(route);
    }
  });

  it("names the model even when it is the only one loaded", () => {
    // Deliberate. A link that resolves to whichever model a server defaults to
    // means different things on different servers, and a figure without its
    // context is the thing this tool exists to stop.
    expect(routeToHash({ view: "dashboard", model: "Only" })).toBe(
      "#/dashboard?model=Only",
    );
  });

  it("leaves the model out only when there is not one yet", () => {
    expect(routeToHash({ view: "overview", model: "" })).toBe("#/overview");
  });
});
