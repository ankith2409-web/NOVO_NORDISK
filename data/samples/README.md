# Real public datasets used as sample data

## What under `data/models/` was written here, and what was not

Worth separating, because "we wrote the model *and* the tool that reads it" is
a weak demonstration and should not be able to hide behind a word like
"sample".

**Authored for this project** (synthetic, written to mirror real structure):
`ClinicalTrialSafety`, `ClinicalTrialSafety_v2`, `QualityControl`,
`DiabetesCare`, `StoreSales`. `DiabetesCare` is built on real public data --
see below. The rest describe structure and DAX only: they are semantic models,
not data, and no rows ship with them.

`StoreSales` exists for a reason worth recording. A reviewer said four times in
one session that the clinical and manufacturing models were the wrong thing to
evaluate a documentation tool on -- "if you are not able to understand these
things, you will not be able to correlate, because you don't know what is
Clinical Trial Safety... you can make a simple one, like use profit and sales
information". So `StoreSales` is sales, cost, profit, margin and orders, with
Total Sales defined as unit price times units sold exactly as she described it.
It is deliberately the plainest model here, and its five headline measures are
named after the tiles on the dashboard she was showing at the time.

**Not authored here**: the `.pbix` files. These are Power BI sample workbooks
distributed by Microsoft (`Sales_Returns_Sample`, `Supply_Chain_Sample`,
`AdventureWorks_Sales`), used unmodified. They matter precisely because nobody
on this project chose their DAX: running the translator over them is the only
honest measure of its coverage, and doing so dropped the figure from 80% on the
models above to 10% on Sales & Returns. That number is in the report, and has
since moved to 31% -- not by loosening anything, but by translating
previous-period measures that used to be refused. The report says why.

`diabetes_patients.csv` here is real, public data, kept as its own folder
rather than mixed into `data/models/` so that distinction stays visible.

## `diabetes_patients.csv` — Pima Indians Diabetes Dataset

768 real patient records (Pregnancies, Glucose, BloodPressure, SkinThickness,
Insulin, BMI, DiabetesPedigreeFunction, Age, Outcome), originally from the
National Institute of Diabetes and Digestive and Kidney Diseases and widely
mirrored as a public-domain machine-learning dataset (UCI Machine Learning
Repository / Kaggle). Fetched for this project from its canonical GitHub
mirror (`jbrownlee/Datasets`).

Left as-is, including its well-known data-quality issue: several columns
(`Glucose`, `BloodPressure`, `SkinThickness`, `Insulin`, `BMI`) use `0` to
encode a missing measurement rather than a real physiological zero. That is
kept rather than cleaned — it is a realistic property of clinical data, and
the `DiabetesCare` model built from it (`data/models/DiabetesCare.SemanticModel`)
is a more honest fixture for it, not a less useful one.

## Why this exists

An earlier gap check found that hospital-pharmacy/diabetes analytics was not
represented in any sample model, unlike clinical-trial safety and
manufacturing QC. `DiabetesCare` closes that gap with a model built on real
data instead of another synthetic one.
