# Concordance — Architecture

**Novo Nordisk GBS Hackathon 2026 — Problem Statement 5**

This document explains what the application does, how it is put together, and why it is
built the way it is. It is written to stand on its own — no prior context assumed.

---

## 1. What problem this solves

A BRD/FRD is typed by hand from interviews and screenshots. It is correct on the day it
is signed and never verified again. Someone edits one filter in one Power BI measure; the
number moves; the document still describes the old logic. Nobody finds out until two
reports disagree in a meeting.

Concordance closes that gap by **deriving** the requirements document from the model
itself — not summarizing it with an LLM, not templating it by hand — and binding every
sentence to the exact object and cryptographic fingerprint it came from. When the model
changes, the tool can tell you which sentences are now in question and which are provably
untouched.

## 2. What the application actually does — feature by feature

| Feature | What it does | Where |
|---|---|---|
| **Model extraction** | Reads a `.pbix` file or a TMDL model folder into one internal representation: tables, columns, measures, relationships, hierarchies, row-level and object-level security, calculation groups, KPI targets and thresholds, perspectives, role membership, column drill variations, and the file each table loads from together with the transformation steps applied on the way in | `concordance/adapters/` |
| **BRD/FRD generation** | Derives business and functional requirement statements from the model's structure. Each one carries the object(s) it was derived from and a SHA-256 fingerprint of the logic | `concordance/generate/` |
| **Fingerprinting** | Canonicalizes DAX (via a hand-written lexer, not regex) before hashing, so reformatting a measure never looks like a change, but a changed filter or constant always does | `concordance/fingerprint.py`, `concordance/normalize/dax.py` |
| **Drift detection** | Compares two versions of a model and reports what was added, removed, changed, or renamed — renames are *proven* by identical fingerprint, not guessed by name similarity — and which requirements that puts in question. Covers the three places a change moves every number while all measure fingerprints stay identical: an RLS filter, a calculation group item, and a table's load query | `concordance/drift/` |
| **Lineage graph** | Traces a number from the source file it was loaded from, through the columns and measures, to the number a report shows | `concordance/graph/csg.py`, `frontend/src/components/Lineage.tsx` |
| **Grounded chatbot** | Answers questions about the model by calling read-only tools against the graph — never from memory — so an answer is always checkable against the model | `concordance/agent/`, `concordance/llm/` |
| **Review / decision log** | Lets a person accept, reject, or correct a low-confidence requirement. The decision is bound to the fingerprint(s) it was made against, so it automatically goes **stale** — not silently carried over — when the underlying logic changes | `concordance/review/decisions.py` |
| **Access control** | Optional shared-token auth, per-reviewer tokens (`--users`), or Auth0 (`--auth0-*`) with signup and Google. A decision records the identity the server resolved from the credential, never one the caller supplied | `concordance/web/server.py`, `concordance/review/identity.py`, `concordance/review/auth0.py` |
| **Multi-model serving** | One server process can host several models at once, each with its own independent chat session and drift baseline | `concordance/web/api.py` (`ModelRegistry`) |
| **Web interface** | A five-view React app: Overview, Model (browser + lineage), Requirements, Drift, Review — plus a docked chat panel | `frontend/src/` |
| **CLI** | `extract`, `inspect`, `explain`, `verify`, `ask`, `drift`, `auditpack`, `snapshot`, `document`, `serve` | `concordance/cli.py` |

## 3. High-level architecture

