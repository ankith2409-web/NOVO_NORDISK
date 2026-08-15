# Real public datasets used as sample data

Everything else under `data/models/` is a synthetic model authored for this
project. `diabetes_patients.csv` here is different: it is real, public data,
kept as its own folder rather than mixed into `data/models/` so that
distinction stays visible.

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
