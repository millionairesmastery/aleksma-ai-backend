from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from db import get_connection, put_connection


BASELINE_OBJECT_KNOWLEDGE = [
    {
        "object_type": "wheel",
        "title": "Road Alloy Rim Fundamentals",
        "summary": (
            "Passenger-car alloy rims: hub + spoke network + rim barrel. "
            "ALL parts aligned on Z-axis. Barrel via revolve in XZ plane around (0,0,1). "
            "Hub as XY cylinder. Spokes as extruded trapezoids in XY plane. "
            "No visualization parts — bolt holes and bore are CUT into the hub."
        ),
        "dimensions": {
            "rim_diameter_in": 18,
            "rim_width_mm": 215,
            "spoke_count": 5,
            "pcd_mm": 114.3,
            "hub_bore_mm": 66.1,
        },
        "components": ["barrel", "hub", "spokes"],
        "generation_rules": [
            "COORDINATE SYSTEM: Wheel axis = Z. In XZ workplane, local Y = global Z.",
            "Barrel: build XZ profile as (radius, z_position) polyline, revolve via .revolve(360, (0,0,0), (0,1,0)).",
            "Hub: cq.Workplane('XY').cylinder(thickness, radius) — cut center bore and bolt holes into it.",
            "Spokes: extruded trapezoids in XY plane, NOT lofted. Use .moveTo/.lineTo for profile, .extrude for thickness.",
            "Offset spokes by half-bolt-spacing so they sit BETWEEN bolt holes.",
            "NEVER create visualization-only parts (solid cylinders for bolt holes, bore viz, drop center ring).",
            "Only 3 types of parts allowed: 'Hub', 'Barrel', 'Spoke_1'...'Spoke_N'.",
            "Connect spokes cleanly from hub region to barrel without floating geometry.",
        ],
        "validation_rules": [
            "Reject wheels with spokes but no hub or rim barrel.",
            "Reject disconnected spoke geometry.",
            "Reject wheel requests that accidentally create tire-like outer rubber geometry.",
            "Reject visualization-only parts (bolt hole cylinders, bore cylinders, well rings).",
        ],
        "example_prompt": "Create an 18 inch 5-spoke alloy rim with 5x114.3 bolt pattern and 66.1 mm center bore.",
        "tags": ["wheel", "rim", "alloy", "automotive"],
        "source": "curated",
        "confidence": 0.92,
    },
    {
        "object_type": "tire",
        "title": "Road Tire Profile Guidance",
        "summary": (
            "Road tires have a D-shaped cross-section: flat tread on top, curved sidewalls, flat inner bead. "
            "NOT a circular cross-section — that makes a balloon, not a tire. "
            "For hollow tires, revolve outer profile then subtract inner profile offset inward by wall thickness."
        ),
        "dimensions": {
            "section_width_mm": 225,
            "aspect_ratio_pct": 45,
            "rim_diameter_in": 17,
            "wall_thickness_mm": 4,
        },
        "components": ["body"],
        "generation_rules": [
            "Tire profile has 3 distinct zones: STRAIGHT sidewalls + ROUNDED shoulders + FLAT tread.",
            "Build on XZ plane: bead (rim_r, -half_w) -> lineTo up sidewall -> threePointArc shoulder -> lineTo FLAT tread across -> threePointArc shoulder -> lineTo down sidewall -> close.",
            "The tread is TWO lineTo segments at outer_r — it is FLAT, never curved. tread_hw = half_w * 0.78.",
            "Revolve the closed profile 360 degrees around (0,0),(0,1,0).",
            "For hollow tires, build inner profile offset inward by wall_thickness, revolve it, then outer.cut(inner).",
            "NEVER use .circle() for tire cross-section — that makes a balloon, not a tire.",
            "Only add tread detail when explicitly requested.",
        ],
        "validation_rules": [
            "Reject tire outputs that create separate overlapping tread, sidewall, or bead parts unless explicitly requested.",
            "Reject wheel spokes or hub features in pure tire requests.",
        ],
        "example_prompt": "Create a 255/60 R20 hollow road tire with 2 mm walls.",
        "tags": ["tire", "tyre", "automotive", "road"],
        "source": "curated",
        "confidence": 0.94,
    },
]


