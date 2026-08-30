/**
 * The client for the Python API.
 *
 * Types here mirror `concordance/web/api.py` payloads. They are hand-written
 * rather than generated because the API is small and stable, and a generator
 * would be one more thing to keep running on demo day.
 *
 * Errors are values, not exceptions. The server answers a failed request with a
 * status and a message written for a person to read -- `501` naming the flag
 * that would enable a feature, `404` with a spelling suggestion -- and throwing
 * that away in favour of a generic "something went wrong" would discard the most
 * useful thing in the response.
 */

export interface CoverageGap {
  feature: string;
  count: number;
}

export interface Overview {
  model: string;
  source_format: string;
  tables: number;
  user_tables: number;
  columns: number;
  calculated_columns: number;
  measures: number;
  relationships: number;
  hierarchies: number;
  user_hierarchies: number;
  unresolved_references: { from: string; to: string; reason: string }[];
  not_extracted: CoverageGap[];
  capabilities: { drift: boolean; reconcile: boolean };
}

export interface LoadedModel {
  name: string;
  source_format: string;
  measures: number;
  tables: number;
  capabilities: { drift: boolean; reconcile: boolean };
  /** True for a model this browser uploaded rather than one the server holds. */
  uploaded: boolean;
}

/** What the server answers when it has read an uploaded file. */
export interface Uploaded {
  name: string;
  source_format: string;
  measures: number;
  tables: number;
  relationships: number;
  /** The model this upload pushed out of the per-session allowance, if any. */
  replaced: string;
  held: number;
}

export interface ModelsPayload {
  default: string;
  models: LoadedModel[];
}

export interface Evidence {
  node_id: string;
  fingerprint: string;
  short_fingerprint: string;
  detail: string;
}

export type Confidence = "high" | "medium" | "low";

export interface Requirement {
  id: string;
  kind: "business" | "functional";
  category: string;
  statement: string;
  rationale: string;
  confidence: Confidence;
  needs_review: boolean;
  evidence: Evidence[];
}

export interface RequirementsPayload {
  kind: string;
  model: string;
  counts: { total: number; high: number; medium: number; low: number };
  requirements: Requirement[];
}

export type DecisionStatus = "open" | "decided" | "stale";

export interface Standing {
  status: DecisionStatus;
  verdict: string;
  note?: string;
  author_claimed?: string;
  /** True when the server resolved the name from the reviewer's own token
   *  rather than taking it from the request body. Always shown alongside the
   *  name: an audit trail that mixes facts and claims without saying which is
   *  which forces every entry to be treated as a claim. */
  author_verified?: boolean;
  at?: string;
  history: {
    verdict: string;
    note: string;
    author_claimed: string;
    author_verified?: boolean;
    at: string;
  }[];
}

export interface ReviewPayload {
  model: string;
  count: number;
  open: number;
  decided: number;
  stale: number;
  /** False when the server was started without a decision log, in which case
   *  the queue is read-only and the interface says so rather than showing
   *  controls that would store nothing. */
  can_decide: boolean;
  /** True when this model was uploaded, which is the *other* reason
   *  `can_decide` can be false -- and a different thing to tell the reader. */
  uploaded: boolean;
  pending: (Requirement & { standing: Standing })[];
}

export interface MeasureDetail {
  name: string;
  table: string;
  expression: string;
  canonical: string;
  description: string;
  display_folder: string;
  behaviours: { label: string; meaning: string }[];
  depends_on: string[];
  used_by: string[];
  fingerprint: string;
  fingerprint_full: string;
}

export interface MeasureSummary {
  name: string;
  table: string;
  folder: string;
}

export interface TableSummary {
  name: string;
  columns: number;
  measures: number;
  kind: "data" | "system" | "measure-only";
}

export type Verdict = "consistent" | "divergent" | "review";

export interface MetricDefinition {
  platform: string;
  language: string;
  expression: string;
  tables: string[];
  columns: string[];
  aggregations: string[];
  resolved_through: string[];
}

export interface Comparison {
  metric: string;
  verdict: Verdict;
  needs_attention: boolean;
  definitions: MetricDefinition[];
  differences: { aspect: string; detail: string }[];
}

