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

export interface ReviewPayload {
  model: string;
  count: number;
  pending: Requirement[];
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
}

export interface GraphPayload {
  model: string;
  nodes: { id: string; kind: string; [key: string]: unknown }[];
  edges: { source: string; target: string; kind: string; [key: string]: unknown }[];
  stats: { nodes: number; edges: number; unresolved_references: number };
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

function withModel(params?: Record<string, string>): Record<string, string> | undefined {
  // Never in snapshot mode: the snapshot is one model captured by URL, and an
  // extra parameter would simply miss every key in it.
  if (SNAPSHOT_MODE || !activeModel) return params;
  return { ...(params ?? {}), model: activeModel };
}

async function get<T>(path: string, params?: Record<string, string>): Promise<Result<T>> {
  if (SNAPSHOT_MODE) return fromSnapshot<T>(path, params);
  params = withModel(params);
  const query = params ? `?${new URLSearchParams(params)}` : "";
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

export const api = {
  /** Point every subsequent request at `name`; "" restores the server default. */
  use: (name: string) => {
    activeModel = name;
  },
  active: () => activeModel,

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
            },
          ],
        },
      };
    }
    return get<ModelsPayload>("/models");
  },

  overview: () => get<Overview>("/overview"),
  graph: () => get<GraphPayload>("/graph"),
  tables: () => get<{ tables: TableSummary[] }>("/tables"),
  measures: () => get<{ measures: MeasureSummary[] }>("/measures"),
  measure: (name: string) => get<MeasureDetail>("/measure", { name }),
  impact: (name: string) => get<{ object: string; would_be_affected: string[] }>("/impact", { name }),
  requirements: (kind: "business" | "functional") =>
    get<RequirementsPayload>("/requirements", { kind }),
  review: () => get<ReviewPayload>("/review"),
  drift: () => get<DriftPayload>("/drift"),
  reconcile: () => get<ReconcilePayload>("/reconcile"),

  async ask(question: string): Promise<
    Result<{
      answer: string;
      grounded: boolean;
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
