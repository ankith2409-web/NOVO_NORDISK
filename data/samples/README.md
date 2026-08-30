# Real public datasets used as sample data

## What under `data/models/` was written here, and what was not

Worth separating, because "we wrote the model *and* the tool that reads it" is
a weak demonstration and should not be able to hide behind a word like
"sample".

**Authored for this project** (synthetic, written to mirror real structure):
`ClinicalTrialSafety`, `ClinicalTrialSafety_v2`, `QualityControl`,
`DiabetesCare`. The last of these is built on real public data -- see below.

**Not authored here**: the `.pbix` files. These are Power BI sample workbooks
distributed by Microsoft (`Sales_Returns_Sample`, `Supply_Chain_Sample`,
`AdventureWorks_Sales`), used unmodified. They matter precisely because nobody
on this project chose their DAX: running the translator over them is the only
honest measure of its coverage, and doing so dropped the figure from 80% on the
models above to 10% on Sales & Returns. That number is in the report.

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
