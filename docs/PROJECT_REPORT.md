# Concordance — Project Report

**Novo Nordisk GBS Hackathon 2026 — Problem Statement 21**
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

A reviewer asked for a small architectural diagram in this document. Here is the whole
system on one page. The single rule that shapes it: the source is parsed once into one
graph, and every feature below reads that graph. Nothing re-parses a `.pbix`, so no two
features can disagree about what a model says.

```
   .pbix file                TMDL folder              SQL warehouse
   (data model + report)     (model only)             (DuckDB, Snowflake,
        │                         │                    Databricks, Redshift, Athena)
        └────────────┬────────────┘                         │
                     ▼                                      │
        ┌──────────────────────────────┐                    │
        │   SemanticModel (model.py)   │                    │
        │  tables · columns · measures │                    │
        │  joins · hierarchies · KPIs  │                    │
        │  security · report pages     │                    │
        └──────────────┬───────────────┘                    │
                       ▼                                    │
        ┌──────────────────────────────┐                    │
        │  Canonical Semantic Graph    │  one graph,        │
        │  (graph/csg.py, networkx)    │  read by all       │
        └──┬────┬─────┬─────┬─────┬────┘                    │
           ▼    ▼     ▼     ▼     ▼                         ▼
        BRD/  DAX→  Dash-  Drift  Line-              Reconciliation
        FRD   SQL   board  (v1 v  age                (is the warehouse's
              +one  tiles→ v2)                        definition the same?)
              query  DAX
           │    │     │     │     │                         │
           └────┴──┬──┴─────┴─────┴─────────────────────────┘
                   ▼
        ┌──────────────────────────────┐
        │  web/api.py — plain functions │  every endpoint testable
        │  returning JSON, no HTTP      │  without opening a socket
        └──────────────┬───────────────┘
                       ▼
        ┌──────────────────────────────┐
        │  web/server.py — stdlib only  │  no web framework
        └──────────────┬───────────────┘
                       ▼
              Browser UI  ·  CLI  ·  OpenLineage export
```

### 3.1 Model extraction
Reads a Power BI model in either of its two real formats — a compiled `.pbix` file or a
TMDL project folder — into one internal representation covering tables, columns,
measures, relationships, hierarchies, row-level and object-level security, calculation
groups, KPI targets and status thresholds, perspectives, role membership, column drill
variations, and which file or database each table is loaded from together with the
transformation steps applied on the way in.

### 3.2 Automated BRD/FRD generation
Produces business and functional requirement statements directly from the model's
structure. No language model writes these sentences — they come from deterministic rules,
so the same model always produces the same document, and every sentence carries the exact
object and a cryptographic fingerprint of the logic it was derived from. Each statement
describes what that particular object actually does, derived from the DAX functions it
uses, rather than repeating one fixed sentence for every measure.

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

