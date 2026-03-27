"""
ai_feature_bridge.py — Bridges AI responses to the parametric feature engine.

When a user asks the AI to modify a feature-tree part, this module:
1. Builds a specialized prompt that includes the current feature tree
2. Asks Claude to output structured JSON commands
3. Applies those commands via the feature engine
"""
from __future__ import annotations
import json
import os
from typing import Optional
from anthropic import Anthropic
from db import get_connection, put_connection
from feature_engine import FEATURE_SCHEMAS, ParameterResolver, rebuild_part_from_features

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

FEATURE_SYSTEM_PROMPT = """You are an expert CAD engineer modifying a parametric part.
The part is built from a feature tree (like Fusion 360). Each feature has a type, parameters, and a sequence number.

You must respond with ONLY a JSON object containing an "actions" array. Each action is one of:

1. Update an existing feature's parameters:
   {"type": "update_feature", "feature_id": <id>, "params": {<param_name>: <new_value>, ...}}

2. Add a new feature to the end of the tree:
   {"type": "add_feature", "feature_type": "<type>", "name": "<descriptive name>", "params": {<param_name>: <value>, ...}}

3. Suppress (hide) or unsuppress a feature:
   {"type": "suppress_feature", "feature_id": <id>, "suppressed": true/false}

4. Delete a feature:
   {"type": "delete_feature", "feature_id": <id>}

Available feature types and their parameters:
{schema_text}

RULES:
- Respond with ONLY valid JSON, no markdown, no explanation, no code blocks
- Use the exact feature_id values from the current feature tree
- Parameter values must be numbers (not strings) for numeric params
- When modifying dimensions, keep proportions reasonable
- When asked to "add a hole", use feature_type "hole" with face_selector and diameter
- When asked to "round edges" or "fillet", use feature_type "fillet" with edge_selector and radius
- When asked to "make it bigger/smaller/taller/wider", update the relevant parameter on the existing feature
- edge_selector examples: "|Z" (parallel to Z), ">Z" (top edges), "<Z" (bottom edges), "#Z" (perpendicular to Z)
- face_selector examples: ">Z" (top face), "<Z" (bottom face), ">X" (right face)
"""


def build_feature_schema_text() -> str:
    """Build a human-readable description of all feature types and their params."""
    lines = []
    for ft, schema in FEATURE_SCHEMAS.items():
        params_desc = []
        for pname, pdef in schema.get("params", {}).items():
            ptype = pdef.get("type", "float")
            default = pdef.get("default", "")
            label = pdef.get("label", pname)
            unit = pdef.get("unit", "")
            desc = f"  - {pname} ({ptype}, default={default}{', unit=' + unit if unit else ''}) — {label}"
            params_desc.append(desc)
        lines.append(f"\n{ft} ({schema.get('label', ft)}):")
        lines.extend(params_desc)
    return "\n".join(lines)


def build_feature_context(part_id: int, conn) -> dict:
    """Load the current feature tree for a part and format it for Claude."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, feature_type, name, sequence, params, suppressed, error_message
            FROM features WHERE part_id = %s ORDER BY sequence
        """, (part_id,))
        features = []
        for row in cur.fetchall():
            features.append({
                "id": row[0],
                "feature_type": row[1],
                "name": row[2],
                "sequence": row[3],
                "params": row[4] if isinstance(row[4], dict) else json.loads(row[4]),
                "suppressed": row[5],
                "error": row[6],
            })

        # Also load named parameters
        cur.execute("SELECT name, value FROM parameters WHERE part_id = %s", (part_id,))
        named_params = {r[0]: r[1] for r in cur.fetchall() if r[1] is not None}

        # Load part info
        cur.execute("SELECT name, color, material FROM parts WHERE id = %s", (part_id,))
        part_row = cur.fetchone()
        part_info = {"name": part_row[0], "color": part_row[1], "material": part_row[2]} if part_row else {}

    return {
        "features": features,
        "named_params": named_params,
        "part_info": part_info,
    }


