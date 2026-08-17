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

# `--compare-to` and `--warehouse` both attach only to whichever model is
# listed first (concordance/cli.py's own comment: "a comparison model and a
# warehouse describe *one* model"). The only real warehouse this repo can
# build is built for QualityControl's schema; the only real second version is
# ClinicalTrialSafety_v2. Wiring both onto one process would mean either an
# empty reconciliation (QualityControl's warehouse against a model it was
# never built against) or no reconciliation at all -- so this demo runs
# drift live and leaves reconciliation for a second deploy, rather than
# shipping a Reconcile tab that quietly returns nothing.
#
# To flip the choice -- reconcile live instead of drift -- swap the model
# order, drop --compare-to, add `RUN python scripts/build_warehouse.py` to
# the Dockerfile (data/warehouse/ is gitignored, so the file has to be built
# in the image rather than copied from the repo), and add:
#   --warehouse data/warehouse/quality_control.duckdb
exec concordance serve \
    data/models/ClinicalTrialSafety.SemanticModel \
    data/models/QualityControl.SemanticModel \
    --compare-to data/models/ClinicalTrialSafety_v2.SemanticModel \
    --decisions /app/state/decisions.jsonl \
    --host 0.0.0.0 \
    --port "${PORT:-8000}"
