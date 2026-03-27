"""Structured spec -> assembly/part generation plan."""

from __future__ import annotations

from typing import Any

from domain_packs import get_domain_pack


def _humanize(name: str) -> str:
    return name.replace("_", " ").title()


def build_assembly_plan(spec: dict[str, Any]) -> dict[str, Any]:
    object_type = spec.get("object_type", "custom_part")
    pack = get_domain_pack(object_type) or {}
    dims = spec.get("dimensions", {}) or {}
    requested_components = spec.get("components") or list(pack.get("required_components", ["body"]))
    is_multi_part = spec.get("is_multi_part", True)

    parts = []
    for idx, comp in enumerate(requested_components):
        parts.append(
            {
                "name": _humanize(comp),
                "slug": comp,
                "sequence": idx + 1,
                "required": comp in pack.get("required_components", []),
                "forbidden": comp in pack.get("forbidden_components", []),
            }
        )

    plan_notes = list(pack.get("generation_notes", []))
    plan_notes.extend(spec.get("notes", []))

    return {
        "object_type": object_type,
        "style": spec.get("style"),
        "is_multi_part": is_multi_part,
        "dimensions": dims,
        "parts": parts,
        "required_components": list(pack.get("required_components", [])) if is_multi_part else [],
        "forbidden_components": list(pack.get("forbidden_components", [])),
        "notes": plan_notes,
    }
