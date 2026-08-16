/**
 * What each way this can fail should actually say to a person.
 *
 * Before this, every failure rendered the same red box containing whatever
 * string the server happened to return. A dead server, an expired session, a
 * mistyped measure name and an exhausted model quota are four unrelated
 * situations with four different fixes, and presenting them identically leaves
 * the reader to work out which one they are in.
 *
 * Each entry answers three questions in the order a person asks them:
 *
 *   title   what happened, in their terms, not the protocol's
 *   detail  why -- only when the reason is not already obvious from the title
 *   action  what to do next, and whether this interface can do it for them
 *
 * `tone` is not decoration. `bad` means something broke; `review` means the
 * request was understood and refused. Colouring a mistyped name the same red as
 * a crashed server is how people learn to ignore red.
 *
 * The server's own message is preserved rather than replaced wherever it is
 * specific -- "no measure named X" is better copy than anything generic, and
 * this module's job is to frame it, not to talk over it.
 */

export type FailureTone = "bad" | "review";

export interface Presented {
  title: string;
  detail?: string;
  /** Label for the recovery control, when retrying is meaningful. */
  retryLabel?: string;
  /** Shown as a fixed instruction when the fix is not a retry. */
  instruction?: string;
  tone: FailureTone;
  /** Close matches the server offered for a name it did not recognise. */
  suggestions?: string[];
}

export interface FailureInput {
  status: number;
  message: string;
  didYouMean?: string[];
  /** What the view was trying to load, e.g. "the requirements". */
  what?: string;
}

/**
 * Map a failed request onto something worth reading.
 *
 * Ordered most-specific first. The final branch is deliberately not a
 * catch-all apology: an unrecognised status still names the status, because a
 * reader who reports "it said 418" can be helped and one who reports "it said
 * something went wrong" cannot.
 */
export function present({ status, message, didYouMean, what }: FailureInput): Presented {
  // Status 0 is this client's own signal that `fetch` rejected -- the server
  // was never reached. By far the most common failure in local use, and the
  // only one where the fix is outside the browser entirely.
  if (status === 0) {
    return {
      title: "Cannot reach the Concordance server",
      detail:
        "The page loaded, but the server behind it is not answering. It has " +
        "usually been stopped, or restarted on a different port.",
      instruction: "concordance serve <model>",
      retryLabel: "Try again",
      tone: "bad",
    };
  }

  if (status === 401) {
    return {
      title: "Your session has ended",
      detail:
        "Sign in again to continue. Nothing you have reviewed is lost — " +
        "decisions are written as they are made, not when you leave.",
      tone: "review",
    };
  }

  // A name the model does not contain. The server computes close matches, and
  // offering them is far more useful than restating that the name was wrong.
  if (status === 404) {
    return {
      title: "Not found in this model",
      detail: message,
      suggestions: didYouMean,
      tone: "review",
    };
  }

  // The chat's provider failed: quota exhausted, key rejected, host down. The
  // model is still fully readable, and saying so prevents someone concluding
  // the whole tool is broken because the chat is.
  if (status === 502) {
    return {
      title: "The language model could not be reached",
      detail:
        message +
        " Every other view still works — they read the extracted model " +
        "directly and never call a language model.",
      retryLabel: "Ask again",
      tone: "review",
    };
  }

  // 501 means an optional flag was not passed. Views that can render the fuller
  // NotConfigured screen do so and never reach here; this is the fallback for
  // the ones that cannot.
  if (status === 501) {
    return {
      title: "Not available on this server",
      detail: message,
      tone: "review",
    };
  }

  if (status === 400) {
    return {
      title: "The server could not accept that request",
      detail: message,
      tone: "review",
    };
  }

  if (status === 413) {
    return {
      title: "That request was too large to send",
      detail: message,
      tone: "review",
    };
  }

  if (status >= 500) {
    return {
      title: "The server failed while answering",
      detail:
        message +
        " This is a defect rather than a limit — the terminal running " +
        "`concordance serve` will have the traceback.",
      retryLabel: "Try again",
      tone: "bad",
    };
  }

  return {
    title: what ? `Could not load ${what}` : "The request did not succeed",
    detail: `${message} (HTTP ${status})`,
    retryLabel: "Try again",
    tone: "bad",
  };
}
