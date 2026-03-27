"""Domain packs for engineering object families.

Each pack defines:
- object_type
- aliases
- required_components
- forbidden_components
- default_dimensions
- generation_notes
- validation_notes
"""

from __future__ import annotations

from .tire import TIRE_PACK
from .wheel import WHEEL_PACK
from .brake_disc import BRAKE_DISC_PACK
from .steering_wheel import STEERING_WHEEL_PACK
from .bracket import BRACKET_PACK
from .enclosure import ENCLOSURE_PACK

DOMAIN_PACKS = {
    "tire": TIRE_PACK,
    "wheel": WHEEL_PACK,
    "brake_disc": BRAKE_DISC_PACK,
    "steering_wheel": STEERING_WHEEL_PACK,
    "bracket": BRACKET_PACK,
    "enclosure": ENCLOSURE_PACK,
}


def get_domain_pack(object_type: str) -> dict | None:
    return DOMAIN_PACKS.get(object_type)


def list_domain_packs() -> list[dict]:
    return list(DOMAIN_PACKS.values())
