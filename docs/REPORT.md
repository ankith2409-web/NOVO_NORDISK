# Concordance

**Novo Nordisk GBS Hackathon 2026 — Problem Statement 5**
An AI agent with a chatbot interface that automates BRD/FRD creation, extracts the
Power BI semantic layer, and documents tables, joins, KPIs, measures and DAX logic.

---

## 1. The problem, stated precisely

A BRD is typed by hand from interviews and screenshots. It is correct on the day it is
signed and never verified again. Someone then edits one filter in one measure: the
number moves, and the document still describes the old logic. Nobody finds out until two
reports disagree in a meeting.

The gap is not documentation *effort*. It is that **no link exists between a sentence and
the logic it describes**, so nothing can tell you the sentence stopped being true. In a
regulated environment that is an audit finding rather than an inconvenience.

Generating a document faster does not close that gap. A generated document that cannot be
re-checked has the same defect as a typed one, arriving sooner.

## 2. What was built

Concordance extracts a Power BI semantic model — `.pbix` or TMDL — into one canonical
graph, and derives the BRD and FRD from that graph rather than from prose. Every
statement carries the objects it came from and the SHA-256 fingerprint of each.

Four capabilities sit on that one mechanism:

| | |
|---|---|
| **Derived documents** | Business and functional requirements, each bound to the objects it was derived from. |
| **Fingerprinted logic** | DAX is lexed and canonicalised before hashing. Reformatting is silent; a changed filter is not. |
| **Drift detection** | Compares two model versions and names the requirements a change puts in question — separately from the ones it provably does not. |
| **Cross-platform reconciliation** | Compares what a DAX measure and a warehouse view each structurally read, and reports where they differ. |

A grounded chatbot answers questions by calling read-only tools against the graph rather
than from model memory, and a React interface presents all of it, including a lineage
view that traces a number from the source file it was loaded from through to the measure
that reports it.

## 3. Why fingerprinting is the core

The whole design turns on one property, demonstrated on a real measure in the shipped
`QualityControl` model:

```
QC Metrics[OOS Rate]
original      2c7cbf0aa669
reformatted   2c7cbf0aa669   same — no drift
altered       76fa30df61e0   DRIFT DETECTED

mutation applied:
  DIVIDE([OOS Results], [Tests Performed], 1)
```

Whitespace, casing and comments change nothing, so a formatter run raises no false alarm
and nobody learns to ignore alarms. Changing the divide-by-zero fallback from `0` to `1`
changes the number, changes the hash, and flags every requirement bound to it.

This required lexing DAX rather than pattern-matching it — the module contains **zero
regex**, because a `//` inside a string literal is not a comment and no regular expression
can reliably tell the difference. Getting that wrong would mean two expressions with
different meanings sharing a fingerprint, which would make every downstream claim false.

Because identical hash means identical logic, **rename detection is provable rather than
guessed**: same fingerprint under a new name means the logic is untouched, and those
requirements need a reference update, not re-validation.

## 4. Results on real models

Seven models were used, in both Power BI formats. Six mirror real structure; the seventh,
`DiabetesCare`, is built on a real public dataset — 768 patient records from the Pima
Indians Diabetes study — kept with its known missing-value encoding intact rather than
cleaned.

**Drift, `ClinicalTrialSafety` v1 → v2:** 1 added, 1 removed, 4 changed, 103 unchanged —
and **8 requirements identified as now in question**. Listing changed objects is what any
diff does; naming the requirements whose evidence sits on those objects is what turns a
diff into *"this document may no longer describe this model"*.

**Reconciliation, `QualityControl` against its warehouse:** 6 metrics defined on both
sides — 4 consistent, 1 needing review, 1 divergent. The divergent one is `OOS Rate`,
where Power BI reads `testresult` and the warehouse reads `batch, testresult`: the two
divide by different denominators and will report different numbers.

No fingerprint could have found that. DAX and SQL never hash alike even when they compute
the same thing, so what is compared is what each definition *structurally reads* — tables,
columns, aggregations. That yields three verdicts rather than pass/fail: deciding whether
two arbitrary expressions in two languages compute the same number is undecidable in
general, and "needs review" is the honest answer for a difference that may or may not
matter.