def ai_modify_feature_part(part_id: int, user_message: str, conversation_history: list = None) -> dict:
    """
    Ask Claude to modify a feature-tree part based on user instructions.

    Returns:
        {
            "actions": [...],           # the actions that were applied
            "mesh": {...},              # updated mesh after applying actions
            "feature_errors": {...},    # any errors from rebuild
            "rebuild_status": "ok",
            "part_id": int,
            "ai_message": str,          # human-readable summary of what was done
        }
    """
    conn = get_connection()
    try:
        context = build_feature_context(part_id, conn)

        # Build the prompt
        schema_text = build_feature_schema_text()
        system = FEATURE_SYSTEM_PROMPT.replace("{schema_text}", schema_text)

        # Build the user message with current feature tree context
        feature_tree_desc = json.dumps(context["features"], indent=2)
        named_params_desc = json.dumps(context["named_params"], indent=2) if context["named_params"] else "none"

        full_user_msg = f"""Current part: {context['part_info'].get('name', 'Part')}

Current feature tree:
{feature_tree_desc}

Named parameters: {named_params_desc}

User request: {user_message}"""

        # Build messages
        messages = []
        if conversation_history:
            for msg in conversation_history[-4:]:  # last 4 messages for context
                messages.append({"role": msg.get("role", "user"), "content": str(msg.get("content", ""))})
        messages.append({"role": "user", "content": full_user_msg})

        # Call Claude
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system,
            messages=messages,
        )

        # Parse the JSON response
        response_text = response.content[0].text.strip()
        # Strip markdown code block if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1] if "\n" in response_text else response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3].strip()

        try:
            ai_result = json.loads(response_text)
        except json.JSONDecodeError:
            return {"error": f"AI returned invalid JSON: {response_text[:200]}"}

        actions = ai_result.get("actions", [])
        if not actions:
            return {"error": "AI returned no actions", "ai_raw": response_text}

        # Apply actions
        applied = []
        for action in actions:
            action_type = action.get("type")
            try:
                if action_type == "update_feature":
                    _apply_update_feature(conn, part_id, action)
                    applied.append(action)
                elif action_type == "add_feature":
                    new_id = _apply_add_feature(conn, part_id, action)
                    action["new_feature_id"] = new_id
                    applied.append(action)
                elif action_type == "suppress_feature":
                    _apply_suppress_feature(conn, part_id, action)
                    applied.append(action)
                elif action_type == "delete_feature":
                    _apply_delete_feature(conn, part_id, action)
                    applied.append(action)
            except Exception as e:
                action["error"] = str(e)
                applied.append(action)

        # Rebuild the part
        from feature_routes import _do_rebuild
        rebuild_result = _do_rebuild(part_id, conn)

        # Build a human-readable summary
        summary_parts = []
        for a in applied:
            if a.get("error"):
                summary_parts.append(f"Failed: {a['type']} — {a['error']}")
            elif a["type"] == "update_feature":
                summary_parts.append(f"Updated feature #{a['feature_id']} params: {a['params']}")
            elif a["type"] == "add_feature":
                summary_parts.append(f"Added {a['feature_type']} feature: {a.get('name', '')}")
            elif a["type"] == "suppress_feature":
                summary_parts.append(f"{'Suppressed' if a.get('suppressed') else 'Unsuppressed'} feature #{a['feature_id']}")
            elif a["type"] == "delete_feature":
                summary_parts.append(f"Deleted feature #{a['feature_id']}")

        return {
            "actions": applied,
            "mesh": rebuild_result.get("mesh"),
            "feature_errors": rebuild_result.get("feature_errors", {}),
            "rebuild_status": rebuild_result.get("rebuild_status", "ok"),
            "part_id": part_id,
            "ai_message": "; ".join(summary_parts) if summary_parts else "No changes made",
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        put_connection(conn)


def _apply_update_feature(conn, part_id, action):
    """Update a feature's parameters."""
    feature_id = action["feature_id"]
    new_params = action["params"]
    with conn.cursor() as cur:
        # Load current params and merge
        cur.execute("SELECT params FROM features WHERE id = %s AND part_id = %s", (feature_id, part_id))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Feature {feature_id} not found on part {part_id}")
        current = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        merged = {**current, **new_params}
        cur.execute("UPDATE features SET params = %s, updated_at = now() WHERE id = %s",
                    (json.dumps(merged), feature_id))
    conn.commit()


def _apply_add_feature(conn, part_id, action) -> int:
    """Add a new feature to the end of the tree."""
    feature_type = action["feature_type"]
    params = action.get("params", {})
    name = action.get("name", FEATURE_SCHEMAS.get(feature_type, {}).get("label", feature_type))
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM features WHERE part_id = %s", (part_id,))
        new_seq = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO features (part_id, feature_type, name, sequence, params, source)
            VALUES (%s, %s, %s, %s, %s, 'ai')
            RETURNING id
        """, (part_id, feature_type, name, new_seq, json.dumps(params)))
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def _apply_suppress_feature(conn, part_id, action):
    """Suppress or unsuppress a feature."""
    feature_id = action["feature_id"]
    suppressed = action.get("suppressed", True)
    with conn.cursor() as cur:
        cur.execute("UPDATE features SET suppressed = %s, updated_at = now() WHERE id = %s AND part_id = %s",
                    (suppressed, feature_id, part_id))
    conn.commit()


def _apply_delete_feature(conn, part_id, action):
    """Delete a feature from the tree."""
    feature_id = action["feature_id"]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM features WHERE id = %s AND part_id = %s", (feature_id, part_id))
    conn.commit()
