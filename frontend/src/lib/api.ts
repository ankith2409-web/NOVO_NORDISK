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
  }[];
  coverage_gaps: { feature: string; count: number; reason: string }[];
}

export interface DriftChange {
  node_id: string;
  kind: "added" | "removed" | "changed";
  object_kind: string;
  summary: string;
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
    unchanged: number;
    affected_requirements: number;
  };
  changes: DriftChange[];
  affected_requirements: { requirement: Requirement; because: string[] }[];
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

async function get<T>(path: string, params?: Record<string, string>): Promise<Result<T>> {
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
    let response: Response;
    try {
      response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ question }),
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
