from __future__ import annotations

ENCLOSURE_PACK = {
    "object_type": "enclosure",
    "aliases": ["enclosure", "housing", "box enclosure", "case", "electronics enclosure"],
    "required_components": [],
    "forbidden_components": ["spoke", "hub", "tread"],
    "default_dimensions": {
        "wall_thickness_mm": 2.5,
        "corner_radius_mm": 3,
        "length_mm": 120,
        "width_mm": 80,
        "height_mm": 40,
    },
    "generation_notes": [
        "Default to a SINGLE SOLID enclosure (3D printed style) unless multi-part is requested.",
        "Create a box with .box(), then SHELL it using .shell(-wall_thickness) to hollow it out, leaving one face open.",
        "Add corner FILLETS on the outer edges for realistic molded/printed geometry.",
        "Add mounting bosses inside corners: union small cylinders to the interior, then cut screw holes into them.",
        "Cut ventilation holes/slots into side walls using rectangular or circular patterns.",
        "For a lid: if requested as assembly, model body and lid as separate parts in parts = {}.",
        "For single-piece (3D printed): result = box.shell().union(bosses).cut(vents) — one solid.",
        "For sheet metal style (if requested): use separate panels as parts = {} with bent tabs.",
        "Output as: result = ... for single-piece, or parts = {} if explicitly asked for body + lid assembly.",
    ],
    "validation_notes": [
        "Accept single-solid enclosure (result = ...) OR multi-part with body/lid entries.",
        "Reject if there is no box-like enclosed volume.",
    ],
}
