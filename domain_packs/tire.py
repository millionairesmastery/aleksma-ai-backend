from __future__ import annotations

TIRE_PACK = {
    "object_type": "tire",
    "aliases": ["tire", "tyre", "car tire", "car tyre"],
    "required_components": [],
    "forbidden_components": ["spoke", "hub", "lug_nut", "wheel_face", "rim_barrel", "rim_flange"],
    "default_dimensions": {
        "section_width_mm": 225,
        "aspect_ratio_pct": 45,
        "rim_diameter_in": 17,
    },
    "generation_notes": [
        "A tire is a SINGLE MOLDED PIECE — one solid, not separate parts.",
        "Use REVOLVE to create the tire: build a D-shaped cross-section profile on XZ plane, then revolve 360 degrees around the Y axis.",
        "Tire cross-section is a D-shape (NOT a circle): flat tread on top, curved sidewalls, flat inner bead.",
        "Calculate real dimensions: sidewall_height = section_width * aspect_ratio / 100. outer_radius = rim_radius + sidewall_height.",
        "Build the profile with .moveTo().lineTo().threePointArc()...close(), then .revolve(360, axis).",
        "The tread surface must be approximately flat/cylindrical — NOT spherical or balloon-shaped.",
        "Inner hole diameter must match rim diameter exactly.",
        "Output as: result = profile.revolve(...) — single solid.",
        "Do not generate spokes, hub, or lug nuts for a pure tire request.",
        "A tire request means tire only unless the prompt explicitly asks for a wheel/rim.",
    ],
    "validation_notes": [
        "Accept single-solid tire (result = ...) OR multi-part with at least one entry.",
        "Reject if wheel-like components appear in the generated part names.",
        "Reject if output contains disconnected decorative geometry unrelated to a tire.",
    ],
}
