"""Engineering validation for generated CAD output."""

from __future__ import annotations

from typing import Any


def _normalize_names(names: list[str]) -> list[str]:
    return [n.lower().replace(" ", "_") for n in names]


def validate_generation(
    spec: dict[str, Any],
    plan: dict[str, Any],
    part_names: list[str],
    script: str,
) -> tuple[bool, list[str]]:
    """Return (ok, errors)."""
    errors: list[str] = []
    names = _normalize_names(part_names)
    object_type = spec.get("object_type")

    required = [c.lower() for c in plan.get("required_components", [])]
    forbidden = [c.lower() for c in plan.get("forbidden_components", [])]
    is_multi_part = plan.get("is_multi_part", True)

    for req in required:
        if not any(req in n for n in names):
            errors.append(f"Missing required component: {req}")

    for bad in forbidden:
        if any(bad in n for n in names):
            errors.append(f"Forbidden component present: {bad}")

    q = object_type or ""
    if not is_multi_part and len(part_names) != 1:
        errors.append("Expected a single unified part, but multiple parts were generated")

    if q == "tire":
        for bad in ("spoke", "hub", "rim_barrel", "rim_flange", "lug_nut", "wheel_face"):
            if any(bad in n for n in names):
                errors.append(f"Tire request generated wheel component: {bad}")
        if not is_multi_part:
            for layered in ("tread", "sidewall", "bead", "inner_liner"):
                if any(layered in n for n in names):
                    errors.append(f"Unified tire request should not output separate {layered} parts")

    if q == "wheel":
        spoke_count = sum(1 for n in names if "spoke" in n)
        if spoke_count and not any("hub" in n for n in names):
            errors.append("Wheel has spokes but no hub")
        if spoke_count and not any("rim" in n or "barrel" in n for n in names):
            errors.append("Wheel has spokes but no rim/barrel")
        # Reject visualization-only parts
        viz_keywords = ["bolt_hole", "center_bore", "drop_center", "well_ring",
                        "counterbore", "visualization", "highlight", "viz", "bore_vis"]
        viz_parts = [n for n in names if any(kw in n for kw in viz_keywords)]
        if viz_parts:
            errors.append(
                f"Wheel contains visualization-only parts that should not exist: "
                f"{', '.join(viz_parts)}. Cut holes into the Hub instead of adding solid cylinders."
            )
        if not is_multi_part:
            if len(part_names) != 1:
                errors.append("Wheel/rim request should produce one connected body unless multi-piece was requested")
            script_l = script.lower()
            has_bore_or_lug_logic = (
                "hole(" in script_l
                or "cborehole" in script_l
                or "cutthruall" in script_l
                or (".cut(" in script_l and any(token in script_l for token in [
                    "bore",
                    "centre_bore",
                    "center_bore",
                    "lug",
                    "pcd",
                    "bolt",
                ]))
            )
            if not has_bore_or_lug_logic:
                errors.append("Wheel/rim body appears to be missing center bore or lug-hole cutting operations")
            if "spoke" not in script_l and "polar" not in script_l and "for " not in script_l:
                errors.append("Wheel/rim body appears to be missing spoke-generation logic")
        if any("tire" in n or "tread" in n for n in names):
            errors.append("Wheel request accidentally generated tire-like geometry")

    if q == "steering_wheel" and spec.get("style") == "f1":
        if "torus" in script.lower() and "body" not in names:
            errors.append("F1 steering wheel should not be modeled as a road-style circular torus-only wheel")

    if len(part_names) == 0:
        errors.append("No parts were generated")

    return len(errors) == 0, errors


def build_retry_feedback(spec: dict[str, Any], plan: dict[str, Any], errors: list[str]) -> str:
    lines = [
        "The generated CAD did not satisfy the engineering requirements.",
        f"Object type: {spec.get('object_type')}",
    ]
    if spec.get("style"):
        lines.append(f"Style: {spec.get('style')}")
    if plan.get("required_components"):
        lines.append(f"Required components: {', '.join(plan['required_components'])}")
    if plan.get("forbidden_components"):
        lines.append(f"Forbidden components: {', '.join(plan['forbidden_components'])}")
    lines.append("Errors:")
    lines.extend(f"- {e}" for e in errors)
    lines.append("Regenerate the CAD and return only corrected Python code.")
    return "\n".join(lines)