```
┌─────────────────────────────────────────────┐
│                SOURCE (one of)                │
│   .pbix file          TMDL folder             │
│   (pbixray)           (hand-written           │
│                        TMDL parser)            │
└──────────────┬───────────────┬────────────────┘
               │               │
               ▼               ▼
        ┌────────────────────────────────────────────────┐
        │              concordance/model.py               │
        │   SemanticModel: tables, columns, measures,     │
        │   relationships, hierarchies, coverage gaps      │
        └───────────────────────┬──────────────────────────┘
                                 ▼
        ┌────────────────────────────────────────────────┐
        │           concordance/graph/csg.py               │
        │   Canonical Semantic Graph (networkx MultiDiGraph)│
        │   One graph. Every downstream feature reads only  │
        │   this — none of them re-parse the source.        │
        └───┬─────────┬──────────┬───────────────┘
            ▼         ▼          ▼
      Requirements  Drift    Chatbot tools
      (generate/)  (drift/) (agent/tools.py)
            │         │          │
            └─────────┴────┬─────┘
                            ▼
              ┌───────────────────────────┐
              │   concordance/web/api.py   │  <- pure functions,
              │   (JSON, no HTTP concerns) │     no socket needed to test
              └─────────────┬─────────────┘
                            ▼
              ┌───────────────────────────┐
              │ concordance/web/server.py  │  stdlib http.server,
              │ (routing, sessions, auth)  │  no framework
              └─────────────┬─────────────┘
                            ▼
              ┌───────────────────────────┐
              │   React app (frontend/)    │  5 views + docked chat,
              │   inlined into app.html    │  served by the same process
              └───────────────────────────┘
```

**The graph is the only source of truth downstream.** The chatbot, the documents, and
drift all read the same `SemanticGraph` object — none of them re-parses the
original file. That is what makes it structurally impossible for the chat's answer and the
generated document to disagree about the same model.

## 4. Extraction layer — `concordance/adapters/`

Two adapters, one shared output shape (`SemanticModel`):

- **`pbix.py`** — reads Power BI's compiled `.pbix` format via `pbixray`. Also extracts
  each table's Power Query (M) source text, row-level security roles, calculation groups,
  and the model's coverage gaps (features present in the file that this adapter does not
  turn into graph objects — KPI objects, object-level security, perspectives — reported,
  never silently dropped). RLS and calculation groups are read against the SQL PBIXRay
  publishes in its own source rather than against a layout guessed from a sample, which is
  what made extracting them defensible; what that reader structurally cannot show — a role
  filtering no table, and model-level permissions — is still reported as a gap.
- **`tmdl.py`** — a hand-written parser for Microsoft's TMDL text format (indentation and
  keyword-based, ~600 lines, no third-party TMDL library exists). Recently extended to
  derive coverage gaps the same way `pbix.py` does, by walking every declaration in the
  file and subtracting the ones that become graph objects — so a construct nobody listed
  is still reported. Because the list is derived rather than declared, extending
  extraction shrinks the disclaimer automatically: reading RLS roles and calculation
  groups removed them from the gap list with no list to edit.

### Power Query (M) lineage — `concordance/normalize/mquery.py`

Power Query expressions are lexed (not regex-matched, for the same reason DAX is lexed —
a comment marker inside a string literal defeats a regular expression) to find which
external system a table is actually loaded from: a CSV path, an Excel workbook, a SQL
server. This is what lets the lineage graph in the UI trace a number
all the way from `test_result.csv` through to the KPI that reports it, rather than
stopping at the Power BI table.

## 5. The Canonical Semantic Graph — `concordance/graph/csg.py`

A `networkx.MultiDiGraph`. Node kinds: `table`, `column`, `calculated_column`, `measure`,
`hierarchy`, `relationship`, and `source` (the file a table loads from). Edge
kinds:

| Edge | Meaning |
|---|---|
| `contains` | table → column |
| `defines` | table → measure |
| `references` | measure → whatever it reads (column or another measure) |
| `joins` | table → table |
| `loads` | table → the source it is loaded from |

Every out-edge means "reads" (`references` and `loads` share that direction on purpose),
which is what lets one traversal function answer "what does this depend on" regardless of
whether the answer is a column, another measure, or a CSV file.

Node ids are derived from **identity**, never content and never file path
(`measure:Table[Name]`), so the same object keeps the same id across re-extractions —
this is what makes drift comparison and stable requirement IDs possible.

## 6. Fingerprinting — the mechanism everything else relies on

