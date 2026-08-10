# Concordance — Project Report

**Novo Nordisk GBS Hackathon 2026 — Problem Statement 5**
*An AI agent with a chatbot interface that automates BRD/FRD creation, extracts the Power
BI semantic layer, and documents tables, joins, KPIs, measures and DAX logic.*

---

## 1. Purpose of this report

This document explains, in plain terms, what the team built, why it was built this way,
what has been verified to work, and what has not been built yet. Every figure below was
produced by running the software, not estimated.

## 2. The problem being solved

Business and functional requirement documents (BRD/FRD) for a Power BI report are
normally typed by hand from interviews and screenshots. The document is accurate on the
day it is signed off and is never checked again. When someone later edits a measure — a
filter, a divide-by-zero fallback, a join — the report's numbers change, but the document
does not. Nobody notices until two reports disagree in a meeting, at which point tracing
the disagreement back to its cause is manual and slow.

The core idea behind Concordance is to stop *writing* the requirements document and start
*deriving* it — directly from the Power BI model — so that every sentence in it can be
traced back to the exact object it describes, and so the tool itself can tell you the
moment that link breaks.

## 3. What was built

### 3.1 Model extraction
Reads a Power BI model in either of its two real formats — a compiled `.pbix` file or a
TMDL project folder — into one internal representation covering tables, columns,
measures, relationships, hierarchies, and (added this cycle) which file or database each
table is actually loaded from.

### 3.2 Automated BRD/FRD generation
Produces business and functional requirement statements directly from the model's
structure. No language model writes these sentences — they come from deterministic rules,
so the same model always produces the same document, and every sentence carries the exact
object and a cryptographic fingerprint of the logic it was derived from.

### 3.3 Fingerprinting
Every measure's DAX is parsed with a hand-written lexer (not a regular expression) into a
canonical form, then hashed with SHA-256. This means reformatting a measure — different
spacing, casing, or comments — never registers as a change, while a real change to the
logic — a different filter, a different constant — always does. Demonstrated live on a
real measure:

```
QC Metrics[OOS Rate]
original      2c7cbf0aa669
reformatted   2c7cbf0aa669   same — no drift
altered       76fa30df61e0   DRIFT DETECTED
```

### 3.4 Drift detection
Compares two versions of the same model and reports what was added, removed, changed, or
renamed. A rename is *proven*, not guessed — an object whose fingerprint is unchanged
under a new name is known to be logically identical, so only requirements resting on
genuinely changed objects are flagged for re-validation.

### 3.5 Cross-platform reconciliation
Compares a Power BI measure against the same metric defined in a SQL warehouse. Since DAX
and SQL never produce the same hash even when they compute the same number, the comparison
instead looks at what each definition structurally reads — which tables, which columns,
which aggregation — and reports whether the two are consistent, divergent, or need human
review.

### 3.6 Lineage graph
Traces a number visually from the file or warehouse it was loaded from, through the
columns and measures that transform it, to the figure a report displays — answering "where
does this number actually come from" in one view.

### 3.7 Grounded chatbot
Answers plain-English questions about the model by calling read-only tools against the
extracted graph — never from memory or general Power BI knowledge — so every answer is
checkable against the real model. Supports automatic fallback across three LLM providers
if one is unavailable.

### 3.8 Review and audit trail
Low-confidence, inferred statements are queued for a human to accept, reject, or correct.
Each decision is bound to the fingerprint of the logic it was made against, so if that
logic later changes, the decision automatically becomes stale — it is not silently carried
forward onto logic nobody has actually reviewed.

### 3.9 Web application
A six-view interface (Overview, Model browser with lineage, Requirements, Drift,
Reconcile, Review) plus a docked chat panel, built in React and served directly by the
Python backend as a single self-contained page — no separate build step required to run
it.

### 3.10 Command-line tool
Eleven commands covering extraction, inspection, document generation, drift comparison,
reconciliation, evidence-bundle export, and serving the web application.

## 4. Platform integration status

| Platform named in the brief | Status |
|---|---|
| Power BI (`.pbix` and TMDL) | Fully implemented and tested |
| DuckDB (warehouse reconciliation) | Fully implemented and tested — used as the credential-free default |
| Snowflake | Connector implemented and unit-tested; not yet proven against a live account (the development network blocks that connection — confirmed, not assumed) |
| Databricks | Not implemented |
| AWS | Not implemented |

The comparison logic itself is platform-agnostic (built on a general SQL parser), so
adding a new warehouse is a small, contained piece of work rather than a redesign.

## 5. Verification

- **504 automated Python tests, 32 automated frontend tests**, all passing.
- Every frontend test was deliberately broken once and confirmed to fail, to prove the
  tests actually catch the problems they claim to.
- Every major feature was also checked by hand against real models and a real warehouse —
  not just unit tests — which is how several real defects were found and fixed during this
  project (a crash on a corrupted database file, a routing bug that served the wrong page,
  a metric-pairing rule that produced far too many false suggestions).
- ~9,390 lines of Python, ~3,586 lines of TypeScript.

## 6. What the sample data does and does not cover

Six sample Power BI models are used for testing and demonstration: two clinical-trial
safety models, one quality-control/manufacturing model, one general sales model, one
sales-returns model, and one supply-chain model. These are **test fixtures**, not a
built-in library of industry dashboards — Concordance does not ship pre-built content for
any domain. Pointed at any other real Power BI model, it extracts and documents that model
with the same behavior. Clinical trial safety and pharmaceutical quality control are
well represented in the current sample data; hospital pharmacy analytics and detailed
supply-chain KPIs are not currently represented in any sample model.

## 7. Known limitations, stated plainly

- Row-level security roles, calculation groups, and Power Query transformation steps are
  detected and reported as present, but their internal logic is not yet interpreted.
- Requirement wording is deterministic and rule-based rather than freely written, so
  models with many similar measures can produce repetitive-sounding sentences in the
  generated document. The underlying data behind each sentence is still accurate and
  distinct — this is a wording limitation, not a correctness one.
- There is no user login system. Review decisions record who claims to have made them,
  but the tool does not authenticate identity.
- Databricks and AWS warehouse connectors do not exist yet.

## 8. Summary

The tool takes a Power BI model as input and produces a requirements document, a drift
report, a reconciliation report, and a queryable chat interface — all derived from, and
provably traceable back to, the same underlying model. That traceability, enforced by
cryptographic fingerprints rather than by convention, is the project's central
contribution and the answer to the problem statement's request for a BRD/FRD automation
tool that also documents the Power BI semantic layer.
