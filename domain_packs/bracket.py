from __future__ import annotations

BRACKET_PACK = {
    "object_type": "bracket",
    "aliases": ["bracket", "l bracket", "mounting bracket", "angle bracket"],
    "required_components": [],
    "forbidden_components": ["tread", "spoke", "hub"],
    "default_dimensions": {
        "thickness_mm": 4,
        "hole_diameter_mm": 6,
        "fillet_radius_mm": 3,
    },
    "generation_notes": [
        "A bracket is a SINGLE MACHINED/BENT PIECE — union all flanges into one solid.",
        "For an L-bracket: create two perpendicular rectangular flanges and union them.",
        "Add FILLETS at all bend transitions — real brackets have bend radii, not sharp edges.",
        "Cut mounting holes through the flanges using .cut() with cylinders on correct positions.",
        "Use realistic wall thickness (typically 2-6mm for sheet metal, thicker for cast brackets).",
        "For gusset/reinforcement ribs, union additional triangular or trapezoidal solids at the bend.",
        "Output as: result = flange1.union(flange2).fillet(radius).cut(holes) — single solid.",
        "Bolts, nuts, and fasteners are NOT part of the bracket — do not generate them unless explicitly asked.",
    ],
    "validation_notes": [
        "Accept single-solid bracket (result = ...) OR multi-part with at least one entry.",
        "Reject if the output contains unrelated wheel/tire components.",
    ],
}
