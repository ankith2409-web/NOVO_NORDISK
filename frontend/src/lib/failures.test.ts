/**
 * That each failure is presented as the situation it actually is.
 *
 * The bug these guard against is not a crash. It is four unrelated problems --
 * a stopped server, an expired session, a mistyped name, an exhausted model
 * quota -- rendering as the same red box, leaving the reader to work out which
 * one they are in and what to do about it.
 *
 * Tone is asserted as carefully as wording. `bad` says something broke;
 * `review` says the request was understood and refused. Colouring a mistyped
 * measure name the same red as a crashed server is how people learn to ignore
 * red, and then miss the one that mattered.
 */
import { describe, expect, it } from "vitest";

import { present } from "./failures";

describe("a server that cannot be reached", () => {
  it("says so, and gives the command that fixes it", () => {
    const shown = present({ status: 0, message: "whatever fetch said" });
    expect(shown.title).toMatch(/cannot reach/i);
    expect(shown.instruction).toContain("concordance serve");
    expect(shown.tone).toBe("bad");
  });

  it("offers a retry, because the server may already be back", () => {
    expect(present({ status: 0, message: "" }).retryLabel).toBeTruthy();
  });
});

describe("an expired session", () => {
  it("reassures that recorded decisions survive it", () => {
    // The specific worry someone has at that moment. Left unsaid, a reviewer
    // who has just worked through a queue assumes they have lost the lot.
    const shown = present({ status: 401, message: "" });
    expect(shown.detail).toMatch(/not lost|nothing.*lost/i);
  });

  it("is not dressed as a breakage", () => {
    expect(present({ status: 401, message: "" }).tone).toBe("review");
  });

  it("offers no retry, because retrying is not what resolves it", () => {
    expect(present({ status: 401, message: "" }).retryLabel).toBeUndefined();
  });
});

describe("a name the model does not contain", () => {
  it("keeps the server's specific message rather than talking over it", () => {
    const shown = present({ status: 404, message: "no measure named 'OOS Rat'" });
    expect(shown.detail).toBe("no measure named 'OOS Rat'");
  });

  it("carries the close matches through to be offered", () => {
    const shown = present({
      status: 404,
      message: "no measure named 'OOS Rat'",
      didYouMean: ["OOS Rate"],
    });
    expect(shown.suggestions).toEqual(["OOS Rate"]);
  });

  it("is a refusal, not a breakage", () => {
    // The tool worked perfectly; the name was wrong. Red would be a lie.
    expect(present({ status: 404, message: "" }).tone).toBe("review");
  });
});

describe("a language model that could not be reached", () => {
  it("says the rest of the tool is unaffected", () => {
    // Without this, one exhausted quota reads as the whole product being down,
    // when in fact only the chat depends on a provider at all.
    const shown = present({ status: 502, message: "quota exhausted." });
    expect(shown.detail).toMatch(/every other view|still work/i);
  });

  it("offers to ask again rather than to reload", () => {
    expect(present({ status: 502, message: "" }).retryLabel).toMatch(/ask/i);
  });
});

describe("a server that failed while answering", () => {
  it("says where the traceback is, because this one is a defect", () => {
    const shown = present({ status: 500, message: "boom." });
    expect(shown.tone).toBe("bad");
    expect(shown.detail).toMatch(/traceback/i);
  });

  it("treats any 5xx the same way", () => {
    expect(present({ status: 503, message: "" }).tone).toBe("bad");
  });
});

describe("a status nothing knows about", () => {
  it("still names the number", () => {
    // A reader who reports "it said 418" can be helped. One who reports
    // "something went wrong" cannot.
    const shown = present({ status: 418, message: "I am a teapot" });
    expect(shown.detail).toContain("418");
  });

  it("names what was being loaded when it is known", () => {
    const shown = present({ status: 418, message: "", what: "the drift report" });
    expect(shown.title).toContain("the drift report");
  });
});

describe("across every case", () => {
  const STATUSES = [0, 400, 401, 404, 413, 500, 501, 502, 503, 418];

  it("always produces a title, and never an empty one", () => {
    for (const status of STATUSES) {
      const shown = present({ status, message: "x" });
      expect(shown.title.trim().length).toBeGreaterThan(0);
    }
  });

  it("never leaks a bare HTTP status into the title", () => {
    // The title is the line someone reads first; it should describe the
    // situation, not the protocol.
    for (const status of STATUSES) {
      const shown = present({ status, message: "x" });
      if (status !== 418) expect(shown.title).not.toMatch(/HTTP \d/);
    }
  });

  it("only ever uses the two tones the palette defines", () => {
    for (const status of STATUSES) {
      expect(["bad", "review"]).toContain(present({ status, message: "x" }).tone);
    }
  });
});
