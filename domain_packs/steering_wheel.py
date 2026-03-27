from __future__ import annotations

STEERING_WHEEL_PACK = {
    "object_type": "steering_wheel",
    "aliases": ["steering wheel", "f1 steering wheel", "formula one steering wheel", "formula 1 steering wheel"],
    "required_components": [],
    "forbidden_components": [],
    "default_dimensions": {
        "style": "road",
        "overall_width_mm": 370,
        "grip_diameter_mm": 30,
        "spoke_count": 3,
        "hub_diameter_mm": 70,
    },
    "generation_notes": [
        "A steering wheel (rim + spokes + hub) is ONE PIECE — union all parts into a single solid.",
        "Distinguish between road steering wheel and F1 steering wheel.",
        "Road steering wheel: use a TORUS (makeTorusShape or revolve a circle around the steering axis) for the circular grip ring.",
        "Create spokes as cylinders or lofted shapes connecting the hub to the rim ring.",
        "Union hub + spokes + rim ring into one solid: result = hub.union(spokes).union(rim_ring).",
        "Add fillets at spoke-to-hub and spoke-to-rim transitions for realism.",
        "F1 steering wheel: rectangular main body with side grips, NOT a circular ring.",
        "For F1 style, use a box-like body with cutouts, side grip contours, and a quick-release hub.",
        "Do NOT generate a circular wheel for an F1 steering wheel request.",
        "Output as: result = ... — single solid for a basic steering wheel.",
        "Buttons, paddle shifters, and screens would be separate parts only if explicitly requested as an assembly.",
    ],
    "validation_notes": [
        "Accept single-solid steering wheel (result = ...) OR multi-part with at least one entry.",
        "Reject if an F1 request generates a circular-only wheel.",
    ],
}
