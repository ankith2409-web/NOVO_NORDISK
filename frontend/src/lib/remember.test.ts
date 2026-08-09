/**
 * Remembering where the user was, without letting it govern where they are.
 *
 * The failure this guards is specific: the same browser gets pointed at
 * different servers holding different models with different flags, so a
 * remembered choice that no longer exists must fall back rather than be
 * trusted. Restoring a model name this server never loaded would send every
 * request at something that is not there, and restoring a view whose flag is
 * now absent would open a page that cannot answer.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { recall, recallOneOf, remember } from "./remember";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("round trip", () => {
  it("returns what was stored", () => {
    remember("view", "drift");
    expect(recall("view")).toBe("drift");
  });

  it("returns null for something never stored", () => {
    expect(recall("view")).toBeNull();
  });

  it("namespaces its keys so it cannot collide with anything else on the origin", () => {
    remember("view", "drift");
    expect(localStorage.getItem("view")).toBeNull();
    expect(localStorage.getItem("concordance:view")).toBe("drift");
  });
});

describe("validating against what exists now", () => {
  it("returns a remembered value that is still on offer", () => {
    remember("model", "QualityControl");
    expect(recallOneOf("model", ["QualityControl", "Sales"])).toBe("QualityControl");
  });

  it("refuses a remembered value this server does not have", () => {
    // The whole point. Returning it would point every request at a model that
    // is not loaded, and the interface would fill with 404s that look like the
    // server is broken rather than like a stale preference.
    remember("model", "SomeOtherServersModel");
    expect(recallOneOf("model", ["QualityControl", "Sales"])).toBeNull();
  });

  it("refuses when nothing at all is on offer", () => {
    remember("model", "QualityControl");
    expect(recallOneOf("model", [])).toBeNull();
  });

  it("compares exactly rather than loosely", () => {
    // Model names are case-sensitive identifiers on the server; accepting a
    // near-match here would send a request that 404s.
    remember("model", "qualitycontrol");
    expect(recallOneOf("model", ["QualityControl"])).toBeNull();
  });
});

describe("a browser that refuses storage", () => {
  it("does not throw when writing is refused", () => {
    // Private-browsing modes and a full quota both throw on setItem. Losing
    // the memory of which tab was open is not worth failing a render over.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });
    expect(() => remember("view", "drift")).not.toThrow();
  });

  it("reads as absent when reading is refused", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("SecurityError");
    });
    expect(recall("view")).toBeNull();
    expect(recallOneOf("view", ["overview"])).toBeNull();
  });
});