This deliberately extends past measures to the three places where a change moves every
number on a report while every measure's own fingerprint stays identical: a row-level
security filter (which rows a given reader's figures are computed over), a calculation
group item (which substitutes its own logic around whichever measure is displayed), and
a table's load query (which rows reach the model in the first place).

### 3.5 Cross-platform reconciliation
Compares a Power BI measure against the same metric defined in a SQL warehouse. Since DAX
and SQL never produce the same hash even when they compute the same number, the comparison
instead looks at what each definition structurally reads — which tables, which columns,
which aggregation — and reports whether the two are consistent, divergent, or need human
review.

### 3.5a DAX to SQL, at a stated grain
Translates a Power BI measure into a query that can be run against a warehouse. DAX has no
single SQL equivalent because a measure's value depends on the filter context the report
supplies; naming a grain is what closes that gap, since `GROUP BY` *is* the filter context
written down. Every query therefore travels with the grain it was written for.

Coverage, measured rather than estimated:

| Model | Measures | Translated | Notes |
|---|---|---|---|
| QualityControl | 20 | 18 | authored for this project |
| ClinicalTrialSafety | 24 | 21 | authored for this project |
| DiabetesCare | 12 | 11 | authored for this project |
| Supply Chain (Microsoft sample) | 4 | 4 | not authored here |
| Sales & Returns (Microsoft sample) | 58 | 18 | not authored here |

The last row is the important one and is not a typo. On models written for this project
the translator handles 89%; on a Power BI file nobody here authored it handles 31%. The
difference is not a defect being hidden — it is what the first three numbers were always
worth.

That last figure was 10% until a reviewer pushed back on one of the refusals. Measures
comparing against an earlier period — `PREVIOUSMONTH` and its siblings — were declined on
the grounds that the answer depends on which month the report is showing. True, and true of
every measure, which is why this tool makes the caller name the grain in the first place. A
previous-month measure simply names its own: it is meaningful at one row per month and at
no other, so it is now translated there, as a window function over the period, and the
period joins the `GROUP BY`. Twelve more of Sales & Returns' measures translate as a result,
and three more of ClinicalTrialSafety's.

The remaining refusals are `ALL`, `ALLSELECTED`, `ISINSCOPE` and `USERELATIONSHIP`, which
change or remove the filter context rather than reading it, and which no single query at a
fixed grain can express.

**Everything in one query.** Asked for twice — "convert the whole thing into an SQL query
rather than giving one each" — and now offered alongside the per-measure queries: every
measure as columns of a single query you run once, instead of forty run in turn and lined
up by hand. Measures reading different tables are aggregated separately and joined on the
grain afterwards, because selecting them from one joined query multiplies one table's rows
by another's: a batch count reported 10 where it should have reported 2 until a test that
compares every combined column against that measure's own query caught it.

What matters more than the ratio is what happens to the other 40. Each is refused with the
construct that stopped it and why, rather than being emitted as SQL that parses and quietly
computes a different number. A wrong query is worse than an absent one: the absent one gets
asked about. Automated tests assert that no refusal on a real Power BI file is ever a gap
in the translator dressed up as a limit of the DAX.

### 3.5b Dashboard tiles, joined to their DAX and their SQL

The question reviewers asked most, and the one they named as the missing piece: *"How do
I understand which DAX and SQL is for which particular KPI in the dashboard?"*

A `.pbix` contains the report as well as the model — pages, tiles, each tile's title, and
the query behind it — and nothing had ever read it. Concordance now does, so a tile
titled "Net Sales" is shown next to the measure that produces its number, that measure's
DAX, and the SQL that reproduces it. It is the only view here that starts from what a
person is actually looking at and works back to the definition; every other one starts
from the model and works outward.

Nothing is inferred. Titles are the titles the author typed, and a tile with no title
says so rather than having one written for it. One detail is worth stating because it
decides whether the feature is trustworthy: a tile refers to its fields by an alias fixed
when the field was first used, and that alias does not follow later renames. In
Microsoft's own sample the first card is titled "Net Sales", carries the alias
`Analysis DAX.Sales`, and actually selects the measure `Net Sales` — there has never been
a measure called `Sales` in that model. Reading the alias reported 22 fields that do not
exist and missed 7 real measures; reading the query Power BI actually evaluates gives 2
unresolved out of 89, and both of those are genuine.

On Sales & Returns: 18 pages, 71 tiles, 27 of which show a measure.

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

Reviewers sign in either through Auth0 — which brings account creation and Google with it
as Universal Login features — or with a personal access token, kept as the path for a
machine that cannot reach the internet. Either way the server records the name it resolved
from the credential presented rather than a name supplied in the request, so a reviewer
cannot sign a decision off under a colleague's name. Every entry
also records whether its author was verified this way or merely self-declared, because a
trail that mixes the two without saying which is which forces every entry to be treated
as unverified.

### 3.9 Web application
The interface leads with four things and keeps the rest to hand. **Overview** — what the
model holds. **Dashboard** — each tile and the formula behind it. **Dataset + SQL** — the
tables, how they join, and every measure's DAX and SQL, copyable in one go. **Documents**
— the BRD and FRD. Below a divider sit Browse objects, Drift, Warehouse and To confirm.

That split is a correction rather than a design. A reviewer, looking at seven equally
weighted tabs, said: *"Make it very simple, that's what I'm trying to tell. Because we
don't need like warehouse or to confirm drift. We just need everything to be converted
into tables, of course, columns, measures, of course what joins it is having... Whatever
you have taken is very complex."* The first response to that was to add an eighth tab,
which is the opposite of listening. Nothing has been deleted — a second reviewer asked
for drift by name, and a capability that cannot be shown is worse than one nobody clicks
— but the four she asked for now stand alone, and the rest are where a first-time reader
will not trip over them.

Built in React and served directly by the Python backend as a single self-contained page
— no separate build step required to run it.

The generated BRD and FRD can be downloaded from the Documents view as Markdown or as
Word, the latter because a requirements document in a regulated setting is circulated for
signature. Both are rendered by the same code the command line uses, so the file someone
downloads and the file the CLI writes cannot disagree — a test asserts they are
byte-identical rather than leaving it to convention.

Failures are presented as the situation they actually are rather than as one red box.
A stopped server, an expired session, a name the model does not contain and an exhausted
model quota are four unrelated problems with four different fixes, so each states what
happened, why, and what to do — with the exact command where one applies, close-name
suggestions where the server computed them, and a control that retries the request that
failed. A refusal the tool handled correctly is not coloured the same as something
breaking, because an interface that cries wolf is one whose warnings get ignored. Every
failure carries `role="alert"`, so it is announced rather than left to be noticed.

Colour is measured rather than chosen: `frontend/contrast.mjs` reads the tokens out of
the stylesheet and checks every foreground/background pair the interface actually paints,
in both themes, against the 4.5:1 floor. It runs as a script because the palette was
already hand-tuned once to exactly that floor, and a value maintained from memory is one
that silently drifts below it.

### 3.10 Command-line tool
Eleven commands covering extraction, inspection, document generation, drift comparison,
reconciliation, evidence-bundle export, and serving the web application.

## 4. Platform integration status

| Platform | Status |
|---|---|
| Power BI (`.pbix` and TMDL) | Fully implemented, tested end to end |
| DuckDB (warehouse reconciliation) | Fully implemented, tested end to end — the default warehouse |
| Snowflake | Connector implemented; unit-tested, **not yet run against a live account** |
| Databricks (Unity Catalog) | Connector implemented; unit-tested, **not yet run against a live account** |
| AWS Redshift | Connector implemented; unit-tested, **not yet run against a live account** |
| AWS Athena (Glue) | Connector implemented; unit-tested, **not yet run against a live account** |

The distinction in that table is the important part, so it is spelled out rather than left
to the word "implemented".

**What is implemented and proven.** The comparison logic — reading an information schema,
parsing view SQL into an AST, comparing what each definition structurally reads — is
platform-agnostic and exercised end to end against DuckDB, which implements the same
standard information schema the cloud warehouses do. Each cloud platform adds a connection
function of roughly thirty lines: authenticate, hand the resulting cursor to the same
adapter, close. Those functions are unit-tested against fake database cursors, which
proves the parameters sent to each driver, the identifier case folding (Snowflake folds
unquoted names to upper case; Databricks, Redshift and Athena all fold to lower — get it
wrong and the query silently matches nothing), the SQL dialect selected, and that the
connection is closed on both the success and the failure path.

**What is not proven.** No live handshake against a real Snowflake, Databricks, Redshift or
Athena account. The network this project was built on blocks all four at the policy layer,
so authenticating for real has to happen elsewhere. Everything inside this process is
tested; whether a real account accepts the credentials is not, and the first real
connection should be treated as the acceptance test.

DuckDB remains the default for a deliberate reason: it needs no account, no credentials and
no network, so anyone can clone this repository and run the full reconciliation demo in one
command. A cloud warehouse as the default would have made the project's central feature
unrunnable for anyone without a paid account.

## 5. Verification

- Built with **Claude Code** (Anthropic's coding agent) driving the implementation, with
  every design decision, test and refusal reviewed and accepted by the team. The reasoning
  behind each non-obvious choice is written into the source as comments rather than left
  in a chat log, which is why the modules read as arguments rather than as instructions.
- **942 automated Python tests, 60 automated frontend tests**, all passing.
- Tests for the load-bearing claims were deliberately broken once and confirmed to fail,
  to prove they actually catch the problems they claim to — including the one asserting a
  reviewer cannot sign off under another reviewer's name.
- Reconciliation was checked against a real DuckDB warehouse holding real metric
  definitions. Its base tables are deliberately empty: the comparison is structural — which
  tables, columns and aggregations each side reads — so it needs the SQL, not the rows. No
  figure in this report is a row count from that warehouse.
- Every major feature was also checked by hand against real models —
  not just unit tests — which is how several real defects were found and fixed during this
  project: a crash on a corrupted database file, a routing bug that served the wrong page,
  a metric-pairing rule that produced far too many false suggestions, a table declared
  outside the expected folder being dropped in silence, and a generated requirement that
  described a ratio as a count because it read an aggregation out of the denominator.
- ~13,900 lines of Python, ~5,900 lines of TypeScript.

## 6. What the sample data does and does not cover

Eight sample Power BI models are used for testing and demonstration: two clinical-trial
safety models (a version pair), one quality-control/manufacturing model, one
diabetes/metabolic-health model, one retail sales-and-profit model, one general sales
model, one sales-returns model, and one supply-chain model. These are **test fixtures**,
not a built-in library of industry dashboards — Concordance does not ship pre-built
content for any domain. Pointed at any other real Power BI model, it extracts and
documents that model with the same behavior.

Five of the eight were authored for this project. The other three are Power BI sample
workbooks published by Microsoft, used unmodified — they are the only honest test of how
this behaves on DAX nobody here chose, and section 3.5a reports what that test showed.
They are also the only ones carrying a report layer, so section 3.5b's tile correlation
has something to correlate.

`StoreSales` is the newest and deliberately the plainest: sales, cost, profit, margin and
orders, with Total Sales defined as unit price times units sold. It exists because a
reviewer said four times in one session that a clinical or manufacturing model is the
wrong thing to evaluate a documentation tool on — a reader spending their attention on
what a protocol deviation is has none left for whether the document describing it is any
good. It is the model the interface opens on.

Of the five authored here, four are synthetic, written to mirror real structure. The
diabetes model is
different: it is built directly on a real, public dataset — the Pima Indians Diabetes
Dataset (National Institute of Diabetes and Digestive and Kidney Diseases), 768 real
patient records — kept including its known missing-value encoding rather than cleaned, so
the fixture reflects an actual data-quality issue rather than a tidy synthetic one. See
`data/samples/README.md` for provenance.

Clinical trial safety, pharmaceutical quality control, diabetes/metabolic health and
plain retail sales are now represented in the sample data; hospital pharmacy analytics and
detailed supply-chain KPIs are not currently represented in any sample model.

## 7. Known limitations, stated plainly

- Translated object names are the one construct still reported rather than read. Every
  other construct is read against a schema the underlying reader publishes; a translation
  names its target through an untyped integer with no published mapping, so attaching each
  translated name to the right object would require guessing — and a document that calls
  one measure by another's translated name is worse than one that says it did not read
  them. A model's single base culture is deliberately not counted as a translation, since
  a warning that appears on every model is one nobody reads.
- In the `.pbix` format, a role's model-level permission is never surfaced by the reader
  and is left empty rather than filled in with a plausible default.
- Sign-in supports Auth0 — including account creation and Google, both of which are
  Universal Login features rather than screens built here — with per-reviewer tokens kept
  as the offline path. The Auth0 route has not yet been exercised against a live tenant:
  its verification logic is tested against a locally generated signing key, but the
  network this was built on blocks Auth0 outright. See `docs/AUTH.md`.
- Requirement wording is generated from rules rather than by a language model. Sentences
  now vary according to what each measure actually does, but the vocabulary is finite by
  construction — this buys reproducibility and traceability at some cost in fluency, and
  that trade is deliberate.
- Reconciliation compares what two definitions *structurally read* — tables, columns,
  aggregations — not the numbers they produce. Deciding whether two arbitrary expressions
  in two languages compute the same value is undecidable in general, which is why a third
  verdict ("needs review") exists instead of a forced pass/fail.
- Cloud warehouse connectors (Snowflake, Databricks, Redshift, Athena) are
  implemented but have never been run against a live account — the build network
  blocks all four. Their logic is unit-tested against fake database cursors; the
  first real connection is the acceptance test. See section 4.
- A model is opened from a file, not from a Power BI URL. A reviewer asked to paste a
  workspace link and have everything generate from it. That needs an Azure AD app
  registration — tenant id, client id and secret, service-principal API access enabled
  for the tenant, and the principal added to the workspace — none of which can be created
  from this side. The upload box is the same journey minus the link: the file it produces
  is the file that link would fetch.
- Time intelligence is translated only where the shift is unambiguous: `PREVIOUSDAY`,
  `PREVIOUSMONTH`, `PREVIOUSQUARTER`, `PREVIOUSYEAR`. `DATEADD` and `PARALLELPERIOD` take
  an offset and a unit that need not match the grain, and `SAMEPERIODLASTYEAR` shifts a
  year while keeping the period — each is a different translation, and guessing between
  them would put a wrong number on screen, so they are still refused by name.

## 8. Summary

The tool takes a Power BI model as input and produces a requirements document, a drift
report, a reconciliation report, and a queryable chat interface — all derived from, and
provably traceable back to, the same underlying model. That traceability, enforced by
cryptographic fingerprints rather than by convention, is the project's central
contribution and the answer to the problem statement's request for a BRD/FRD automation
tool that also documents the Power BI semantic layer.