`concordance/normalize/dax.py` is a **lexer**, not a regex-based normalizer — a `//`
inside a string literal is not a comment, and no regular expression can reliably tell the
difference. The module contains zero regular expressions. DAX is tokenized, comments and
whitespace are dropped, object names are case-folded (DAX is case-insensitive), and the
result is re-serialized into one canonical string, which is then SHA-256 hashed
(`concordance/fingerprint.py`).

Demonstrated live against a real measure:

```
QC Metrics[OOS Rate]
original      2c7cbf0aa669
reformatted   2c7cbf0aa669   same — no drift
altered       76fa30df61e0   DRIFT DETECTED   (0 → 1 in the zero-divide fallback)
```

Because identical fingerprint means provably identical logic, **rename detection is a
proof, not a heuristic**: an object with the same fingerprint under a new name is known to
be unchanged, and requirements bound to it need only a reference update, not
re-validation. `concordance/drift/compare.py` splits these two cases apart explicitly.

## 7. Requirements generation — `concordance/generate/requirements.py`

**Deliberately deterministic — no LLM in this path.** Requirements are derived from
structural rules over the graph (a measure becomes a functional requirement stating its
exact DAX; an inactive relationship becomes a business requirement flagged for
confirmation; a `%`-named measure gets a behavior tag like "ratio"). The same model always
produces the same document, and every statement traces to a graph object via
`Evidence(node_id, fingerprint, detail)`.

Anything the deriver is not confident enough to assert outright (structure it infers
rather than something the model states directly) is marked `Confidence.LOW` and routed to
the **Review queue** instead of the document.

## 8. Drift — `concordance/drift/`

`snapshot.py` fingerprints every object in a model. `compare.py` diffs two snapshots and
classifies each change as `ADDED`, `REMOVED`, `CHANGED`, or `RENAMED` (proven via identical
fingerprint — see §6). It then walks every requirement's bound evidence to report which
ones now need **re-validation** (their logic actually changed) versus which need only a
**reference update** (only a name they cite moved) — the difference between a reviewer
re-checking a handful of statements and a hundred.

## 9. Review and decisions — `concordance/review/decisions.py`

An append-only JSON Lines log. Each decision (`accepted` / `rejected` / `corrected`)
records the fingerprints of the evidence it was made against, at the moment it was made.
Answering "is this decision still valid" is a live comparison against the model's
*current* fingerprints — not a stored flag — so a sign-off automatically becomes **stale**
the moment the underlying DAX changes, with no one needing to remember to revoke it.
Verified end-to-end: accepted a requirement, edited the relationship it rested on, and
watched the same requirement ID move from `decided` to `stale` as its fingerprint changed.

The author field is named `author_claimed` — this server has no user accounts, and a
shared access token is not a person, so the log does not pretend otherwise.

## 10. The chatbot — `concordance/agent/`

`tools.py` exposes nine read-only functions over the graph (`overview`, `list_tables`,
`list_measures`, `describe_measure`, `describe_table`, `list_relationships`,
`list_hierarchies`, `what_uses`, `search`). `chat.py` runs a tool-calling loop: the model
is given only these tools and must call them to answer, so an answer is always
**grounded** — traceable to a real tool call against the real graph — never pulled from
the LLM's training data about Power BI in general.

**Provider fallback** (`concordance/llm/fallback.py`): Gemini → Anthropic → Groq, in that
order, gated on the failure actually being provider-unavailable (rate limit, quota,
auth) rather than blindly retrying every error. `llm/fake.py` provides a scriptable fake
provider so the entire tool-calling loop is unit-testable without any network access or
API key.

## 11. Web layer — `concordance/web/`

- **`api.py`** — every endpoint is a plain function `(context, params) -> dict`, with zero
  HTTP concerns, so the whole API is unit-tested without opening a socket.
  `ModelRegistry` lets one server host several models; `?model=` selects which one, and an
  unrecognized name is refused with 404 rather than silently answering from the default —
  answering from the wrong model would be a confident, complete, wrong answer.
