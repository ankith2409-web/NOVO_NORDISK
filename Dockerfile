# Concordance -- live demo container.
#
# Why a container at all, when the project is otherwise dependency-light by
# choice: `concordance serve` is a single long-running process that loads a
# model into memory once at startup and keeps per-browser chat sessions alive
# across requests. That is precisely what a serverless platform (Vercel, and
# similarly-shaped functions elsewhere) cannot host -- there is no request
# boundary here to make stateless. A container on a platform that runs a
# persistent process (Render, Fly.io, a VM) is the honest fit.
#
# What this image serves is the sample data already in the repository.
# StoreSales -- Microsoft's own Store Sales sample, unmodified -- is the model
# the deployment opens on, because sales, cost, profit and margin need no
# domain explained to a reviewer before they can judge the documentation.
# ClinicalTrialSafety (with its v2 for a live drift comparison), QualityControl
# (with its warehouse for the reconciliation tab) and DiabetesCare sit
# alongside it as browsable models, and Supply Chain and Sales & Returns --
# also Microsoft's own, unmodified -- are the honest test of a .pbix nobody
# here authored. Pointing this at a different model instead means replacing
# the COPY lines below and start.sh's arguments; nothing else in the image
# changes.
#
# Every COPY line below must name a path `start.sh` actually lists, and every
# .pbix among them must be un-ignored in `.dockerignore` (which excludes
# `data/models/*.pbix` outright) as well as `.gitignore` (which is what keeps
# it in the repo this image is built from at all) -- three lists that all have
# to agree, and the way this drifts silently is a model going missing from the
# deployed container while every other list still claims it is there.
FROM python:3.11-slim

WORKDIR /app

# System build tooling for anything in the dependency chain that ships as
# source rather than a wheel on this platform/arch combination. Removed in
# the same layer so it never inflates the final image.
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY concordance ./concordance
# python-docx and pyjwt[crypto] (Auth0) come along automatically as of this
# project's pyproject.toml; nothing extra to request here.
RUN pip install --no-cache-dir .

# The models this image serves live. Copied explicitly rather than the whole
# `data/` tree, which also holds sample data (data/samples, data/warehouse)
# this image has no use for.
#
# StoreSales is first because `concordance serve` treats the first model given
# to it as the default -- the one the interface opens on -- and `start.sh`
# passes these in the same order they are listed here. It is Microsoft's own
# Store Sales sample, unmodified, so it also carries the report layer none of
# the TMDL folders below can have: five tables, a real dashboard, a domain
# that needs no explaining before a reviewer can judge the documentation.
#
# DiabetesCare earns its place among the TMDL folders by being the only one of
# the three that defines row-level security, object-level security, a KPI and
# a calculation group. Without it the copilot's `describe_security`,
# `list_kpis` and `list_calculation_groups` answer "this model defines none"
# on every model the deployment can reach -- three working features that would
# look broken to anyone who asked, which is worse than not having built them.
#
# Supply Chain and Sales & Returns close the list: also Microsoft's own,
# unmodified, and the only honest test of a .pbix nobody here authored -- one
# translates completely, the other refuses most of its measures for stated
# reasons rather than guessing, and both carry a report layer for the
# Dashboard tab to correlate.
COPY data/models/StoreSales.pbix ./data/models/StoreSales.pbix
COPY data/models/ClinicalTrialSafety.SemanticModel ./data/models/ClinicalTrialSafety.SemanticModel
COPY data/models/ClinicalTrialSafety_v2.SemanticModel ./data/models/ClinicalTrialSafety_v2.SemanticModel
COPY data/models/QualityControl.SemanticModel ./data/models/QualityControl.SemanticModel
COPY data/models/DiabetesCare.SemanticModel ./data/models/DiabetesCare.SemanticModel
COPY data/models/Supply_Chain_Sample.pbix ./data/models/Supply_Chain_Sample.pbix
COPY data/models/Sales_Returns_Sample.pbix ./data/models/Sales_Returns_Sample.pbix

# The demo warehouse is built here rather than copied: `data/warehouse/` is a
# generated artefact and gitignored, so it is never in the repo this image is
# built from. The script is self-contained -- it writes its own schema and rows
# and reaches nothing external -- so building it at image time is reproducible
# and needs no credentials. It is what makes the Reconcile tab answer on a
# deployment nobody has handed a Snowflake account to.
COPY scripts/build_warehouse.py ./scripts/build_warehouse.py
RUN python scripts/build_warehouse.py

# Decisions persist to this file for the life of the container. On a platform
# without a mounted persistent volume (Render's free tier included) this
# resets on every restart or redeploy -- acceptable for a demo, worth knowing
# before relying on it for anything that has to survive one.
RUN mkdir -p /app/state

COPY start.sh ./start.sh
RUN chmod +x ./start.sh

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# $PORT is injected by the hosting platform and varies per deploy; start.sh
# falls back to 8000 for a plain `docker run`. --host must be 0.0.0.0 --
# concordance serve's own default, 127.0.0.1, is right for a laptop and
# refuses every connection that arrives from outside the container.
CMD ["./start.sh"]
