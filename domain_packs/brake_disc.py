from __future__ import annotations

BRAKE_DISC_PACK = {
    "object_type": "brake_disc",
    "aliases": ["brake disc", "brake rotor", "disc rotor"],
    "required_components": [],
    "forbidden_components": ["spoke", "tread", "sidewall"],
    "default_dimensions": {
        "outer_diameter_mm": 330,
        "thickness_mm": 30,
        "hat_height_mm": 40,
        "lug_count": 5,
        "pcd_mm": 114.3,
        "center_bore_mm": 66.1,
    },
    "generation_notes": [
        "A brake disc (rotor) is a SINGLE CAST PIECE — union the disc body and mounting hat into one solid.",
        "Use REVOLVE for the disc profile: build a cross-section on XZ plane with the disc ring and hat step, then revolve 360 degrees.",
        "The hat is the stepped center section that connects the disc ring to the hub — model it as a stepped cylinder unioned with the disc ring.",
        "Cut bolt holes through the hat on the correct PCD (pitch circle diameter).",
        "Cut a center bore hole through the hat center.",
        "For ventilated discs: cut ventilation slots using a circular pattern of rectangular cuts between the two disc faces.",
        "Add fillets at the hat-to-disc transition for realistic casting geometry.",
        "Output as: result = disc.union(hat).cut(bolt_holes).cut(center_bore) — single solid, not parts dict.",
        "For a full brake assembly (if requested), caliper and pads would be separate parts in parts = {}.",
        "A plain 'brake disc' request means disc + hat only — one solid piece.",
    ],
    "validation_notes": [
        "Accept single-solid disc (result = ...) OR multi-part with at least one entry.",
        "Reject if no disc-like body exists (must have a flat circular ring shape).",
        "Reject tire or wheel spoke geometry for pure brake disc requests.",
    ],
}
