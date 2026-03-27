"""
Feature Tree API routes — CRUD for parametric features + rebuild.
"""

import json
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel
from typing import Optional

from db import get_connection, put_connection
from auth import UserInfo, get_current_user_optional
from permissions import get_user_id_for_request
from feature_engine import (
    FEATURE_SCHEMAS,
    rebuild_part_from_features,
    compute_feature_hash,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FeatureCreate(BaseModel):
    feature_type: str
    name: str = ""
    params: dict = {}
    insert_after_sequence: Optional[int] = None  # None = append at end

class FeatureUpdate(BaseModel):
    name: Optional[str] = None
    params: Optional[dict] = None
    suppressed: Optional[bool] = None

class FeatureReorder(BaseModel):
    feature_ids: list[int]

class ParameterCreate(BaseModel):
    name: str
    expression: str
    description: str = ""
    unit: str = "mm"
    group_name: str = "Parameters"

class ParameterUpdate(BaseModel):
    expression: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    group_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_branch_id(conn, part_id: int, user=None) -> int:
    """Get the active branch_id for the current user (or main if no user)."""
    from main import _ensure_main_branch, _get_user_branch
    from permissions import get_user_id_for_request
    with conn.cursor() as cur:
        user_id = get_user_id_for_request(user) if user else None
        if user_id:
            return _get_user_branch(cur, user_id, part_id)
        return _ensure_main_branch(cur, part_id)


def _load_features(conn, part_id: int, branch_id: int = None) -> list[dict]:
    with conn.cursor() as cur:
        if branch_id:
            cur.execute("""
                SELECT id, feature_type, name, sequence, params, suppressed, error_message, source
                FROM features WHERE part_id = %s AND branch_id = %s ORDER BY sequence
            """, (part_id, branch_id))
        else:
            # Fallback for parts without branch_id set
            cur.execute("""
                SELECT id, feature_type, name, sequence, params, suppressed, error_message, source
                FROM features WHERE part_id = %s AND branch_id IS NULL ORDER BY sequence
            """, (part_id,))
        return [
            {
                "id": r[0], "feature_type": r[1], "name": r[2],
                "sequence": r[3],
                "params": r[4] if isinstance(r[4], dict) else json.loads(r[4]),
                "suppressed": r[5], "error_message": r[6], "source": r[7],
            }
            for r in cur.fetchall()
        ]


def _load_named_params(conn, part_id: int) -> dict[str, float]:
    with conn.cursor() as cur:
        cur.execute("SELECT name, value FROM parameters WHERE part_id = %s", (part_id,))
        return {r[0]: r[1] for r in cur.fetchall() if r[1] is not None}


def _auto_version_checkpoint(conn, part_id: int, label: str, user_id: int = None):
    """Best-effort auto-checkpoint on the user's working branch."""
    try:
        from main import _ensure_user_working_branch, _ensure_main_branch, _create_version
        with conn.cursor() as cur:
            cur.execute("SELECT cadquery_script FROM parts WHERE id = %s", (part_id,))
            row = cur.fetchone()
            script = row[0] if row else ""
            if user_id:
                branch_id = _ensure_user_working_branch(cur, user_id, part_id)
            else:
                branch_id = _ensure_main_branch(cur, part_id)
            _create_version(cur, part_id, branch_id, script,
                            label=label, author_type="human", auto=True)
        conn.commit()
    except Exception:
        pass  # version control is best-effort


def _do_rebuild(part_id: int, conn, up_to_seq: int = None, branch_id: int = None) -> dict:
    """Rebuild part from its features, update DB caches, return result."""
    features = _load_features(conn, part_id, branch_id)
    named_params = _load_named_params(conn, part_id)

    if not features:
        return {"rebuild_status": "ok", "mesh": None, "feature_errors": {}, "part_id": part_id}

    result = rebuild_part_from_features(features, named_params, up_to_seq)

    with conn.cursor() as cur:
        # Update per-feature errors
        for feat in features:
            err = result["feature_errors"].get(feat["sequence"])
            if err != feat.get("error_message"):
                cur.execute("UPDATE features SET error_message = %s, updated_at = now() WHERE id = %s",
                           (err, feat["id"]))

        # Update part mesh cache + bbox
        if result["mesh"]:
            h = compute_feature_hash(features)
            bbox = result.get("bbox") or {}

            cur.execute("""
                UPDATE parts SET
                    mesh_cache = %s, feature_hash = %s, feature_tree_mode = true,
                    bbox_min_x = %s, bbox_min_y = %s, bbox_min_z = %s,
                    bbox_max_x = %s, bbox_max_y = %s, bbox_max_z = %s,
                    updated_at = now()
                WHERE id = %s
            """, (
                json.dumps(result["mesh"]), h,
                bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                part_id,
            ))

    conn.commit()

    return {
        "rebuild_status": result["rebuild_status"],
        "mesh": result["mesh"],
        "feature_errors": result["feature_errors"],
        "last_good_sequence": result.get("last_good_sequence", 0),
        "part_id": part_id,
    }


# ---------------------------------------------------------------------------
# Feature CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("/parts/{part_id}/features")
async def list_features(part_id: int, user: UserInfo = Depends(get_current_user_optional)):
    conn = get_connection()
    try:
        branch_id = _resolve_branch_id(conn, part_id, user)
        features = _load_features(conn, part_id, branch_id)
        return features
    finally:
        put_connection(conn)


@router.post("/parts/{part_id}/features")
async def create_feature(part_id: int, body: FeatureCreate, user: UserInfo = Depends(get_current_user_optional)):
    # Validate feature type
    if body.feature_type not in FEATURE_SCHEMAS:
        raise HTTPException(400, f"Unknown feature type: {body.feature_type}")

    # Fill default params from schema
    schema = FEATURE_SCHEMAS[body.feature_type]
    params = {}
    for key, pdef in schema["params"].items():
        params[key] = body.params.get(key, pdef.get("default"))
    # Overlay any extra params from request
    for key, val in body.params.items():
        params[key] = val

    name = body.name or schema["label"]

    conn = get_connection()
    try:
        branch_id = _resolve_branch_id(conn, part_id, user)
        with conn.cursor() as cur:
            # Determine sequence (scoped to branch)
            if body.insert_after_sequence is not None:
                new_seq = body.insert_after_sequence + 1
                cur.execute(
                    "UPDATE features SET sequence = sequence + 1 WHERE part_id = %s AND branch_id = %s AND sequence >= %s",
                    (part_id, branch_id, new_seq),
                )
            else:
                cur.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM features WHERE part_id = %s AND branch_id = %s", (part_id, branch_id))
                new_seq = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, source)
                VALUES (%s, %s, %s, %s, %s, %s, 'manual')
                RETURNING id
            """, (part_id, branch_id, body.feature_type, name, new_seq, json.dumps(params)))
            feat_id = cur.fetchone()[0]
        conn.commit()

        # Rebuild from branch features
        rebuild_result = _do_rebuild(part_id, conn, branch_id=branch_id)
        _auto_version_checkpoint(conn, part_id, f"Add {body.feature_type}: {name}", user_id=get_user_id_for_request(user))
        feature = {"id": feat_id, "feature_type": body.feature_type, "name": name,
                   "sequence": new_seq, "params": params, "suppressed": False}
        return {**rebuild_result, "feature": feature}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.put("/parts/{part_id}/features/{feature_id}")
async def update_feature(part_id: int, feature_id: int, body: FeatureUpdate, user: UserInfo = Depends(get_current_user_optional)):
    conn = get_connection()
    try:
        updates = []
        values = []
        if body.name is not None:
            updates.append("name = %s")
            values.append(body.name)
        if body.params is not None:
            updates.append("params = %s")
            values.append(json.dumps(body.params))
        if body.suppressed is not None:
            updates.append("suppressed = %s")
            values.append(body.suppressed)
        if not updates:
            raise HTTPException(400, "No fields to update")

        updates.append("updated_at = now()")
        values.extend([feature_id, part_id])

        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE features SET {', '.join(updates)} WHERE id = %s AND part_id = %s",
                values,
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Feature not found")
        conn.commit()

        branch_id = _resolve_branch_id(conn, part_id, user)
        rebuild_result = _do_rebuild(part_id, conn, branch_id=branch_id)
        _auto_version_checkpoint(conn, part_id, f"Update feature", user_id=get_user_id_for_request(user))
        return rebuild_result
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.delete("/parts/{part_id}/features/{feature_id}")
async def delete_feature(part_id: int, feature_id: int, user: UserInfo = Depends(get_current_user_optional)):
    conn = get_connection()
    try:
        branch_id = _resolve_branch_id(conn, part_id, user)
        with conn.cursor() as cur:
            cur.execute("SELECT sequence FROM features WHERE id = %s AND part_id = %s", (feature_id, part_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Feature not found")
            deleted_seq = row[0]
            cur.execute("DELETE FROM features WHERE id = %s", (feature_id,))
            # Re-sequence (scoped to branch)
            cur.execute(
                "UPDATE features SET sequence = sequence - 1 WHERE part_id = %s AND branch_id = %s AND sequence > %s",
                (part_id, branch_id, deleted_seq),
            )
        conn.commit()

        rebuild_result = _do_rebuild(part_id, conn, branch_id=branch_id)
        _auto_version_checkpoint(conn, part_id, f"Delete feature", user_id=get_user_id_for_request(user))
        return rebuild_result
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Feature operations
# ---------------------------------------------------------------------------

@router.post("/parts/{part_id}/features/reorder")
async def reorder_features(part_id: int, body: FeatureReorder):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for i, fid in enumerate(body.feature_ids, 1):
                cur.execute(
                    "UPDATE features SET sequence = %s, updated_at = now() WHERE id = %s AND part_id = %s",
                    (i, fid, part_id),
                )
        conn.commit()
        rebuild_result = _do_rebuild(part_id, conn)
        return rebuild_result
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.post("/parts/{part_id}/features/{feature_id}/suppress")
async def suppress_feature(part_id: int, feature_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE features SET suppressed = true, updated_at = now() WHERE id = %s AND part_id = %s",
                (feature_id, part_id),
            )
        conn.commit()
        return _do_rebuild(part_id, conn)
    finally:
        put_connection(conn)


@router.post("/parts/{part_id}/features/{feature_id}/unsuppress")
async def unsuppress_feature(part_id: int, feature_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE features SET suppressed = false, updated_at = now() WHERE id = %s AND part_id = %s",
                (feature_id, part_id),
            )
        conn.commit()
        return _do_rebuild(part_id, conn)
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------

@router.post("/parts/{part_id}/rebuild")
async def rebuild_part(part_id: int, up_to_sequence: Optional[int] = Query(None)):
    conn = get_connection()
    try:
        return _do_rebuild(part_id, conn, up_to_sequence)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.get("/parts/{part_id}/preview/{sequence}")
async def preview_at_sequence(part_id: int, sequence: int):
    conn = get_connection()
    try:
        return _do_rebuild(part_id, conn, up_to_seq=sequence)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Feature schema catalog
# ---------------------------------------------------------------------------

@router.get("/features/schemas")
async def get_feature_schemas():
    return FEATURE_SCHEMAS


# ---------------------------------------------------------------------------
# Parameter CRUD
# ---------------------------------------------------------------------------

@router.get("/parts/{part_id}/parameters")
async def list_parameters(part_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, expression, value, description, unit, group_name, sort_order
                FROM parameters WHERE part_id = %s ORDER BY sort_order, id
            """, (part_id,))
            return [
                {"id": r[0], "name": r[1], "expression": r[2], "value": r[3],
                 "description": r[4], "unit": r[5], "group_name": r[6], "sort_order": r[7]}
                for r in cur.fetchall()
            ]
    finally:
        put_connection(conn)


