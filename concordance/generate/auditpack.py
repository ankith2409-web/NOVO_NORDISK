"""The evidence bundle a validation reviewer would ask for.

In a regulated setting the document is not the deliverable on its own -- the
question is always "how do you know this is correct?". Everything needed to
answer that already exists here: each requirement carries the fingerprint of
the object satisfying it, and those fingerprints can be recomputed from the
model at any later date.

An audit pack collects that into one dated folder: the BRD and FRD in Word and
Markdown, the fingerprint manifest, and a manifest describing what was read and
by which version of the tool. Anyone holding the pack and the model can verify
every claim in it independently, which is the property that makes it evidence
rather than assertion.

This is deliberately not a compliance claim. It is a record of what was
extracted and what it was bound to; whether that satisfies any particular
regulation is a judgement for the organisation's quality function, and the
README says so plainly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from concordance import __version__
from concordance.drift import snapshot as snap
from concordance.generate import document as doc
from concordance.generate import word
from concordance.generate.requirements import Confidence, Kind
from concordance.graph.csg import SemanticGraph


@dataclass(frozen=True)
class AuditPack:
    """What was written, and the counts a reviewer checks first."""

    directory: Path
    files: tuple[Path, ...]
    requirement_count: int
    needs_review: int
    object_count: int
    unresolved: int
    coverage_gaps: int


def build(graph: SemanticGraph, directory: str | Path) -> AuditPack:
    """Write a complete evidence pack for one model."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)

    generated_on = datetime.now(timezone.utc).isoformat(timespec="seconds")
    name = graph.model.name
    written: list[Path] = []

    brd = doc.build(graph, Kind.BUSINESS)
    frd = doc.build(graph, Kind.FUNCTIONAL)

    for label, built in (("BRD", brd), ("FRD", frd)):
        markdown_path = out / f"{name}.{label}.md"
        markdown_path.write_text(doc.to_markdown(built), encoding="utf-8")
        written.append(markdown_path)
        written.append(word.write(built, out / f"{name}.{label}.docx"))

    # The fingerprint manifest is what makes the pack checkable: recompute it
    # from the model later and any divergence is visible.
    taken = snap.take(graph, label=f"audit-{generated_on}")
    written.append(taken.save(out / f"{name}.fingerprints.json"))

    requirements = brd.requirements + frd.requirements
    needs_review = sum(1 for r in requirements if r.needs_review)

    manifest = {
        "generated_on": generated_on,
        "tool": {"name": "concordance", "version": __version__},
        "model": {
            "name": name,
            "source_path": graph.model.source_path,
            "source_format": graph.model.source_type,
            **graph.model.summary(),
        },
        "requirements": {
            "total": len(requirements),
            "business": len(brd.requirements),
            "functional": len(frd.requirements),
            "by_confidence": {
                level.value: sum(1 for r in requirements if r.confidence is level)
                for level in Confidence
            },
            "awaiting_human_confirmation": needs_review,
        },
        # Stated rather than omitted: a pack that hides what could not be read
        # would misrepresent its own completeness.
        "limitations": {
            "unresolved_references": [
                {"from": u.source, "to": u.target, "reason": u.reason}
                for u in graph.unresolved
            ],
            "features_not_extracted": [
                {"feature": g.feature, "count": g.count, "reason": g.reason}
                for g in graph.model.coverage_gaps
            ],
        },
        "verification": (
            "Every requirement is bound to the fingerprint of the object that "
            "satisfies it. Re-run `concordance snapshot` against the model and "
            "compare with the fingerprint manifest in this pack: any difference "
            "identifies exactly which requirements are affected. Fingerprints "
            "are computed over the normalised expression, so reformatting does "
            "not register as a change and a change in meaning cannot be missed."
        ),
        "files": sorted(p.name for p in written),
    }

    manifest_path = out / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written.append(manifest_path)

    readme_path = out / "README.txt"
    readme_path.write_text(_readme(manifest), encoding="utf-8")
    written.append(readme_path)

    return AuditPack(
        directory=out,
        files=tuple(sorted(written)),
        requirement_count=len(requirements),
        needs_review=needs_review,
        object_count=len(taken.objects),
        unresolved=len(graph.unresolved),
        coverage_gaps=len(graph.model.coverage_gaps),
    )


def _readme(manifest: dict) -> str:
    model = manifest["model"]
    requirements = manifest["requirements"]
    limitations = manifest["limitations"]

    lines = [
        f"Requirements evidence pack — {model['name']}",
        "=" * 64,
        "",
        f"Generated      {manifest['generated_on']}",
        f"Tool           concordance {manifest['tool']['version']}",
        f"Source         {model['source_path']}  [{model['source_format']}]",
        "",
        "Contents",
        "-" * 64,
        "  *.BRD.docx / .md    Business requirements",
        "  *.FRD.docx / .md    Functional requirements",
        "  *.fingerprints.json Fingerprint of every object in the model",
        "  MANIFEST.json       Machine-readable record of this pack",
        "",
        "Model",
        "-" * 64,
        f"  {model['tables']} tables, {model['columns']} columns, "
        f"{model['measures']} measures, {model['relationships']} relationships",
        "",
        "Requirements",
        "-" * 64,
        f"  {requirements['total']} total "
        f"({requirements['business']} business, {requirements['functional']} functional)",
        f"  stated by the model     {requirements['by_confidence']['high']}",
        f"  inferred from structure {requirements['by_confidence']['medium']}",
        f"  awaiting confirmation   {requirements['by_confidence']['low']}",
        "",
    ]

    if requirements["awaiting_human_confirmation"]:
        lines += [
            f"  {requirements['awaiting_human_confirmation']} requirement(s) could not be",
            "  established from the model alone and are listed at the top of each",
            "  document. They are not approved by generating this pack.",
            "",
        ]

    lines += ["Limitations", "-" * 64]
    if limitations["unresolved_references"]:
        lines.append(
            f"  {len(limitations['unresolved_references'])} reference(s) could not be resolved:"
        )
        for item in limitations["unresolved_references"]:
            lines.append(f"    {item['from']} -> {item['to']}  ({item['reason']})")
    else:
        lines.append("  All references in the model resolved.")

    if limitations["features_not_extracted"]:
        lines.append("")
        lines.append("  Model features present but NOT covered by this pack:")
        for item in limitations["features_not_extracted"]:
            lines.append(f"    {item['count']} x {item['feature']}")
    else:
        lines.append("  No model features were detected that this tool cannot read.")

    lines += [
        "",
        "How to verify this pack",
        "-" * 64,
        "  concordance drift <this pack>/*.fingerprints.json <the model>",
        "",
        "  Any requirement whose bound object has changed since this pack was",
        "  generated will be named, with the old and new definitions side by side.",
        "",
        "  This pack records what was extracted and what each requirement was",
        "  bound to. Whether that satisfies a given regulatory expectation is a",
        "  judgement for the organisation's quality function, not a claim made",
        "  by this tool.",
        "",
    ]
    return "\n".join(lines)