def ensure_knowledge_schema() -> None:
    """No-op: knowledge tables have been removed. Only engineering_references is kept."""
    pass


def _ensure_knowledge_schema_legacy() -> None:
    """Legacy — kept for reference only, not called."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS object_knowledge (
                    id SERIAL PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
                    components JSONB NOT NULL DEFAULT '[]'::jsonb,
                    generation_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                    validation_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                    example_prompt TEXT,
                    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source TEXT NOT NULL DEFAULT 'curated',
                    status TEXT NOT NULL DEFAULT 'active',
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.7,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(object_type, title)
                );
                CREATE INDEX IF NOT EXISTS idx_object_knowledge_search ON object_knowledge
                    USING gin(to_tsvector('english', object_type || ' ' || title || ' ' || summary || ' ' || coalesce(example_prompt, '')));

                CREATE TABLE IF NOT EXISTS generation_patterns (
                    id SERIAL PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    script TEXT NOT NULL,
                    part_names JSONB NOT NULL DEFAULT '[]'::jsonb,
                    source TEXT NOT NULL DEFAULT 'generation',
                    quality_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                    success_count INT NOT NULL DEFAULT 1,
                    reuse_count INT NOT NULL DEFAULT 0,
                    script_hash TEXT UNIQUE NOT NULL,
                    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_generation_patterns_search ON generation_patterns
                    USING gin(to_tsvector('english', object_type || ' ' || prompt_text));

                CREATE TABLE IF NOT EXISTS generation_feedback (
                    id SERIAL PRIMARY KEY,
                    project_id INT REFERENCES projects(id) ON DELETE SET NULL,
                    assembly_id INT REFERENCES assemblies(id) ON DELETE SET NULL,
                    user_query TEXT NOT NULL,
                    object_type TEXT,
                    spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    plan_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    script TEXT,
                    part_names JSONB NOT NULL DEFAULT '[]'::jsonb,
                    success BOOLEAN NOT NULL DEFAULT false,
                    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
                    reference_matches JSONB NOT NULL DEFAULT '[]'::jsonb,
                    knowledge_matches JSONB NOT NULL DEFAULT '[]'::jsonb,
                    pattern_matches JSONB NOT NULL DEFAULT '[]'::jsonb,
                    failure_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS draft_object_knowledge (
                    id SERIAL PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
                    components JSONB NOT NULL DEFAULT '[]'::jsonb,
                    generation_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                    validation_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                    example_prompt TEXT,
                    source_feedback_id INT REFERENCES generation_feedback(id) ON DELETE SET NULL,
                    source_pattern_id INT REFERENCES generation_patterns(id) ON DELETE SET NULL,
                    source TEXT NOT NULL DEFAULT 'auto-promoted',
                    status TEXT NOT NULL DEFAULT 'draft',
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.65,
                    prompt_hash TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_draft_object_knowledge_search ON draft_object_knowledge
                    USING gin(to_tsvector('english', object_type || ' ' || title || ' ' || summary || ' ' || coalesce(example_prompt, '')));
                """
            )

            for entry in BASELINE_OBJECT_KNOWLEDGE:
                cur.execute(
                    """
                    INSERT INTO object_knowledge
                        (object_type, title, summary, dimensions, components,
                         generation_rules, validation_rules, example_prompt, tags,
                         source, status, confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
                    ON CONFLICT (object_type, title) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        dimensions = EXCLUDED.dimensions,
                        components = EXCLUDED.components,
                        generation_rules = EXCLUDED.generation_rules,
                        validation_rules = EXCLUDED.validation_rules,
                        example_prompt = EXCLUDED.example_prompt,
                        tags = EXCLUDED.tags,
                        source = EXCLUDED.source,
                        confidence = EXCLUDED.confidence,
                        updated_at = now()
                    """,
                    (
                        entry["object_type"],
                        entry["title"],
                        entry["summary"],
                        json.dumps(entry["dimensions"]),
                        json.dumps(entry["components"]),
                        json.dumps(entry["generation_rules"]),
                        json.dumps(entry["validation_rules"]),
                        entry["example_prompt"],
                        json.dumps(entry["tags"]),
                        entry["source"],
                        entry["confidence"],
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def _normalize_json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def search_object_knowledge(query: str, object_type: Optional[str] = None, limit: int = 3) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if query.strip():
                if object_type:
                    cur.execute(
                        """
                        SELECT id, object_type, title, summary, dimensions, components,
                               generation_rules, validation_rules, example_prompt, tags,
                               source, confidence
                        FROM object_knowledge
                        WHERE status = 'active'
                          AND object_type = %s
                          AND to_tsvector('english', object_type || ' ' || title || ' ' || summary || ' ' || coalesce(example_prompt, ''))
                              @@ plainto_tsquery('english', %s)
                        ORDER BY confidence DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (object_type, query, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, object_type, title, summary, dimensions, components,
                               generation_rules, validation_rules, example_prompt, tags,
                               source, confidence
                        FROM object_knowledge
                        WHERE status = 'active'
                          AND to_tsvector('english', object_type || ' ' || title || ' ' || summary || ' ' || coalesce(example_prompt, ''))
                              @@ plainto_tsquery('english', %s)
                        ORDER BY confidence DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (query, limit),
                    )
            else:
                if object_type:
                    cur.execute(
                        """
                        SELECT id, object_type, title, summary, dimensions, components,
                               generation_rules, validation_rules, example_prompt, tags,
                               source, confidence
                        FROM object_knowledge
                        WHERE status = 'active' AND object_type = %s
                        ORDER BY confidence DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (object_type, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, object_type, title, summary, dimensions, components,
                               generation_rules, validation_rules, example_prompt, tags,
                               source, confidence
                        FROM object_knowledge
                        WHERE status = 'active'
                        ORDER BY confidence DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )

            rows = cur.fetchall()
            results = []
            for row in rows:
                results.append(
                    {
                        "id": row[0],
                        "object_type": row[1],
                        "title": row[2],
                        "summary": row[3],
                        "dimensions": _normalize_json_value(row[4], {}),
                        "components": _normalize_json_value(row[5], []),
                        "generation_rules": _normalize_json_value(row[6], []),
                        "validation_rules": _normalize_json_value(row[7], []),
                        "example_prompt": row[8],
                        "tags": _normalize_json_value(row[9], []),
                        "source": row[10],
                        "confidence": row[11],
                    }
                )
            return results
    finally:
        put_connection(conn)


def search_generation_patterns(query: str, object_type: Optional[str] = None, limit: int = 2) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if query.strip():
                if object_type:
                    cur.execute(
                        """
                        SELECT id, object_type, prompt_text, spec_json, script, part_names,
                               quality_score, success_count, reuse_count
                        FROM generation_patterns
                        WHERE object_type = %s
                          AND to_tsvector('english', object_type || ' ' || prompt_text)
                              @@ plainto_tsquery('english', %s)
                        ORDER BY quality_score DESC, success_count DESC, last_used_at DESC
                        LIMIT %s
                        """,
                        (object_type, query, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, object_type, prompt_text, spec_json, script, part_names,
                               quality_score, success_count, reuse_count
                        FROM generation_patterns
                        WHERE to_tsvector('english', object_type || ' ' || prompt_text)
                              @@ plainto_tsquery('english', %s)
                        ORDER BY quality_score DESC, success_count DESC, last_used_at DESC
                        LIMIT %s
                        """,
                        (query, limit),
                    )
            else:
                if object_type:
                    cur.execute(
                        """
                        SELECT id, object_type, prompt_text, spec_json, script, part_names,
                               quality_score, success_count, reuse_count
                        FROM generation_patterns
                        WHERE object_type = %s
                        ORDER BY quality_score DESC, success_count DESC, last_used_at DESC
                        LIMIT %s
                        """,
                        (object_type, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, object_type, prompt_text, spec_json, script, part_names,
                               quality_score, success_count, reuse_count
                        FROM generation_patterns
                        ORDER BY quality_score DESC, success_count DESC, last_used_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )

            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "object_type": row[1],
                    "prompt_text": row[2],
                    "spec_json": _normalize_json_value(row[3], {}),
                    "script": row[4],
                    "part_names": _normalize_json_value(row[5], []),
                    "quality_score": row[6],
                    "success_count": row[7],
                    "reuse_count": row[8],
                }
                for row in rows
            ]
    finally:
        put_connection(conn)


def list_object_knowledge(object_type: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            clauses = []
            params: list[Any] = []
            if object_type:
                clauses.append("object_type = %s")
                params.append(object_type)
            if status:
                clauses.append("status = %s")
                params.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"""
                SELECT id, object_type, title, summary, dimensions, components, generation_rules,
                       validation_rules, example_prompt, tags, source, status, confidence,
                       created_at, updated_at
                FROM object_knowledge
                {where}
                ORDER BY confidence DESC, updated_at DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "object_type": row[1],
                    "title": row[2],
                    "summary": row[3],
                    "dimensions": _normalize_json_value(row[4], {}),
                    "components": _normalize_json_value(row[5], []),
                    "generation_rules": _normalize_json_value(row[6], []),
                    "validation_rules": _normalize_json_value(row[7], []),
                    "example_prompt": row[8],
                    "tags": _normalize_json_value(row[9], []),
                    "source": row[10],
                    "status": row[11],
                    "confidence": row[12],
                    "created_at": str(row[13]),
                    "updated_at": str(row[14]),
                }
                for row in rows
            ]
    finally:
        put_connection(conn)


def list_generation_patterns(object_type: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if object_type:
                cur.execute(
                    """
                    SELECT id, object_type, prompt_text, spec_json, script, part_names,
                           source, quality_score, success_count, reuse_count, last_used_at, created_at
                    FROM generation_patterns
                    WHERE object_type = %s
                    ORDER BY quality_score DESC, success_count DESC, last_used_at DESC
                    LIMIT %s
                    """,
                    (object_type, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, object_type, prompt_text, spec_json, script, part_names,
                           source, quality_score, success_count, reuse_count, last_used_at, created_at
                    FROM generation_patterns
                    ORDER BY quality_score DESC, success_count DESC, last_used_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "object_type": row[1],
                    "prompt_text": row[2],
                    "spec_json": _normalize_json_value(row[3], {}),
                    "script": row[4],
                    "part_names": _normalize_json_value(row[5], []),
                    "source": row[6],
                    "quality_score": row[7],
                    "success_count": row[8],
                    "reuse_count": row[9],
                    "last_used_at": str(row[10]),
                    "created_at": str(row[11]),
                }
                for row in rows
            ]
    finally:
        put_connection(conn)


def list_generation_feedback(object_type: Optional[str] = None, success: Optional[bool] = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            clauses = []
            params: list[Any] = []
            if object_type:
                clauses.append("object_type = %s")
                params.append(object_type)
            if success is not None:
                clauses.append("success = %s")
                params.append(success)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"""
                SELECT id, project_id, assembly_id, user_query, object_type, spec_json, plan_json, script,
                       part_names, success, validation_errors, reference_matches, knowledge_matches,
                       pattern_matches, failure_reason, created_at
                FROM generation_feedback
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "project_id": row[1],
                    "assembly_id": row[2],
                    "user_query": row[3],
                    "object_type": row[4],
                    "spec_json": _normalize_json_value(row[5], {}),
                    "plan_json": _normalize_json_value(row[6], {}),
                    "script": row[7],
                    "part_names": _normalize_json_value(row[8], []),
                    "success": row[9],
                    "validation_errors": _normalize_json_value(row[10], []),
                    "reference_matches": _normalize_json_value(row[11], []),
                    "knowledge_matches": _normalize_json_value(row[12], []),
                    "pattern_matches": _normalize_json_value(row[13], []),
                    "failure_reason": row[14],
                    "created_at": str(row[15]),
                }
                for row in rows
            ]
    finally:
        put_connection(conn)


def list_draft_object_knowledge(object_type: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            clauses = []
            params: list[Any] = []
            if object_type:
                clauses.append("object_type = %s")
                params.append(object_type)
            if status:
                clauses.append("status = %s")
                params.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(
                f"""
                SELECT id, object_type, title, summary, dimensions, components, generation_rules,
                       validation_rules, example_prompt, source_feedback_id, source_pattern_id,
                       source, status, confidence, created_at, updated_at
                FROM draft_object_knowledge
                {where}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "object_type": row[1],
                    "title": row[2],
                    "summary": row[3],
                    "dimensions": _normalize_json_value(row[4], {}),
                    "components": _normalize_json_value(row[5], []),
                    "generation_rules": _normalize_json_value(row[6], []),
                    "validation_rules": _normalize_json_value(row[7], []),
                    "example_prompt": row[8],
                    "source_feedback_id": row[9],
                    "source_pattern_id": row[10],
                    "source": row[11],
                    "status": row[12],
                    "confidence": row[13],
                    "created_at": str(row[14]),
                    "updated_at": str(row[15]),
                }
                for row in rows
            ]
    finally:
        put_connection(conn)


def mark_patterns_reused(pattern_ids: list[int]) -> None:
    ids = [int(pid) for pid in pattern_ids if pid is not None]
    if not ids:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE generation_patterns
                SET reuse_count = reuse_count + 1,
                    last_used_at = now()
                WHERE id = ANY(%s)
                """,
                (ids,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def promote_draft_object_knowledge(draft_id: int) -> Optional[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT object_type, title, summary, dimensions, components, generation_rules,
                       validation_rules, example_prompt, confidence
                FROM draft_object_knowledge
                WHERE id = %s
                """,
                (draft_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            cur.execute(
                """
                INSERT INTO object_knowledge
                    (object_type, title, summary, dimensions, components, generation_rules,
                     validation_rules, example_prompt, source, status, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'promoted-draft', 'active', %s)
                ON CONFLICT (object_type, title) DO UPDATE SET
                    summary = EXCLUDED.summary,
                    dimensions = EXCLUDED.dimensions,
                    components = EXCLUDED.components,
                    generation_rules = EXCLUDED.generation_rules,
                    validation_rules = EXCLUDED.validation_rules,
                    example_prompt = EXCLUDED.example_prompt,
                    confidence = GREATEST(object_knowledge.confidence, EXCLUDED.confidence),
                    updated_at = now()
                """,
                (
                    row[0],
                    row[1],
                    row[2],
                    json.dumps(_normalize_json_value(row[3], {})),
                    json.dumps(_normalize_json_value(row[4], [])),
                    json.dumps(_normalize_json_value(row[5], [])),
                    json.dumps(_normalize_json_value(row[6], [])),
                    row[7],
                    row[8],
                ),
            )
            cur.execute(
                "UPDATE draft_object_knowledge SET status = 'promoted', updated_at = now() WHERE id = %s",
                (draft_id,),
            )
            cur.execute(
                """
                SELECT id, object_type, title, summary, dimensions, components, generation_rules,
                       validation_rules, example_prompt, source_feedback_id, source_pattern_id,
                       source, status, confidence, created_at, updated_at
                FROM draft_object_knowledge
                WHERE id = %s
                """,
                (draft_id,),
            )
            promoted_row = cur.fetchone()
        conn.commit()
        if not promoted_row:
            return {"id": draft_id, "status": "promoted"}
        return {
            "id": promoted_row[0],
            "object_type": promoted_row[1],
            "title": promoted_row[2],
            "summary": promoted_row[3],
            "dimensions": _normalize_json_value(promoted_row[4], {}),
            "components": _normalize_json_value(promoted_row[5], []),
            "generation_rules": _normalize_json_value(promoted_row[6], []),
            "validation_rules": _normalize_json_value(promoted_row[7], []),
            "example_prompt": promoted_row[8],
            "source_feedback_id": promoted_row[9],
            "source_pattern_id": promoted_row[10],
            "source": promoted_row[11],
            "status": promoted_row[12],
            "confidence": promoted_row[13],
            "created_at": str(promoted_row[14]),
            "updated_at": str(promoted_row[15]),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


def record_generation_feedback(
    *,
    project_id: Optional[int],
    assembly_id: Optional[int],
    user_query: str,
    object_type: Optional[str],
    spec: dict[str, Any],
    plan: dict[str, Any],
    script: Optional[str],
    part_names: list[str],
    success: bool,
    validation_errors: list[str],
    reference_matches: list[str],
    knowledge_matches: list[str],
    pattern_matches: list[int],
    failure_reason: Optional[str],
) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO generation_feedback
                    (project_id, assembly_id, user_query, object_type, spec_json, plan_json,
                     script, part_names, success, validation_errors, reference_matches,
                     knowledge_matches, pattern_matches, failure_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    project_id,
                    assembly_id,
                    user_query,
                    object_type,
                    json.dumps(spec or {}),
                    json.dumps(plan or {}),
                    script,
                    json.dumps(part_names or []),
                    success,
                    json.dumps(validation_errors or []),
                    json.dumps(reference_matches or []),
                    json.dumps(knowledge_matches or []),
                    json.dumps(pattern_matches or []),
                    failure_reason,
                ),
            )
            feedback_id = cur.fetchone()[0]

            if success and script and user_query.strip():
                script_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
                prompt_hash = hashlib.sha256(
                    f"{object_type or 'custom_part'}::{user_query.strip().lower()}".encode("utf-8")
                ).hexdigest()
                quality_score = 0.6
                if object_type and object_type != "custom_part":
                    quality_score += 0.1
                quality_score += min(len(part_names or []), 5) * 0.04
                if not validation_errors:
                    quality_score += 0.1
                quality_score = min(0.95, quality_score)

                cur.execute(
                    """
                    INSERT INTO generation_patterns
                        (object_type, prompt_text, spec_json, script, part_names,
                         source, quality_score, success_count, reuse_count, script_hash)
                    VALUES (%s, %s, %s, %s, %s, 'generation', %s, 1, 0, %s)
                    ON CONFLICT (script_hash) DO UPDATE SET
                        prompt_text = EXCLUDED.prompt_text,
                        spec_json = EXCLUDED.spec_json,
                        part_names = EXCLUDED.part_names,
                        quality_score = GREATEST(generation_patterns.quality_score, EXCLUDED.quality_score),
                        success_count = generation_patterns.success_count + 1,
                        last_used_at = now()
                    """,
                    (
                        object_type or "custom_part",
                        user_query,
                        json.dumps(spec or {}),
                        script,
                        json.dumps(part_names or []),
                        quality_score,
                        script_hash,
                    ),
                )

                if object_type and object_type != "custom_part" and quality_score >= 0.78:
                    title = f"Learned {object_type.title()} Pattern: {user_query.strip()[:72]}"
                    summary = (
                        f"Auto-promoted draft from a successful {object_type} generation. "
                        f"Prompt: {user_query.strip()}"
                    )
                    generation_rules = [
                        f"Use this as a successful reference pattern for {object_type} prompts.",
                        "Preserve the high-level proportioning and connected geometry strategy that made this generation valid.",
                    ]
                    validation_rules = [
                        "Cross-check future generations against this successful pattern before accepting them.",
                    ]
                    if plan.get("notes"):
                        generation_rules.extend(str(note) for note in plan["notes"][:4])

                    cur.execute(
                        """
                        INSERT INTO draft_object_knowledge
                            (object_type, title, summary, dimensions, components, generation_rules,
                             validation_rules, example_prompt, source_feedback_id, source,
                             confidence, prompt_hash)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'auto-promoted', %s, %s)
                        ON CONFLICT (prompt_hash) DO UPDATE SET
                            title = EXCLUDED.title,
                            summary = EXCLUDED.summary,
                            dimensions = EXCLUDED.dimensions,
                            components = EXCLUDED.components,
                            generation_rules = EXCLUDED.generation_rules,
                            validation_rules = EXCLUDED.validation_rules,
                            example_prompt = EXCLUDED.example_prompt,
                            source_feedback_id = EXCLUDED.source_feedback_id,
                            confidence = GREATEST(draft_object_knowledge.confidence, EXCLUDED.confidence),
                            updated_at = now()
                        """,
                        (
                            object_type,
                            title,
                            summary,
                            json.dumps((spec or {}).get("dimensions", {})),
                            json.dumps((spec or {}).get("components", []) or part_names or ["body"]),
                            json.dumps(generation_rules),
                            json.dumps(validation_rules),
                            user_query,
                            feedback_id,
                            min(0.9, quality_score),
                            prompt_hash,
                        ),
                    )

        conn.commit()
        return feedback_id
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)
