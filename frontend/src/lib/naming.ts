/**
 * What the features are called on screen.
 *
 * Gathered here because the names are the part most likely to change: they are
 * the first thing a new reader meets and the last thing anyone agrees on.
 * Scattered across six views they would drift apart -- a tab saying one thing
 * and the page it opens saying another -- and changing them would mean finding
 * every one.
 *
 * The internal ids do not change with them. A tab id, an API route and a CLI
 * flag are contracts with the deployment and with anyone's saved links; only
 * the words a person reads are here.
 *
 * The names themselves answer a specific complaint. "Reconcile" and "Drift"
 * are the vocabulary of the people who built this, not of the people who read
 * it -- a reviewer asked what both meant, and having to explain a tab is a
 * sign the tab is misnamed. Each label below says what the page does, in words
 * that need no gloss.
 */

export const FEATURE = {
  /** Was: "Drift". What moved between two versions of a model. */
  drift: {
    tab: "What changed",
    heading: "What changed",
    /** For a sentence: "... so <subject> cannot run". */
    subject: "the comparison between versions",
  },
  /** Was: "Reconcile". Whether the warehouse agrees with Power BI. */
  reconcile: {
    // Shorter than the heading on purpose: the rail wraps past about twelve
    // characters, and a wrapped label leaves the "off" badge stranded on its
    // own line. The page it opens carries the fuller name.
    tab: "Warehouse",
    heading: "Warehouse check",
    subject: "the warehouse check",
  },
  /** Was: "Review". The queue of statements a person still has to settle. */
  review: {
    tab: "To confirm",
    heading: "Awaiting confirmation",
    subject: "the confirmation queue",
  },
  /** The consolidated dataset page. Named for what it holds. */
  dataset: {
    tab: "Dataset + SQL",
    heading: "The whole dataset",
    subject: "the dataset",
  },
} as const;
