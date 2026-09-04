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
# StoreSales is listed first, so it is the model the interface opens on. A
# reviewer asked for that in as many words: the clinical and manufacturing
# models are the wrong thing to evaluate a documentation tool on, because the
# reader spends their attention on the domain instead of on the documentation.
# Sales, cost, profit and margin need no explaining to anybody.
#
# It is Microsoft's own Store Sales sample, unmodified, rather than one written
# here -- five tables, four joins, 32 measures of which 29 translate, and a real
# report whose thirteen tiles every one carry a measure. An authored model
# stood here first and was replaced by it: a file somebody else wrote is worth
# more as evidence, and this one also has the report layer an authored
# .SemanticModel folder cannot have, so the tiles have something to correlate.
#
# The last two are Microsoft's own published Power BI samples, unmodified.
# They are here because a reviewer asked the fair question: everything above was
# authored for this project, so of course it reads cleanly. A real .pbix built
# by someone else is the only honest test of that, and it says two things at
# once -- Supply Chain translates completely, and Sales & Returns translates
# eighteen measures of fifty-eight, refusing the rest for stated reasons rather
# than guessing. The second number is the more useful one to be able to show.
# They also carry a report layer, which the models above do not: the Dashboard
# tab has tiles to correlate only where a .pbix supplied them.
#
# Only the models that are actually present are passed. `serve` refuses to
# start on a source it cannot read -- correct behaviour for someone who typed a
# path, and fatal here, where `set -e` turns it into a container that exits on
# boot and a demo that is simply down. The two .pbix samples are committed so
# this should never trigger; it exists so that if one ever goes missing the
# demo loses a model instead of losing everything.
MODELS=""
for model in \
    data/models/StoreSales.pbix \
    data/models/ClinicalTrialSafety.SemanticModel \
    data/models/QualityControl.SemanticModel \
    data/models/DiabetesCare.SemanticModel \
    data/models/Supply_Chain_Sample.pbix \
    data/models/Sales_Returns_Sample.pbix
do
    if [ -e "$model" ]; then
        MODELS="$MODELS $model"
    else
        echo "start.sh: $model is missing, serving without it" >&2
    fi
done

# Real sign-in, wired from the platform's own environment variables rather
# than a file this script would have to carry. None of the three is a
# secret: a tenant domain and an API identifier are meant to be public, and
# an Auth0 client id is handed to every browser by design -- a single-page
# app cannot hold a secret, so Auth0 does not issue one to it. Set all three
# on the deployment (AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_CLIENT_ID) to turn
# on Google/email sign-in and verified reviewer names; leave any unset and
# the server runs exactly as it does today, unauthenticated.
AUTH0_ARGS=""
if [ -n "${AUTH0_DOMAIN:-}" ] || [ -n "${AUTH0_AUDIENCE:-}" ] || [ -n "${AUTH0_CLIENT_ID:-}" ]; then
    if [ -z "${AUTH0_DOMAIN:-}" ] || [ -z "${AUTH0_AUDIENCE:-}" ] || [ -z "${AUTH0_CLIENT_ID:-}" ]; then
        echo "start.sh: AUTH0_DOMAIN, AUTH0_AUDIENCE and AUTH0_CLIENT_ID must all be set together; ignoring the partial config and starting without sign-in" >&2
    else
        AUTH0_ARGS="--auth0-domain ${AUTH0_DOMAIN} --auth0-audience ${AUTH0_AUDIENCE} --auth0-client-id ${AUTH0_CLIENT_ID}"
    fi
fi

# shellcheck disable=SC2086  # word splitting is the point: one argument each.
exec concordance serve $MODELS \
    --compare-to ClinicalTrialSafety=data/models/ClinicalTrialSafety_v2.SemanticModel \
    --warehouse QualityControl=data/warehouse/quality_control.duckdb \
    --decisions /app/state/decisions.jsonl \
    --decisions-reset-on-restart \
    $AUTH0_ARGS \
    --host 0.0.0.0 \
    --port "${PORT:-8000}"