**Metric pairing** was strengthened during development after measurement showed name
similarity alone scores `Batches Released` against `batches_rejected` at 0.80 — higher
than many true pairs. Pairing now weighs what each definition reads, which both demotes
false matches and finds real ones a name score cannot reach: on the real warehouse it
surfaces `Instrument Failure Rank` against `instrument_utilisation`, scoring 0.667 on
name, below any workable threshold.

## 5. What it refuses to claim

This is the part that is hardest to demo and matters most in a regulated industry.

- **It says what it did not read.** Translated object names are counted and named, with
  the consequence stated and — unusually — the reason: every other construct is read
  against a schema the source publishes, while a translation names its target through an
  untyped id with no published mapping, so attaching names to objects would be guesswork.
  This is derived
  from the files rather than a hand-maintained list, which cuts both ways: a construct the
  code has never heard of is still counted, and extending extraction shrinks the
  disclaimer automatically — reading row-level security and calculation groups removed
  them from the list with no list to edit. Where a format's reader is the limit rather
  than this code, that is said too: a `.pbix` security role filtering no table cannot be
  seen at all, so it is reported as a gap instead of being quietly absent.
- **It marks what it inferred.** A statement derived from structure rather than declared
  by the model is flagged low-confidence and queued for a person, never asserted quietly.
- **It lets a sign-off expire.** A review decision is bound to the fingerprints it was
  made against. Change the logic and it goes stale automatically — verified by accepting
  a requirement, editing the relationship beneath it, and confirming the standing moved
  from *decided* to *stale* with the fingerprint moving `20128b29d1e1` → `ee32bfb1016c`.
  An ordinary approved/not-approved column carries an old approval onto new logic by
  design; this cannot, because the comparison happens every time the question is asked.

## 6. Engineering

- **10,713 lines of Python, 4,081 of TypeScript, 657 automated tests** (618 Python, 39
  frontend). Tests were mutation-checked — each confirmed to fail when the logic it covers
  is deliberately broken, because a test that cannot fail protects nothing and a green run
  does not show that. The check applies to the load-bearing claims as well as the frontend:
  the test asserting a reviewer cannot sign off under another name was verified to fail
  when the server is allowed to take the author from the request body.
- **sqlglot** parses warehouse SQL into a real AST for the same reason DAX is lexed.
- **Dependency-light by choice**: the web layer is stdlib `http.server`; the lineage
  diagram is hand-laid-out SVG rather than a graph library.
- **The LLM is deliberately absent from derivation.** Requirements are produced
  deterministically, so the same model always yields the same document and every sentence
  is traceable. The language model powers the chatbot, where it answers by calling tools
  against the graph — an ungrounded generator would reintroduce exactly the
  confidently-wrong behaviour the project exists to remove.

Several defects were found by running the software against real data rather than by unit
tests — a Python list repr leaking into user-facing output, four bugs in warehouse
extraction, a source-pairing rule that produced six suggestions where one was useful, a
table declared outside the expected folder being dropped in silence, and a generated
requirement that called a ratio a count because it read an aggregation out of the
denominator. Each is recorded in the commit history with the measurement that exposed it.

## 7. Honest status

**Proven end to end:** both Power BI formats, drift, reconciliation, lineage to source,
the grounded chatbot, the decision log, multi-model serving, access control, row-level
security and calculation-group extraction, Power Query step interpretation, and
per-reviewer identity — a reviewer presenting their own token cannot record a decision
under a colleague's name, verified against a running server.

**Out of scope by decision:** cloud warehouse connectors. DuckDB exposes the same standard
information schema, so the extraction path exercised is the real one, and it needs no
account — which is what lets anyone clone the repository and run the reconciliation demo.
The comparison logic is dialect-agnostic, so adding one later is a connector function
against a stable interface.

**Not built:** perspectives, translations and column variations are counted and named but
not read. Requirement prose is rule-generated rather than model-written, so its vocabulary
is finite by construction — the price paid for a document that is byte-identical on every
run and traceable to a fingerprint.

Every one of these limits is reported by the software at the point of use, not only here.
That is the same standard the tool applies to the models it reads, and applying it to
itself is the point.

---

*All figures in this report were produced by running the tool against the models in the
repository. Nothing was estimated.*
