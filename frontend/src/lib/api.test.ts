/**
 * Routing every request at the model the interface is actually showing.
 *
 * The active model lives in this module rather than being threaded through
 * each view, precisely so no call site can forget it -- and that decision is
 * only worth anything if it holds for every call. A view that quietly kept
 * asking the default while the header said otherwise would render a confident,
 * fully-formed answer about the wrong model, which is the failure mode this
 * whole project exists to avoid.
 *
 * Errors are values here, not exceptions, so they are tested as values: a dead
 * server has to produce the instruction that fixes it rather than "Failed to
 * fetch", and a 501 has to survive intact because the views branch on it to
 * explain a missing flag instead of showing a red error.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

function respond(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response);
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(() => respond({ model: "QualityControl" }));
  vi.stubGlobal("fetch", fetchMock);
  api.use("");
});

afterEach(() => {
  vi.unstubAllGlobals();
  api.use("");
});

const lastUrl = () => String(fetchMock.mock.calls.at(-1)?.[0]);

describe("which model a request is about", () => {
  it("names no model until one is chosen, so the server picks its default", () => {
    // The common case is one model loaded, and it should pay nothing for the
    // existence of the multi-model path.
    void api.overview();
    expect(lastUrl()).toBe("/api/overview");
  });

  it("carries the active model once one is chosen", () => {
    api.use("ClinicalTrialSafety");
    void api.overview();
    expect(lastUrl()).toContain("model=ClinicalTrialSafety");
  });

  it("carries it on every read route, not just the one that switched", () => {
    // A switcher that moves five views and leaves the sixth on the old model
    // is a bug that reads as a rendering glitch.
    api.use("ClinicalTrialSafety");
    const calls: [string, () => unknown][] = [
      ["overview", () => api.overview()],
      ["graph", () => api.graph()],
      ["tables", () => api.tables()],
      ["measures", () => api.measures()],
      ["review", () => api.review()],
      ["drift", () => api.drift()],
      ["requirements", () => api.requirements("business")],
      ["measure", () => api.measure("OOS Rate")],
      ["impact", () => api.impact("OOS Rate")],
    ];
    for (const [name, call] of calls) {
      call();
      expect(lastUrl(), `${name} dropped the model`).toContain(
        "model=ClinicalTrialSafety",
      );
    }
  });

  it("keeps a route's own parameters alongside the model", () => {
    api.use("ClinicalTrialSafety");
    void api.requirements("functional");
    expect(lastUrl()).toContain("kind=functional");
    expect(lastUrl()).toContain("model=ClinicalTrialSafety");
  });

  it("encodes a name that needs it", () => {
    api.use("Clinical Trial Safety");
    void api.overview();
    expect(lastUrl()).toContain("model=Clinical+Trial+Safety");
  });

  it("returns to the server default when the choice is cleared", () => {
    api.use("ClinicalTrialSafety");
    api.use("");
    void api.overview();
    expect(lastUrl()).toBe("/api/overview");
  });

  it("sends the model with a chat question too", () => {
    // The chat reads tables and DAX out of a graph. Asking about one model and
    // having it answer from another is the same silent wrongness as the reads.
    api.use("ClinicalTrialSafety");
    void api.ask("what does OOS Rate measure?");
    const body = JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body));
    expect(body.model).toBe("ClinicalTrialSafety");
    expect(body.question).toBe("what does OOS Rate measure?");
  });
});

describe("failures, as values rather than exceptions", () => {
  it("turns an unreachable server into the instruction that fixes it", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const result = await api.overview();
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.status).toBe(0);
    expect(result.message).toContain("concordance serve");
  });

  it("keeps the server's own message rather than replacing it", async () => {
    // A 501 naming the flag that would enable a feature is the most useful
    // thing in the response, and the views branch on it.
    fetchMock.mockReturnValueOnce(
      respond({ error: "no comparison model was configured" }, 501),
    );
    const result = await api.drift();
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.status).toBe(501);
    expect(result.message).toContain("no comparison model was configured");
  });

  it("passes through a spelling suggestion when the server offers one", async () => {
    fetchMock.mockReturnValueOnce(
      respond({ error: "no such measure", did_you_mean: ["OOS Rate"] }, 404),
    );
    const result = await api.measure("OOS Rat");
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.didYouMean).toEqual(["OOS Rate"]);
  });

  it("still reports a status when the body is not JSON at all", async () => {
    fetchMock.mockReturnValueOnce(
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.reject(new SyntaxError("not json")),
      } as unknown as Response),
    );
    const result = await api.overview();
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.message).toContain("500");
  });
});

describe("credentials", () => {
  it("sends them, because the session cookie is what keeps a chat coherent", () => {
    void api.overview();
    expect(fetchMock.mock.calls.at(-1)?.[1]).toMatchObject({
      credentials: "same-origin",
    });
  });
});

describe("the document download URL", () => {
  it("carries the kind and the format the caller asked for", () => {
    api.use("");
    const url = api.documentUrl("functional", "docx");
    expect(url).toContain("kind=functional");
    expect(url).toContain("format=docx");
  });

  it("names the active model, so a switcher does not export the wrong one", () => {
    // The failure this prevents is the worst kind available here: a file that
    // downloads cleanly, opens cleanly, and describes a different model than
    // the one on screen.
    api.use("QualityControl");
    expect(api.documentUrl("business", "md")).toContain("model=QualityControl");
  });

  it("omits the model when the server default is in use", () => {
    api.use("");
    expect(api.documentUrl("business", "md")).not.toContain("model=");
  });

  it("escapes a model name rather than breaking the query string", () => {
    api.use("Sales & Returns");
    const url = api.documentUrl("business", "md");
    expect(url).toContain("model=Sales+%26+Returns");
    api.use("");
  });
});