export interface Summary {
  text: string | null;
  /** Set when no summary could be produced -- no key configured, quota,
   *  network -- so the interface can say why rather than showing nothing. */
  error?: string;
  provider?: string;
  disclaimer?: string;
}

/** One measure, as it is written in the model and as it would be written in SQL. */
export interface DatasetMeasure {
  measure: string;
  table: string;
  folder: string;
  description: string;
  dax: string;
  /** Empty when `status` is not "exact"; never a partial query. */
  sql: string;
  status: "exact" | "blocked" | "unsupported";
  reason: string;
  blocked_by: string;
  reads_tables: string[];
}

export interface DatasetTable {
  name: string;
  columns: number;
  measures: number;
  /** A container holding only measures: a grouping, not a data entity. */
  measures_only: boolean;
}

export interface DatasetJoin {
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  cardinality: string;
  cross_filter: string;
  active: boolean;
  /** The same join the generated queries use, in the chosen dialect. */
  sql: string;
}

export interface DatasetPayload {
  model: string;
  grain: string[];
  dialect: string;
  tables: DatasetTable[];
  joins: DatasetJoin[];
  grain_options: { value: string; table: string; column: string }[];
  dialects: string[];
  counts: { measures: number; translated: number; blocked: number };
  measures: DatasetMeasure[];
}

export interface ReconcilePayload {
  model: string;
  warehouse: string;
  counts: {
    shared_metrics: number;
    divergent: number;
    review: number;
    consistent: number;
  };
  comparisons: Comparison[];
  unique_to_platform: Record<string, string[]>;
  possible_pairings: {
    left: string;
    left_platform: string;
    right: string;
    right_platform: string;
    similarity: number;
    /** How the pair was found. "structure" catches ones no name score could. */
    basis: "name" | "structure" | "both";
    /** Names are close and the two read nothing in common. */
    contradicted: boolean;
    /** What the two actually read, as one line a reviewer can act on. */
    evidence: string;
  }[];
  coverage_gaps: { feature: string; count: number; reason: string }[];
  /** Only present when requested with `?summary=true`. */
  summary?: Summary;
}

export interface DriftChange {
  node_id: string;
  kind: "added" | "removed" | "changed" | "renamed";
  object_kind: string;
  summary: string;
  /** False only for a rename — its logic is provably unchanged. */
  is_semantic: boolean;
  before: { fingerprint: string; short_fingerprint: string; detail: string } | null;
  after: { fingerprint: string; short_fingerprint: string; detail: string } | null;
}

export interface DriftPayload {
  before: string;
  after: string;
  model: string;
  has_drift: boolean;
  counts: {
    added: number;
    removed: number;
    changed: number;
    renamed: number;
    unchanged: number;
    affected_requirements: number;
    needing_revalidation: number;
    reference_updates_only: number;
  };
  changes: DriftChange[];
  affected_requirements: {
    requirement: Requirement;
    because: string[];
    needs_revalidation: boolean;
  }[];
  /** Only present when requested with `?summary=true`. */
  summary?: Summary;
}

export interface GraphPayload {
  model: string;
  nodes: { id: string; kind: string; [key: string]: unknown }[];
  edges: { source: string; target: string; kind: string; [key: string]: unknown }[];
  stats: { nodes: number; edges: number; unresolved_references: number };
}

export interface WhoAmI {
  person: string;
  identified: boolean;
  /** True when this server was started with any way of identifying people. */
  identifies_reviewers: boolean;
  /** True when identity comes from Auth0 rather than a personal token. */
  auth0: boolean;
}

export interface ApiFailure {
  ok: false;
  status: number;
  /** Written for a person: the API never returns a bare code. */
  message: string;
  /** Present when the server could suggest a spelling. */
  didYouMean?: string[];
}

export type Result<T> = ({ ok: true } & { data: T }) | ApiFailure;

/**
 * A build with `VITE_SNAPSHOT=1` answers from a captured run instead of a live
 * server, so the interface can be shared as a single file with no backend.
 *
 * Gated at build time rather than by falling back when a request fails. A
 * silent fallback would hide a dead server behind stale data, which is exactly
 * the confidently-wrong behaviour this project exists to avoid -- the normal
 * build has no snapshot in it at all and still says plainly when it cannot
 * reach the API.
 */
