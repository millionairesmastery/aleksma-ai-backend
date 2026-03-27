"""Design for Manufacturing warning rules."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List


@dataclass
class DFMWarning:
    part_name: str
    rule: str
    message: str
    severity: str  # "info", "warning", "error"

    def to_dict(self):
        return asdict(self)


MATERIAL_DENSITIES = {
    # Metals
    "steel": 7850, "chromoly_4130": 7850, "cast_iron": 7200,
    "stainless_304": 8000, "stainless_316": 8000,
    "aluminum_6061": 2700, "aluminum_7075": 2810, "aluminum_anodized_black": 2700,
    "titanium_gr5": 4430,
    "copper": 8960, "brass": 8500, "bronze": 8800,
    "gold": 19320, "silver": 10490, "nickel": 8900, "zinc": 7130, "chrome": 7850,
    # Composites
    "carbon_fiber": 1600, "fiberglass": 1800,
    # Plastics
    "abs_plastic": 1050, "pla_plastic": 1240, "petg_plastic": 1270,
    "nylon_pa6": 1150, "polycarbonate": 1200, "acetal_pom": 1410,
    "polypropylene": 900, "hdpe": 960, "acrylic_pmma": 1180,
    # Rubber / Elastomers
    "rubber_natural": 920, "rubber_silicone": 1100, "rubber_neoprene": 1230,
    "rubber_epdm": 860, "tpu_flexible": 1200,
    # Wood
    "wood_oak": 750, "wood_walnut": 650, "wood_maple": 700,
    "wood_cherry": 580, "wood_pine": 500, "wood_bamboo": 600,
    "plywood": 680, "mdf": 750,
    # Stone / Mineral
    "glass": 2500, "ceramic": 3900, "concrete": 2400, "marble": 2700, "granite": 2750,
    # Soft
    "leather": 860, "fabric_canvas": 400, "foam_pu": 30, "cork": 120,
}


def check_dfm(parts: List[dict], manufacturing_method: str = "cnc") -> List[DFMWarning]:
    """
    Run DFM checks on a list of parts.
    Each part dict: {"name", "material", "bbox": {"width", "height", "length"}, "volume_mm3"}
    """
    warnings: List[DFMWarning] = []
    for part in parts:
        name = part["name"]
        bbox = part.get("bbox", {})
        if not bbox:
            continue

        dims = [
            bbox.get("width", 999),
            bbox.get("height", 999),
            bbox.get("length", 999),
        ]
        min_dim = min(dims)
        max_dim = max(dims)

        if manufacturing_method == "cnc" and min_dim < 2:
            warnings.append(DFMWarning(
                name, "min_wall_thickness",
                f"Thinnest dimension is {min_dim:.1f}mm — below 2mm CNC minimum",
                "warning",
            ))
        elif manufacturing_method == "3d_print" and min_dim < 1:
            warnings.append(DFMWarning(
                name, "min_wall_thickness",
                f"Thinnest dimension is {min_dim:.1f}mm — below 1mm 3D print minimum",
                "warning",
            ))
        elif manufacturing_method == "sheet_metal" and min_dim < 0.5:
            warnings.append(DFMWarning(
                name, "min_wall_thickness",
                f"Thinnest dimension is {min_dim:.1f}mm — below 0.5mm sheet metal minimum",
                "warning",
            ))

        if manufacturing_method == "3d_print" and max_dim > 300:
            warnings.append(DFMWarning(
                name, "print_bed_size",
                f"Largest dimension is {max_dim:.0f}mm — exceeds typical 300mm print bed",
                "warning",
            ))

        if manufacturing_method == "cnc" and max_dim > 1000:
            warnings.append(DFMWarning(
                name, "cnc_travel",
                f"Largest dimension is {max_dim:.0f}mm — exceeds typical CNC travel",
                "info",
            ))

        volume_mm3 = part.get("volume_mm3", 0)
        material = part.get("material", "steel")
        density = MATERIAL_DENSITIES.get(material, 7850)
        if volume_mm3 > 0:
            weight_kg = (volume_mm3 / 1e9) * density
            if weight_kg > 50:
                warnings.append(DFMWarning(
                    name, "weight",
                    f"Estimated weight is {weight_kg:.1f}kg — may need crane/forklift for handling",
                    "info",
                ))

    return warnings