@router.post("/parts/{part_id}/parameters")
async def create_parameter(part_id: int, body: ParameterCreate):
    conn = get_connection()
    try:
        # Evaluate expression to get value
        from feature_engine import ParameterResolver
        resolver = ParameterResolver(_load_named_params(conn, part_id))
        try:
            value = resolver.evaluate(body.expression)
        except Exception:
            value = None

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO parameters (part_id, name, expression, value, description, unit, group_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (part_id, body.name, body.expression, value, body.description, body.unit, body.group_name))
            param_id = cur.fetchone()[0]
        conn.commit()
        return {"id": param_id, "name": body.name, "expression": body.expression,
                "value": value, "unit": body.unit, "group_name": body.group_name}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.put("/parts/{part_id}/parameters/{param_id}")
async def update_parameter(part_id: int, param_id: int, body: ParameterUpdate):
    conn = get_connection()
    try:
        updates = []
        values = []
        if body.expression is not None:
            updates.append("expression = %s")
            values.append(body.expression)
            # Re-evaluate
            from feature_engine import ParameterResolver
            resolver = ParameterResolver(_load_named_params(conn, part_id))
            try:
                val = resolver.evaluate(body.expression)
                updates.append("value = %s")
                values.append(val)
            except Exception:
                pass
        if body.description is not None:
            updates.append("description = %s")
            values.append(body.description)
        if body.unit is not None:
            updates.append("unit = %s")
            values.append(body.unit)
        if body.group_name is not None:
            updates.append("group_name = %s")
            values.append(body.group_name)
        if not updates:
            raise HTTPException(400, "No fields to update")

        updates.append("updated_at = now()")
        values.extend([param_id, part_id])
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE parameters SET {', '.join(updates)} WHERE id = %s AND part_id = %s",
                values,
            )
        conn.commit()

        # Rebuild part since parameters changed
        rebuild_result = _do_rebuild(part_id, conn)
        return rebuild_result
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.delete("/parts/{part_id}/parameters/{param_id}")
async def delete_parameter(part_id: int, param_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM parameters WHERE id = %s AND part_id = %s", (param_id, part_id))
        conn.commit()
        return {"deleted": True}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Sketch CRUD
# ---------------------------------------------------------------------------

class SketchCreate(BaseModel):
    name: str = "Sketch"
    plane_type: str = "XY"
    plane_origin: list = [0, 0, 0]
    plane_normal: list = [0, 0, 1]
    entities: list = []

class SketchUpdate(BaseModel):
    entities: Optional[list] = None
    name: Optional[str] = None
    plane_type: Optional[str] = None


@router.get("/parts/{part_id}/sketches")
async def list_sketches(part_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, plane_type, plane_origin, plane_normal, entities, dof, solver_status
                FROM sketches WHERE part_id = %s ORDER BY id
            """, (part_id,))
            return [{"id": r[0], "name": r[1], "plane_type": r[2],
                     "plane_origin": r[3], "plane_normal": r[4],
                     "entities": r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]"),
                     "dof": r[6], "solver_status": r[7]}
                    for r in cur.fetchall()]
    finally:
        put_connection(conn)


@router.post("/parts/{part_id}/sketches")
async def create_sketch(part_id: int, body: SketchCreate):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sketches (part_id, name, plane_type, plane_origin, plane_normal, entities)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (part_id, body.name, body.plane_type,
                  json.dumps(body.plane_origin), json.dumps(body.plane_normal),
                  json.dumps(body.entities)))
            sketch_id = cur.fetchone()[0]
        conn.commit()
        return {"id": sketch_id, "name": body.name, "plane_type": body.plane_type, "entities": body.entities}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.put("/parts/{part_id}/sketches/{sketch_id}")
async def update_sketch(part_id: int, sketch_id: int, body: SketchUpdate):
    conn = get_connection()
    try:
        updates = []
        values = []
        if body.entities is not None:
            updates.append("entities = %s")
            values.append(json.dumps(body.entities))
        if body.name is not None:
            updates.append("name = %s")
            values.append(body.name)
        if body.plane_type is not None:
            updates.append("plane_type = %s")
            values.append(body.plane_type)
        if not updates:
            raise HTTPException(400, "Nothing to update")
        updates.append("updated_at = now()")
        values.extend([sketch_id, part_id])
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE sketches SET {', '.join(updates)} WHERE id = %s AND part_id = %s",
                values
            )
        conn.commit()
        return {"updated": True, "sketch_id": sketch_id}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


@router.delete("/parts/{part_id}/sketches/{sketch_id}")
async def delete_sketch(part_id: int, sketch_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sketches WHERE id = %s AND part_id = %s", (sketch_id, part_id))
        conn.commit()
        return {"deleted": True}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, str(exc))
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# AI-driven feature modification
# ---------------------------------------------------------------------------

@router.post("/parts/{part_id}/ai-modify")
async def ai_modify_part(part_id: int, body: dict = Body(...)):
    """AI-driven modification of a feature-tree part."""
    from ai_feature_bridge import ai_modify_feature_part

    user_message = body.get("message", "")
    conversation = body.get("conversation", [])

    if not user_message:
        raise HTTPException(400, "message is required")

    result = ai_modify_feature_part(part_id, user_message, conversation)

    if result.get("error"):
        raise HTTPException(500, result["error"])

    return result


# ---------------------------------------------------------------------------
# Direct Modeling endpoints — B-Rep face operations (no feature tree needed)
# ---------------------------------------------------------------------------

@router.post("/parts/{part_id}/direct/push-pull")
async def direct_push_pull(part_id: int, body: dict = Body(...)):
    """Push/pull a face along its normal."""
    from direct_modeling import push_pull_face
    return _apply_direct_op(part_id, push_pull_face, body, "push_pull")

@router.post("/parts/{part_id}/direct/move-face")
async def direct_move_face(part_id: int, body: dict = Body(...)):
    """Move a face in a direction."""
    from direct_modeling import move_face
    return _apply_direct_op(part_id, move_face, body, "move_face")

@router.post("/parts/{part_id}/direct/offset-face")
async def direct_offset_face(part_id: int, body: dict = Body(...)):
    """Offset (grow/shrink) a face."""
    from direct_modeling import offset_face
    return _apply_direct_op(part_id, offset_face, body, "offset_face")

@router.post("/parts/{part_id}/direct/delete-face")
async def direct_delete_face(part_id: int, body: dict = Body(...)):
    """Delete a face and heal the solid."""
    from direct_modeling import delete_face
    return _apply_direct_op(part_id, delete_face, body, "delete_face")


def _apply_direct_op(part_id: int, op_func, body: dict, op_name: str) -> dict:
    """
    Shared helper for direct modeling operations:
    1. Load the part's current shape (from script or feature tree)
    2. Apply the operation
    3. Tessellate the result
    4. Update the mesh_cache in DB
    5. Return the new mesh
    """
    from executor import shape_to_topo_mesh, extract_bounding_box, execute_script
    from direct_modeling import shape_to_step_bytes, step_bytes_to_shape

    face_index = body.get("face_index", 0)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cadquery_script, feature_tree_mode, step_data FROM parts WHERE id = %s",
                (part_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Part not found")

            script, feature_tree_mode, step_data = row

            # Get the current shape — PRIORITY ORDER:
            # 1. step_data (persisted from previous direct edit or import)
            # 2. Feature-tree rebuild (check for actual features, not just the flag)
            # 3. CadQuery script execution
            if step_data:
                raw = bytes(step_data) if not isinstance(step_data, bytes) else step_data
                wp = step_bytes_to_shape(raw)
            else:
                # No STEP data — rebuild from features or script
                features = _load_features(conn, part_id)
                if features:
                    named_params = _load_named_params(conn, part_id)
                    rebuild_result = rebuild_part_from_features(features, named_params)
                    wp = rebuild_result.get("shape")
                    if wp is None:
                        raise HTTPException(400, "Feature rebuild produced no shape")
                    # Save STEP data NOW so next direct edit loads instantly
                    try:
                        sb = shape_to_step_bytes(wp)
                        cur.execute("UPDATE parts SET step_data = %s WHERE id = %s", (sb, part_id))
                        conn.commit()
                    except Exception:
                        pass
                else:
                    wp = execute_script(script)

            # Apply the direct modeling operation
            kwargs = {"workplane": wp, "face_index": face_index}

            if op_name == "push_pull":
                kwargs["distance"] = body.get("distance", 5.0)
                kwargs["direction_vec"] = body.get("direction_vec")
            elif op_name == "move_face":
                kwargs["direction"] = body.get("direction", [0, 0, 1])
                kwargs["distance"] = body.get("distance", 5.0)
            elif op_name == "offset_face":
                kwargs["offset"] = body.get("offset", 2.0)

            result_wp = op_func(**kwargs)

            # Tessellate
            quality = body.get("quality", "preview")
            mesh = shape_to_topo_mesh(result_wp, quality=quality)
            bbox = extract_bounding_box(result_wp)

            # Persist: save modified shape as STEP + update mesh cache
            step_bytes = shape_to_step_bytes(result_wp)

            cur.execute("""
                UPDATE parts SET
                    mesh_cache = %s,
                    step_data = %s,
                    bbox_min_x = %s, bbox_min_y = %s, bbox_min_z = %s,
                    bbox_max_x = %s, bbox_max_y = %s, bbox_max_z = %s,
                    updated_at = now()
                WHERE id = %s
            """, (
                json.dumps(mesh),
                step_bytes,
                bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                part_id,
            ))
        conn.commit()

        return {
            "success": True,
            "part_id": part_id,
            "operation": op_name,
            "mesh": mesh,
            "bbox": bbox,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        import traceback; traceback.print_exc()
        raise HTTPException(500, f"Direct modeling failed: {e}")
    finally:
        put_connection(conn)
