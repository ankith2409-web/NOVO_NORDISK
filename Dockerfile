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
# What this image serves is the sample data already in the repository --
# ClinicalTrialSafety, with its v2 for a live drift comparison, plus
# QualityControl as a second browsable model -- the same models used
# throughout this project's own screenshots. Pointing it at a real model
# instead means replacing the COPY lines below and start.sh's arguments;
# nothing else in the image changes.
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

# The two models this image serves live. Copied explicitly rather than the
# whole `data/` tree, which also holds the .pbix samples this image has no
# use for.
#
# No warehouse file here on purpose, even though QualityControl has one in
# local development: `data/warehouse/` is gitignored (it's a build artefact
# -- see scripts/build_warehouse.py -- not something meant to be committed),
# so a COPY naming it fails on any real build, exactly the way this one did
# the first time. start.sh doesn't pass --warehouse either, for the separate
# reason explained there, so there was nothing this image needed the file
# for. To wire reconciliation into a deploy later: add
# `RUN python scripts/build_warehouse.py` below (it's self-contained, no
# external inputs) and pass --warehouse in start.sh.
COPY data/models/ClinicalTrialSafety.SemanticModel ./data/models/ClinicalTrialSafety.SemanticModel
COPY data/models/ClinicalTrialSafety_v2.SemanticModel ./data/models/ClinicalTrialSafety_v2.SemanticModel
COPY data/models/QualityControl.SemanticModel ./data/models/QualityControl.SemanticModel

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