export const SNAPSHOT_MODE = import.meta.env.VITE_SNAPSHOT === "1";

let captured: Record<string, unknown> | null = null;

async function fromSnapshot<T>(
  path: string,
  params?: Record<string, string>,
): Promise<Result<T>> {
  if (!captured) {
    captured = (await import("./snapshot.json")).default as Record<string, unknown>;
  }
  const query = params ? `?${new URLSearchParams(params)}` : "";

  // Per-object lookups were captured by name rather than by URL.
  if (path === "/measure" && params?.name) {
    const found = (captured._measures as Record<string, T>)[params.name];
    if (found) return { ok: true, data: found };
  }
  if (path === "/impact" && params?.name) {
    const found = (captured._impact as Record<string, T>)[params.name];
    if (found) return { ok: true, data: found };
  }

  const hit = captured[path + query] as T | undefined;
  if (hit) return { ok: true, data: hit };
  return {
    ok: false,
    status: 501,
    message: "Not part of this snapshot. Run the server for the live model.",
  };
}

/**
 * The model every request is about.
 *
 * Held here rather than threaded through each view on purpose. A server can
 * hold several models, and a switcher that moves five views while the sixth
 * quietly keeps answering for the old one is a bug that reads as a rendering
 * glitch. Routing it in one place means no call site can forget.
 *
 * Empty means "whatever the server made default", which is also the only
 * possibility when one model is loaded -- the common case pays nothing.
 */
let activeModel = "";

/** Guards the one-shot reload on 401. See the handler in `get`. */
let reloadingForAuth = false;

function withModel(params?: Record<string, string>): Record<string, string> | undefined {
  // Never in snapshot mode: the snapshot is one model captured by URL, and an
  // extra parameter would simply miss every key in it.
  if (SNAPSHOT_MODE || !activeModel) return params;
  return { ...(params ?? {}), model: activeModel };
}

/**
 * `params` may be a plain object or `URLSearchParams`.
 *
 * The second form exists because a grain is a list: `?grain=A&grain=B` cannot
 * be expressed as an object, and joining the values with a separator would
 * break on the first column name that contains it.
 */
