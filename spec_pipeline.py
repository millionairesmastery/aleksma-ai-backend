"""Prompt -> engineering spec JSON."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from domain_packs import list_domain_packs, get_domain_pack


SPEC_SYSTEM_PROMPT = """You convert engineering prompts into structured JSON specs.

Return ONLY valid JSON. No markdown. No prose.

Your job:
1. Classify the object type.
2. Extract dimensions and units.
3. Decide whether related parts are explicitly requested.
4. Produce a normalized engineering spec.

Rules:
- If the user asks for a tire only, include_rim=false and include_wheel=false.
- If the user asks for wheel + tire, include both.
- If the user asks for an F1 steering wheel, style must be "f1" not "road".
- Prefer explicit booleans like include_rim, include_wheel, include_hub.
- Include `components` as a list of intended component names.
- Include `notes` for important generation constraints.

Required keys:
{
  "object_type": string,
  "style": string|null,
  "is_multi_part": boolean,
  "include_rim": boolean|null,
  "include_wheel": boolean|null,
  "include_hub": boolean|null,
  "dimensions": object,
  "components": string[],
  "notes": string[]
}
"""


def _parse_json_block(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Spec stage did not return valid JSON")


def _fallback_object_type(user_query: str) -> str:
    q = user_query.lower()

    # Steering wheel must be classified before generic "wheel" match
    if "steering wheel" in q or "steering-wheel" in q:
        return "steering_wheel"

    # High-priority aliases that should win even when other keywords are present.
    # e.g. "create a rim to fit the tire" must be classified as 'wheel', not 'tire'.
    HIGH_PRIORITY = ["alloy wheel", "alloy rim", "performance rim", " rim ", "rim,", "rim.", "rim\n",
                     "wheel rim", "car rim", "wheel spoke", "spoke rim"]
    for term in HIGH_PRIORITY:
        if term in q:
            return "wheel"
    # Standalone "rim" or "spoke" at word boundaries
    import re as _re
    if _re.search(r'\brim\b', q) or _re.search(r'\bspoke\b', q):
        return "wheel"

    for pack in list_domain_packs():
        for alias in pack.get("aliases", []):
            if alias in q:
                return pack["object_type"]
    return "custom_part"


def _wants_componentized_tire(user_query: str) -> bool:
    q = user_query.lower()
    component_terms = [
        "cutaway",
        "cross section",
        "cross-section",
        "section view",
        "exploded",
        "exploded view",
        "component",
        "components",
        "separate part",
        "separate parts",
        "inner liner",
        "sidewall",
        "sidewalls",
        "bead",
        "beads",
        "tread and sidewall",
        "show layers",
        "layered",
    ]
    return any(term in q for term in component_terms)


def _wants_componentized_wheel(user_query: str) -> bool:
    q = user_query.lower()
    component_terms = [
        "exploded",
        "exploded view",
        "multi piece",
        "multi-piece",
        "component",
        "components",
        "cross section",
        "cross-section",
        "cutaway",
        "show parts",
    ]
    return any(term in q for term in component_terms)


def _post_process_spec(spec: dict[str, Any], user_query: str) -> dict[str, Any]:
    spec = dict(spec)
    spec.setdefault("notes", [])
    spec.setdefault("components", [])
    spec.setdefault("dimensions", {})

    wall_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*wall", user_query.lower())
    if wall_match and "wall_thickness_mm" not in spec["dimensions"]:
        spec["dimensions"]["wall_thickness_mm"] = float(wall_match.group(1))

    if spec.get("object_type") == "tire" and not _wants_componentized_tire(user_query):
        spec["is_multi_part"] = False
        spec["components"] = ["body"]
        spec["notes"] = [
            note for note in spec["notes"]
            if "parts = {}" not in str(note).lower()
        ]
        spec["notes"].append(
            "Generate the tire as one unified solid outer body. Do not create separate overlapping tread, sidewall, bead, or inner liner parts unless explicitly requested."
        )
        spec["notes"].append(
            "Use a rounded tire cross-section, not a boxy ring."
        )

    if spec.get("object_type") == "wheel":
        # Always generate wheels as multi-part assemblies so each sub-part
        # (hub, spokes, barrel, lip) is independently selectable and editable.
        spec["is_multi_part"] = True
        if not spec["components"] or spec["components"] == ["body"]:
            spec["components"] = ["hub", "barrel"]  # lips are part of barrel
            # Add spoke components based on spoke count
            spoke_count = spec.get("dimensions", {}).get("spoke_count", 5)
            for i in range(1, spoke_count + 1):
                spec["components"].append(f"spoke_{i}")
        spec["notes"].append(
            "Generate the wheel/rim as a multi-part assembly using parts = {}."
        )
        spec["notes"].append(
            "Each sub-part (Hub, Barrel, Front Lip, Back Lip, Spoke_1..N) must be a separate entry in the parts dict."
        )
        spec["notes"].append(
            "All parts must physically connect — spokes should overlap into the hub for a solid connection."
        )

        q = user_query.lower()
        spoke_match = re.search(r"(\d+)\s*[- ]?spoke", q)
        if spoke_match and "spoke_count" not in spec["dimensions"]:
            spec["dimensions"]["spoke_count"] = int(spoke_match.group(1))

        pcd_match = re.search(r"(\d)\s*[x×]\s*(\d{2,3}(?:\.\d+)?)", q)
        if pcd_match:
            spec["dimensions"]["lug_count"] = int(pcd_match.group(1))
            spec["dimensions"]["pcd_mm"] = float(pcd_match.group(2))

        bore_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:center bore|centre bore|hub bore|bore)", q)
        if bore_match and "center_bore_mm" not in spec["dimensions"]:
            spec["dimensions"]["center_bore_mm"] = float(bore_match.group(1))

    return spec


def _fallback_spec(user_query: str) -> dict[str, Any]:
    q = user_query.lower()
    object_type = _fallback_object_type(user_query)
    style = "f1" if "formula one" in q or "formula 1" in q or "f1" in q else None
    include_rim = None
    include_wheel = None
    include_hub = None

    if object_type == "tire":
        include_rim = any(k in q for k in ["rim", "wheel"])
        include_wheel = include_rim
        include_hub = include_rim

    pack = get_domain_pack(object_type) or {}
    components = list(pack.get("required_components", ["body"]))
    notes = list(pack.get("generation_notes", []))

    dims: dict[str, Any] = dict(pack.get("default_dimensions", {}))
    tire_match = re.search(r"(\d{3})\s*[x/]\s*(\d{2})\s*r\s*(\d{2})", q)
    if tire_match:
        dims["section_width_mm"] = int(tire_match.group(1))
        dims["aspect_ratio_pct"] = int(tire_match.group(2))
        dims["rim_diameter_in"] = int(tire_match.group(3))

    return _post_process_spec({
        "object_type": object_type,
        "style": style,
        "is_multi_part": object_type in {"tire", "wheel", "brake_disc", "steering_wheel", "enclosure"},
        "include_rim": include_rim,
        "include_wheel": include_wheel,
        "include_hub": include_hub,
        "dimensions": dims,
        "components": components,
        "notes": notes,
    }, user_query)


def build_engineering_spec(
    client,
    user_query: str,
    references: Optional[list[dict[str, Any]]] = None,
    knowledge_records: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Use AI first, then fall back to deterministic heuristics."""
    references = references or []
    knowledge_records = knowledge_records or []
    ref_summary = []
    for ref in references[:2]:
        ref_summary.append(
            {
                "name": ref.get("name"),
                "category": ref.get("category"),
                "dimensions": ref.get("dimensions", {}),
                "components": [c.get("name") for c in ref.get("sub_components", [])],
            }
        )
    knowledge_summary = []
    for record in knowledge_records[:3]:
        knowledge_summary.append(
            {
                "object_type": record.get("object_type"),
                "title": record.get("title"),
                "summary": record.get("summary"),
                "dimensions": record.get("dimensions", {}),
                "components": record.get("components", []),
                "generation_rules": record.get("generation_rules", []),
            }
        )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=SPEC_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt": user_query,
                            "references": ref_summary,
                            "knowledge_records": knowledge_summary,
                            "domain_packs": [
                                {
                                    "object_type": p["object_type"],
                                    "aliases": p.get("aliases", []),
                                    "required_components": p.get("required_components", []),
                                    "forbidden_components": p.get("forbidden_components", []),
                                }
                                for p in list_domain_packs()
                            ],
                        }
                    ),
                }
            ],
        )
        spec = _parse_json_block(response.content[0].text)
        if "object_type" not in spec:
            raise ValueError("Missing object_type")
        spec.setdefault("style", None)
        spec.setdefault("is_multi_part", True)
        spec.setdefault("include_rim", None)
        spec.setdefault("include_wheel", None)
        spec.setdefault("include_hub", None)
        spec.setdefault("dimensions", {})
        spec.setdefault("components", [])
        spec.setdefault("notes", [])
        return _post_process_spec(spec, user_query)
    except Exception:
        return _fallback_spec(user_query)