- **`server.py`** — stdlib `http.server` (`ThreadingHTTPServer`), deliberately
  dependency-free. Per-browser sessions (`SessionStore`) so two tabs don't interleave one
  chat conversation; one conversation *per model* within a session, so switching models
  never replays one model's tool results into a question about another. Optional
  constant-time-compared access token for when the server is bound beyond loopback.

Every error path is guarded: a missing model file and a malformed request body both
return a readable JSON error rather than a stack trace or a dropped connection — verified
by deliberately constructing each failure and checking the actual response.

## 12. Frontend — `frontend/src/`

React 19 + Vite + Tailwind 4. Five views (`Overview`, `Model`, `Requirements`, `Drift`,
`Review`) plus a docked `Copilot` chat panel that stays mounted whether shown
or hidden, so closing it never drops the conversation.

- **`lib/api.ts`** — the only place that talks to the backend. The active model is held
  here (not threaded through every view), so no view can forget to route a request at the
  right model.
- **`lib/remember.ts`** — persists which view/model/panel state to `localStorage`, but
  only after validating the remembered value still exists on the *current* server — a
  browser can be pointed at different servers with different models loaded.
- **`components/Lineage.tsx`** — a hand-laid-out SVG tracing a node's ancestry and descent
  through the graph, including all the way to its source file. Deliberately not
  a general graph-visualization library — the model is overwhelmingly hierarchical and a
  bounded, exact chain answers "where does this number come from" better than a canvas.

Two build modes: `npm run dev` (proxies `/api` to a separately running Python process, for
development) and `npm run build:embedded` (inlines the whole app into one HTML file that
`concordance serve` hands out directly — the mode a plain `pip install` user gets, with no
Node required).

## 13. Testing

**681 Python tests, 60 frontend tests.** Both suites are written against real behavior,
not mocks of it wherever a real dependency is available, and the fixture models in
`data/models/` are real (if small) Power BI files, not synthetic data.

The frontend tests are mutation-checked: each was confirmed to actually fail when the
logic it covers is deliberately broken (e.g., commenting out the model-routing parameter
fails four tests), because a test suite that cannot fail protects nothing and a green run
does not show that on its own.

## 14. Honest limits

Stated here in the same terms the software states them to a user, because a tool whose
subject is "does this document overstate what it knows" has to hold itself to the same
standard:

- **Object name translations** are the one construct still reported rather than read, and
  the reason is the same rule applied consistently: every other construct here is read
  against SQL PBIXRay publishes in its own source, whereas a translation names its target
  through a bare integer `ObjectType` with no published mapping. Guessing which integer
  means "measure" would attach Danish names to the wrong objects, and a document
  confidently calling one measure by another's translated name is worse than one that says
  it did not read them. A model's single base culture is deliberately not counted as a
  translation — a warning that fires on every model ever read is one nobody reads.
- **For `.pbix`, model-level role permissions are never surfaced** by the reader
  underneath, and are left empty rather than defaulted to a plausible `read`. A role that
  filters no table is invisible to the RLS frame, but is recovered from role membership
  when it has members.
- **No LLM in requirement generation**, by design (determinism and traceability).
  Sentences now vary with what each measure actually does, but the vocabulary is finite by
  construction; fluency is the price paid for a document that is byte-identical on every
  run and traceable to a fingerprint.
- **Identity has two routes and no roles.** `--users` resolves a decision's author from
  the reviewer's own bearer token; `--auth0-*` verifies an Auth0 access token (RS256 only,
  signature checked against the tenant's JWKS, issuer/audience/expiry all required) and
  brings signup and Google with it. Either way the name comes from the credential and
  never from the request body. What neither provides is authorisation: anyone who can sign
  in can do anything, and Auth0 RBAC is not consulted. The Auth0 path is unproven against
  a live tenant — see `docs/AUTH.md`.