async function get<T>(
  path: string,
  params?: Record<string, string> | URLSearchParams,
): Promise<Result<T>> {
  const repeated = params instanceof URLSearchParams;
  if (SNAPSHOT_MODE) {
    // A snapshot is keyed by URL, so it is looked up with the flattened form.
    const flat: Record<string, string> = {};
    if (repeated) params.forEach((value, key) => (flat[key] = value));
    return fromSnapshot<T>(path, repeated ? flat : params);
  }

  let query = "";
  if (repeated) {
    const merged = new URLSearchParams(params);
    if (activeModel) merged.set("model", activeModel);
    query = `?${merged}`;
  } else {
    const withIt = withModel(params);
    query = withIt ? `?${new URLSearchParams(withIt)}` : "";
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}${query}`, { credentials: "same-origin" });
  } catch {
    // A dead server is the single most likely failure in local use, and it
    // deserves the instruction that fixes it rather than "Failed to fetch".
    return {
      ok: false,
      status: 0,
      message: "Cannot reach the Concordance server. Start it with `concordance serve <model>`.",
    };
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* fall through to the status-only message below */
  }

  if (!response.ok) {
    // A session that ended mid-use is not an error to render inside a view --
    // "the server answered 401" in the middle of the Drift report tells the
    // reader nothing they can act on. Reloading lands on the sign-in page the
    // server now serves for a document request, which is the only thing that
    // can actually resolve it.
    if (response.status === 401) {
      // Once per page life, and never twice. A reload that lands on a page
      // which 401s again would spin forever -- which is exactly what happened
      // the first time this shipped, because the server was not persisting the
      // credential it had just accepted. The bug is fixed; this guard stays,
      // because "retry forever" is the wrong shape for a failure regardless of
      // what causes it.
      if (!reloadingForAuth) {
        reloadingForAuth = true;
        window.location.reload();
      }
      return {
        ok: false,
        status: 401,
        message: "Your session has ended. Sign in again to continue.",
      };
    }
    const payload = (body ?? {}) as { error?: string; did_you_mean?: string[] };
    return {
      ok: false,
      status: response.status,
      message: payload.error ?? `The server answered ${response.status}.`,
      didYouMean: payload.did_you_mean,
    };
  }
  return { ok: true, data: body as T };
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<Result<T>> {
  if (SNAPSHOT_MODE) {
    return {
      ok: false,
      status: 501,
      message:
        "A snapshot is a captured reading, with nowhere to write a decision back to. " +
        "Run `concordance serve <model> --decisions <path>` to answer the queue.",
    };
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}${activeModel ? `?model=${encodeURIComponent(activeModel)}` : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
  } catch {
    return {
      ok: false,
      status: 0,
      message: "Cannot reach the Concordance server.",
    };
  }
  const parsed = await response.json().catch(() => null);
  if (!response.ok) {
    const payload = (parsed ?? {}) as { error?: string };
    return {
      ok: false,
      status: response.status,
      message: payload.error ?? `The server answered ${response.status}.`,
    };
  }
  return { ok: true, data: parsed as T };
}

export const api = {
  /** Point every subsequent request at `name`; "" restores the server default. */
  use: (name: string) => {
    activeModel = name;
  },
  active: () => activeModel,

  /**
   * Where to download the generated document from.
   *
   * A URL rather than a fetch: the browser's own download machinery handles
   * the filename, the progress and the save dialog, and fetching the bytes
   * into memory only to hand them back to a synthetic anchor would throw all
   * of that away for a file that can run to megabytes.
   *
   * Not available from a snapshot, which has no server to render one.
   */
  documentUrl: (
    kind: "business" | "functional",
    format: "md" | "docx",
    sql?: { grain: string[]; dialect: string },
  ): string => {
    const params = new URLSearchParams({ kind, format });
    if (activeModel) params.set("model", activeModel);
    if (sql) {
      // `sql` alone is what asks for the section; the grain may legitimately be
      // empty, which means the whole-model figure rather than "no SQL".
      params.set("sql", "1");
      for (const g of sql.grain) params.append("grain", g);
      params.set("dialect", sql.dialect);
    }
    return `/api/document?${params}`;
  },

  models: async (): Promise<Result<ModelsPayload>> => {
    if (SNAPSHOT_MODE) {
      // A snapshot is one captured model, so the switcher has nothing to offer
      // and should not be drawn at all.
      const overview = await fromSnapshot<Overview>("/overview");
      if (!overview.ok) return overview;
      const { model, source_format, measures, user_tables, capabilities } = overview.data;
      return {
        ok: true,
        data: {
          default: model,
          models: [
            {
              name: model,
              source_format,
              measures,
              tables: user_tables,
              capabilities,
              uploaded: false,
            },
          ],
        },
      };
    }
    return get<ModelsPayload>("/models");
  },

  /**
   * Send a Power BI file to be read for this browser session.
   *
   * The file is the whole body. A multipart form would be the conventional
   * choice and is the wrong one here: it would mean the server parsing an
   * envelope to recover exactly the bytes `fetch` already sends on its own,
   * and the only other field -- the name -- fits in the query string. Fewer
   * moving parts on the side of the wire that has to distrust the input.
   *
   * `onProgress` is fed by XMLHttpRequest rather than fetch, which still
   * cannot report upload progress. A 60MB .pbix over a conference wifi is
   * tens of seconds of nothing, and a spinner that cannot say how far along it
   * is reads as a hang.
   */
  upload: (
    file: File,
    onProgress?: (fraction: number) => void,
  ): Promise<Result<Uploaded>> => {
    if (SNAPSHOT_MODE) {
      return Promise.resolve({
        ok: false,
        status: 501,
        message:
          "A snapshot has no server to read a file with. Run `concordance serve <model>` " +
          "and open it there to upload your own.",
      });
    }
    return new Promise((resolve) => {
      const request = new XMLHttpRequest();
      request.open("POST", `/api/upload?filename=${encodeURIComponent(file.name)}`);
      request.withCredentials = true;
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress?.(event.loaded / event.total);
      });
      request.addEventListener("load", () => {
        let parsed: { error?: string; hint?: string } & Partial<Uploaded> = {};
        try {
          parsed = JSON.parse(request.responseText);
        } catch {
          /* handled by the status check below */
        }
        if (request.status >= 200 && request.status < 300) {
          resolve({ ok: true, data: parsed as Uploaded });
          return;
        }
        resolve({
          ok: false,
          status: request.status,
          // The hint is the half that says what to do next, so it is joined on
          // rather than dropped -- the alternative is "no .tmdl files inside
          // it" with no indication that zipping the folder is the answer.
          message:
            [parsed.error, parsed.hint]
              .filter(Boolean)
              // Ended before joining: the server's `problem` is a fragment
              // ("no .tmdl files inside it") and the hint is a sentence, so
              // concatenating them raw runs the two together mid-thought.
              .map((part) => (/[.!?]$/.test(part!) ? part : `${part}.`))
              .join(" ") || `The server answered ${request.status}.`,
        });
      });
      request.addEventListener("error", () =>
        resolve({ ok: false, status: 0, message: "Cannot reach the Concordance server." }),
      );
      request.addEventListener("abort", () =>
        resolve({ ok: false, status: 0, message: "Upload cancelled." }),
      );
      request.send(file);
    });
  },

  /** Drop one uploaded model. Only ever this browser's own. */
  forget: (model: string) => post<{ forgotten: string }>("/forget", { model }),

  whoami: () => get<WhoAmI>("/whoami"),
  overview: () => get<Overview>("/overview"),
  graph: () => get<GraphPayload>("/graph"),
  tables: () => get<{ tables: TableSummary[] }>("/tables"),
  measures: () => get<{ measures: MeasureSummary[] }>("/measures"),
  measure: (name: string) => get<MeasureDetail>("/measure", { name }),
  impact: (name: string) => get<{ object: string; would_be_affected: string[] }>("/impact", { name }),
  requirements: (kind: "business" | "functional") =>
    get<RequirementsPayload>("/requirements", { kind }),
  review: () => get<ReviewPayload>("/review"),

  /** Record a decision. The server derives what it was made about; a caller
   *  that could state that could approve one thing while recording another. */
  decide: (requirement_id: string, verdict: string, note = "", author = "") =>
    post<{ requirement_id: string; standing: Standing }>("/decide", {
      requirement_id,
      verdict,
      note,
      author,
    }),
  drift: () => get<DriftPayload>("/drift"),
  reconcile: () => get<ReconcilePayload>("/reconcile"),

  /**
   * Every measure at one grain, with its SQL.
   *
   * `grain` repeats rather than joining with a separator, because a column
   * name may legally contain whichever separator would have been chosen.
   */
  dataset: (grain: string[], dialect: string) => {
    const params = new URLSearchParams();
    for (const g of grain) params.append("grain", g);
    if (dialect) params.set("dialect", dialect);
    return get<DatasetPayload>("/dataset", params);
  },
  // Separate calls, not a flag on the calls above: a summary is a second,
  // slower request the caller opts into deliberately, on data it has already
  // rendered -- not something every drift/reconcile load should wait on.
  driftSummary: () => get<DriftPayload>("/drift", { summary: "true" }),
  reconcileSummary: () => get<ReconcilePayload>("/reconcile", { summary: "true" }),

  async ask(question: string): Promise<
    Result<{
      answer: string;
      grounded: boolean;
      /** A greeting or "what can you do", answered without a language model.
       *  It asserts nothing about the model, so it is not an ungrounded claim. */
      conversational?: boolean;
      tool_calls: { name: string; arguments: Record<string, unknown> }[];
      rejected_calls: string[];
    }>
  > {
    if (SNAPSHOT_MODE) {
      return {
        ok: false,
        status: 501,
        message:
          "The copilot needs a live model — it answers by calling tools against the graph, " +
          "so there is nothing to snapshot. Run `concordance serve <model>` to use it.",
      };
    }

    let response: Response;
    try {
      response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ question, model: activeModel }),
      });
    } catch {
      return {
        ok: false,
        status: 0,
        message: "Cannot reach the Concordance server.",
      };
    }
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const payload = (body ?? {}) as { error?: string };
      return {
        ok: false,
        status: response.status,
        message: payload.error ?? `The server answered ${response.status}.`,
      };
    }
    return { ok: true, data: body };
  },
};
