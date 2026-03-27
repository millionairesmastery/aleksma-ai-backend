"""Pydantic validation models for feature operations.

Auto-generates typed models from FEATURE_SCHEMAS so that AI-generated
feature operations are validated with physical constraints before
reaching the geometry kernel.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Physical constraints ──────────────────────────────────────────────────────

MAX_DIMENSION_MM = 10_000  # 10 meters
MIN_DIMENSION_MM = 0.001   # 1 micron
MAX_PATTERN_COUNT = 500
MAX_ANGLE_DEG = 360


# ── Feature operation model (what AI returns) ─────────────────────────────────

class FeatureOperation(BaseModel):
    """A single AI-generated feature operation."""
    action: Literal["add", "modify", "delete", "set_material"]
    feature_type: Optional[str] = None
    feature_id: Optional[int] = None
    params: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    # set_material fields
    material: Optional[str] = None
    color: Optional[str] = None
    message: Optional[str] = None

    @model_validator(mode="after")
    def check_action_requirements(self):
        if self.action == "add" and not self.feature_type:
            raise ValueError("'add' action requires 'feature_type'")
        if self.action == "modify" and self.feature_id is None:
            raise ValueError("'modify' action requires 'feature_id'")
        if self.action == "delete" and self.feature_id is None:
            raise ValueError("'delete' action requires 'feature_id'")
        return self


class FeatureOperationList(BaseModel):
    """Wrapper for parsing a list of operations from AI JSON."""
    operations: List[FeatureOperation]


# ── Parameter validation ──────────────────────────────────────────────────────

def validate_feature_params(feature_type: str, raw_params: dict, schemas: dict) -> dict:
    """Validate and clean feature params against FEATURE_SCHEMAS.

    Args:
        feature_type: The feature type key (e.g., "box", "fillet")
        raw_params: Raw parameter dict from AI or user
        schemas: The FEATURE_SCHEMAS dict from feature_engine

    Returns:
        Cleaned parameter dict with defaults filled in and types coerced.

    Raises:
        ValueError: If params violate physical constraints.
    """
    schema = schemas.get(feature_type)
    if not schema:
        raise ValueError(f"Unknown feature type: '{feature_type}'")

    param_schemas = schema.get("params", {})
    cleaned = {}
    errors = []

    for key, pschema in param_schemas.items():
        value = raw_params.get(key, pschema.get("default"))

        # Skip None values (optional params)
        if value is None:
            cleaned[key] = None
            continue

        ptype = pschema.get("type", "float")

        try:
            if ptype == "float":
                value = float(value)
                pmin = pschema.get("min")
                pmax = pschema.get("max", MAX_DIMENSION_MM)
                if pmin is not None and value < pmin:
                    errors.append(f"{key}: {value} below minimum {pmin}")
                if pmax is not None and value > pmax:
                    errors.append(f"{key}: {value} above maximum {pmax}")

            elif ptype == "int":
                value = int(value)
                pmin = pschema.get("min", 1)
                pmax = pschema.get("max", MAX_PATTERN_COUNT)
                if value < pmin:
                    errors.append(f"{key}: {value} below minimum {pmin}")
                if value > pmax:
                    errors.append(f"{key}: {value} above maximum {pmax}")

            elif ptype == "bool":
                value = bool(value)

            elif ptype in ("enum", "select"):
                options = pschema.get("options", [])
                if options and str(value) not in [str(o) for o in options]:
                    errors.append(f"{key}: '{value}' not in allowed options {options}")

            elif ptype == "vec3":
                if not isinstance(value, (list, tuple)) or len(value) < 3:
                    errors.append(f"{key}: must be [x, y, z] array, got {value}")
                else:
                    value = [float(v) for v in value[:3]]
                    for i, v in enumerate(value):
                        if abs(v) > MAX_DIMENSION_MM:
                            errors.append(f"{key}[{i}]: {v} exceeds max dimension {MAX_DIMENSION_MM}mm")

            elif ptype == "vector":
                if not isinstance(value, (list, tuple)) or len(value) < 3:
                    errors.append(f"{key}: must be [x, y, z] array, got {value}")
                else:
                    value = [float(v) for v in value[:3]]

            elif ptype == "json":
                pass  # Accept as-is

            elif ptype in ("edge_selector", "face_selector", "face_selector_string", "sketch_ref"):
                pass  # Accept as-is (validated downstream)

            else:
                pass  # Unknown type, pass through

        except (TypeError, ValueError) as e:
            errors.append(f"{key}: type error — {e}")

        cleaned[key] = value

    # Include any extra params from raw_params not in schema (pass through)
    for key, value in raw_params.items():
        if key not in cleaned:
            cleaned[key] = value

    if errors:
        raise ValueError(
            f"Parameter validation failed for '{feature_type}':\n" +
            "\n".join(f"  - {e}" for e in errors)
        )

    return cleaned


def parse_ai_operations(raw_json: Any) -> List[FeatureOperation]:
    """Parse and validate a list of feature operations from AI JSON output.

    Accepts either a list of dicts or a dict with an 'operations' key.
    """
    if isinstance(raw_json, dict):
        ops_list = raw_json.get("operations", [])
    elif isinstance(raw_json, list):
        ops_list = raw_json
    else:
        raise ValueError(f"Expected list or dict with 'operations' key, got {type(raw_json).__name__}")

    validated = []
    errors = []
    for i, op_data in enumerate(ops_list):
        try:
            validated.append(FeatureOperation.model_validate(op_data))
        except Exception as e:
            errors.append(f"Operation {i}: {e}")

    if errors and not validated:
        raise ValueError(
            "All AI operations failed validation:\n" +
            "\n".join(f"  - {e}" for e in errors)
        )

    return validated
