#!/bin/sh
# Entrypoint for the demo container.
#
# Kept as a real shell script rather than a multi-line Dockerfile CMD --
# JSON-array CMD spanning several lines depends on exactly how the Dockerfile
# parser's line-continuation interacts with JSON-string parsing, which is easy
# to get subtly wrong and hard to verify without a build (this sandbox's
# network policy blocks Docker Hub pulls, so the image was never build-tested
# here). A shell script has none of that ambiguity, and is what you'll want to
# edit anyway when this points at a different model.
set -eu

# Both optional features are bound explicitly, by name, to the model each one
# actually belongs to. Drift needs the version pair, which only
# ClinicalTrialSafety has; the warehouse is built for QualityControl's schema
# and reports nothing in common against anything else.
#
# Without the MODEL= prefix both flags attach to whichever model is listed
# first, which made this exact configuration impossible to start -- the
# deployment served "not configured" on both tabs for every model.
exec concordance serve \
    data/models/ClinicalTrialSafety.SemanticModel \
    data/models/QualityControl.SemanticModel \
    data/models/DiabetesCare.SemanticModel \
    --compare-to ClinicalTrialSafety=data/models/ClinicalTrialSafety_v2.SemanticModel \
    --warehouse QualityControl=data/warehouse/quality_control.duckdb \
    --decisions /app/state/decisions.jsonl \
    --host 0.0.0.0 \
    --port "${PORT:-8000}"
