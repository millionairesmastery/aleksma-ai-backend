from __future__ import annotations

import hashlib
import json
import os
import struct
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import List, Optional

import anthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from db import init_db, close_db, get_connection, put_connection
from executor import (
    compute_volume,
    execute_script,
    execute_script_multi,
    export_assembly_step,
    export_brep,
    export_parts_stl_zip,
    export_step,
    export_stl,
    extract_bounding_box,
    find_face_at_point,
    shape_to_mesh,
    shape_to_topo_mesh,
)
from dfm import check_dfm, MATERIAL_DENSITIES
from context import build_assembly_context
from models import (
    AssemblyCreate,
    AssemblyResponse,
    ChatMessageResponse,
    OperationCreate,
    OperationResponse,
    OperationUpdate,
    ParameterUpdateRequest,
    ParametricPartCreate,
    ParametricUpdateRequest,
    PartCreate,
    PartResponse,
    PartUpdate,
    ProjectChatRequest,
    ProjectCreate,
    ProjectResponse,
)
from operations import Operation, get_param_schema, OPERATION_REGISTRY
from parametric import ParametricEngine, parse_script_to_operations
from parametric_templates import generate_from_template, get_template_schema, list_templates, TEMPLATE_REGISTRY
import template_library  # registers all templates + provides seed_templates
from spec_pipeline import build_engineering_spec
from assembly_planner import build_assembly_plan
from validator import validate_generation, build_retry_feedback
from collaboration import (
    get_or_create_room, remove_empty_room,
    handle_connect, handle_disconnect, handle_message,
    lock_part_for_ai, unlock_part_for_ai, broadcast_mesh_update,
)
from auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserInfo,
    register_user, login_user, refresh_access_token,
    get_current_user, get_current_user_optional,
)
from permissions import get_user_id_for_request, can_view_project, can_edit_project, is_org_member, is_org_admin
from knowledge import (
    ensure_knowledge_schema,
)

import json as _json
from feature_routes import router as feature_router
from parametric_ws import router as parametric_ws_router

load_dotenv()

# ---------------------------------------------------------------------------
# App with lifespan
# ---------------------------------------------------------------------------


def _ensure_mesh_cache_columns():
    """Add mesh_cache / script_hash columns to parts if they don't exist yet."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE parts
                ADD COLUMN IF NOT EXISTS script_hash TEXT,
                ADD COLUMN IF NOT EXISTS mesh_cache  JSONB
            """)
            # Chat messages are per-user — add user_id if missing
            cur.execute("""
                ALTER TABLE chat_messages
                ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE SET NULL
            """)
            # AI interaction log — saves full prompt/response for every Claude call
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_interactions (
                    id              SERIAL PRIMARY KEY,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    interaction_type TEXT NOT NULL,
                    part_id         INT REFERENCES parts(id) ON DELETE SET NULL,
                    assembly_id     INT REFERENCES assemblies(id) ON DELETE SET NULL,
                    branch_id       INT,
                    user_id         INT REFERENCES users(id) ON DELETE SET NULL,
                    instruction     TEXT,
                    system_prompt   TEXT,
                    user_message    TEXT,
                    raw_response    TEXT,
                    parsed_result   JSONB,
                    applied_ops     JSONB,
                    errors          JSONB,
                    model           TEXT,
                    duration_ms     INT,
                    success         BOOLEAN NOT NULL DEFAULT true
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_interactions_part
                ON ai_interactions(part_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_interactions_created
                ON ai_interactions(created_at DESC)
            """)
        conn.commit()
    finally:
        put_connection(conn)


def _log_ai_interaction(conn, *, interaction_type: str, instruction: str = None,
                        part_id: int = None, assembly_id: int = None,
                        branch_id: int = None, user_id: int = None,
                        system_prompt: str = None, user_message: str = None,
                        raw_response: str = None, parsed_result=None,
                        applied_ops=None, errors=None, model: str = None,
                        duration_ms: int = None, success: bool = True):
    """Save an AI interaction to the database for audit/review."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ai_interactions
                   (interaction_type, part_id, assembly_id, branch_id, user_id,
                    instruction, system_prompt, user_message, raw_response,
                    parsed_result, applied_ops, errors, model, duration_ms, success)
                   VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s,%s)""",
                (interaction_type, part_id, assembly_id, branch_id, user_id,
                 instruction, system_prompt, user_message, raw_response,
                 _json.dumps(parsed_result) if parsed_result else None,
                 _json.dumps(applied_ops) if applied_ops else None,
                 _json.dumps(errors) if errors else None,
                 model, duration_ms, success),
            )
        conn.commit()
    except Exception as e:
        print(f"[AI-LOG] Failed to save interaction: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


def _script_hash(script: str) -> str:
    return hashlib.sha256(script.encode()).hexdigest()


# ---------------------------------------------------------------------------
# In-memory LRU mesh cache — avoids DB roundtrip for hot parts
# ---------------------------------------------------------------------------
class _MeshLRU:
    """Thread-safe LRU cache mapping (part_id, script_hash) → mesh dict."""
    def __init__(self, max_items: int = 256):
        self._cache: OrderedDict = OrderedDict()
        self._max = max_items

    def get(self, part_id: int, shash: str):
        key = (part_id, shash)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, part_id: int, shash: str, mesh: dict):
        key = (part_id, shash)
        self._cache[key] = mesh
        self._cache.move_to_end(key)
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)

    def invalidate(self, part_id: int):
        to_del = [k for k in self._cache if k[0] == part_id]
        for k in to_del:
            del self._cache[k]


_mesh_lru = _MeshLRU(max_items=256)


def _pack_binary_mesh(mesh: dict) -> bytes:
    """Pack mesh dict into a compact binary format.

    Layout:
      [4 bytes]  header_length (uint32 LE)
      [N bytes]  JSON header: {id, vertex_count, face_count, topo_faces, topo_edges}
      [V bytes]  Float32 positions (vertex_count * 3 floats)
      [F bytes]  Uint32 indices  (face_count * 3 uints)
    """
    import array

    verts = mesh["vertices"]
    faces = mesh["faces"]

    # Build flat typed arrays
    pos_flat = array.array("f")  # float32
    for v in verts:
        pos_flat.extend(v)

    idx_flat = array.array("I")  # uint32
    for f in faces:
        idx_flat.extend(f)

    # Header contains everything EXCEPT the big vertex/face arrays
    header = {
        "id": mesh.get("id"),
        "vertex_count": mesh["vertex_count"],
        "face_count": mesh["face_count"],
    }
    if mesh.get("topo_faces"):
        header["topo_faces"] = mesh["topo_faces"]
    if mesh.get("topo_edges"):
        header["topo_edges"] = mesh["topo_edges"]

    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    # Pad header to 4-byte alignment so Float32Array/Uint32Array can be
    # constructed directly from the buffer without copying.
    padding = (4 - len(header_bytes) % 4) % 4
    header_bytes += b"\x00" * padding
    header_len = len(header_bytes)

    buf = bytearray()
    buf.extend(struct.pack("<I", header_len))
    buf.extend(header_bytes)
    buf.extend(pos_flat.tobytes())
    buf.extend(idx_flat.tobytes())
    return bytes(buf)


# Thread pool for parallel mesh generation (CadQuery releases the GIL during OCC calls)
_mesh_executor = ThreadPoolExecutor(max_workers=6)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_knowledge_schema()
    _ensure_mesh_cache_columns()
    # Seed template library (safe to call multiple times)
    try:
        conn = get_connection()
        template_library.seed_templates(conn)
        put_connection(conn)
    except Exception as e:
        print(f"Template seeding skipped (run migrate.py first): {e}")
    yield
    close_db()


app = FastAPI(title="Aleksma AI", version="1.0.0", lifespan=lifespan)

_cors_raw = os.environ.get("CORS_ORIGINS", "*")
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()] if _cors_raw != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(feature_router)
app.include_router(parametric_ws_router)

_state: dict = {"workplane": None}

DEFAULT_USER_ID = 1

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """You are a senior mechanical engineer and CAD expert. You generate CadQuery Python scripts.

YOUR ENGINEERING PROCESS (follow this for every request):
1. THINK about what the object really is. Use your full engineering knowledge.
   - What are its real-world dimensions? Use accurate sizes, not guesses.
   - What sub-components does it have? Break it down completely.
   - What shapes make up each component? Plan the geometry.
   - How do the parts connect and fit together spatially?
2. BUILD each component as a separate named part with realistic proportions.
3. POSITION all parts so they assemble correctly — no floating pieces.

EXAMPLE: "F1 steering wheel" — you KNOW this is:
   - Rectangular carbon fiber body ~280x140x25mm (not circular like a car wheel)
   - Display screen cutout in the upper center
   - 6-8 buttons on each side, ~10mm dia
   - 2 paddle shifters behind, ~100x30mm
   - 2 rotary dials on the face
   - Grip handles on left and right sides, ~30mm diameter
   - Quick-release hub on the back, ~55mm dia
   You must use this kind of real knowledge for EVERY object.

OUTPUT RULES:
- Output ONLY raw Python code — no markdown, no fences, no prose
- First line: import cadquery as cq
- Units: millimeters. COMPLETE standalone script every time.
- Only use: cadquery (as cq), math (standard library)

MULTI-PART OUTPUT (ALWAYS use for objects with 2+ components):
  parts = {}
  body = cq.Workplane("XY").box(280, 140, 25)
  parts["Body"] = body
  grip_left = cq.Workplane("XY").cylinder(60, 15).translate((-150, 0, 0))
  parts["Left Grip"] = grip_left
Each value must be a cq.Workplane. Each becomes a separate selectable part.
Use this for ANYTHING that has distinct sub-parts.

COMPLEX OBJECTS (wheels, brake assemblies, engines, etc.) MUST use multi-part:
  - Each logical component gets its own entry in parts = {}
  - For wheels: Hub, Barrel, Front Lip, Back Lip, Spoke_1, Spoke_2, ... Spoke_N
  - For brakes: Disc, Hat, Caliper, Pad_Inner, Pad_Outer
  - This allows the user to select and edit each sub-part independently
  - Parts should physically connect/overlap where they meet

SPATIAL AWARENESS (you have no eyes — you MUST reason about collisions):
  - When placing repeated features (spokes, ribs, fins) around holes (bolts, bores),
    calculate angular positions and ensure features do NOT cover or block holes.
  - If n_spokes == n_bolts, offset spokes by half-a-bolt-spacing so they sit BETWEEN bolts.
  - Spoke inner edges must stop OUTSIDE the bolt circle (PCD + hole_radius + clearance).
  - Never let structural features (spokes, ribs) extend into the center bore area.
  - Think about what a human would see: can they access the bolt holes with a wrench?
    If your geometry blocks access, fix the angular offset or trim the feature.

For truly simple single objects (a box, a bolt), use: result = cq.Workplane(...)

PARAMETER DECLARATIONS (REQUIRED):
Always declare every key editable dimension as an UPPER_CASE variable at the very top of the script, one per line, before any geometry code:
  RIM_RADIUS = 228.6     # mm — bead seat radius
  SPOKE_COUNT = 5        # number of spokes
  WALL_THICKNESS = 3.0   # mm
Rules:
- Variable names: UPPER_CASE with underscores, minimum 3 chars (WALL_T not W)
- One variable per line, no chained assignments (not A = B = 10)
- Do NOT bury key dimensions inside expressions — always declare first, use below
- These become live-editable parameters users can tune without the AI

GEOMETRY TECHNIQUES (use the BEST tool for each shape):
- Boolean cut for holes/pockets: main.cut(tool_shape)
- Fillet for rounded edges: .edges(selector).fillet(r) — keep r < smallest dimension/3
- Shell for hollow parts: .shell(-thickness)
- Use math.sin/cos for circular arrays and angular positioning
- REVOLVE for round/axially-symmetric parts: barrel profiles, rings, hubs, bottles, vases
- LOFT between different cross-sections for tapered/shaped parts
- SWEEP along a path for curved features (pipes, handles)
- Use extrude for prismatic/straight features

REVOLVE RULES:
- Use revolve for ANY axially-symmetric part (wheels, barrels, bottles, shafts, pulleys)
- Profile must be a closed shape OFFSET from the revolution axis (never touching it)
- Build the cross-section profile with .moveTo() .lineTo() .close() then .revolve()
- Example barrel (Z-axis): cq.Workplane("XZ").moveTo(r_outer, w/2).lineTo(r_outer, -w/2).lineTo(r_inner, -w/2).lineTo(r_inner, w/2).close().revolve(360, (0,0,0), (0,1,0))
- For complex profiles with drop-centers, lips, etc. — use .polyline(pts).close().revolve(360, (0,0,0), (0,1,0))
- IMPORTANT: In CadQuery's XZ workplane, local Y = global Z. So (0,1,0) revolves around the global Z-axis.
- For XY workplane revolve around Z, use (0,0,1). For XZ workplane revolve around global Z, use (0,1,0).

CAST/MACHINED SINGLE-PIECE PARTS:
- Wheels, rims, pulleys, gears = ONE SOLID, not separate parts for each feature
- UNION spokes with hub: hub.union(spoke1).union(spoke2)... then union with barrel
- This ensures spokes are physically connected to the hub (no floating pieces)
- Add fillets at spoke-hub junctions for realistic transitions
- Use .union() to merge all features before outputting

MULTI-PART is for ASSEMBLIES with truly separate manufactured components:
- Brake assembly: disc + caliper + pads = 3 separate parts
- Wheel + tire: rim is one solid part, tire is another
- Engine: block + head + pistons = separate parts
- Do NOT make each spoke/bolt/feature a separate part if it's one casting

POSITIONING:
- All parts must physically connect or touch — no floating pieces
- Center first part at origin, position others relative to it
- Read EXISTING PARTS context and place new parts accordingly

ASSEMBLY CONTEXT USAGE (CRITICAL):
- The CURRENT ASSEMBLY section below lists all existing parts with their FULL CadQuery scripts.
- READ EACH SCRIPT to extract exact dimension variables (rim_r, hub_r, bolt_pcd, etc.)
- When creating a part that must fit an existing one, use those EXACT values — not approximations.
- Example: if the Rim script has `bead_seat_r = 228.6`, your Tire script MUST have `rim_r = 228.6`
- If you need to create a Tire to fit a Rim, read the Rim script and extract its outer barrel radius,
  bead seat radius, and width. Your tire inner radius MUST match the rim bead seat radius exactly.

AVAILABLE HELPERS (pre-imported):
  round_tube(length, od=30, wall=2, axis='Z')
  rect_tube(length, width=40, height=25, wall=2, axis='Z')
  flat_plate(width, length, thickness=4)
  plate_with_bolt_holes(width, length, thickness=4, hole_d=8.5, margin=15)
  l_bracket(flange_w, flange_h, length, thickness=4)
  gusset(width, height, thickness=3)
  mounting_boss(height, od=12, bore_d=6)

CADQUERY REFERENCE:
  Box:        cq.Workplane("XY").box(L, W, H)
  Cylinder:   cq.Workplane("XY").cylinder(height, radius)
  Sphere:     cq.Workplane("XY").sphere(radius)
  Hole:       .faces(">Z").workplane().hole(diameter)
  Counterbore:.faces(">Z").workplane().cboreHole(d, cbD, cbDepth)
  Fillet:     .edges("|Z").fillet(r)
  Chamfer:    .edges(">Z").chamfer(d)
  Shell:      .shell(-thickness)
  Union:      wp1.union(wp2)
  Cut:        wp1.cut(wp2)
  Move:       .translate((x, y, z))
  Spin:       .rotate((0,0,0), (0,0,1), angle_deg)
"""


def _search_references(query: str, limit: int = 2) -> list:
    """engineering_references table removed — always returns empty."""
    return []


def _format_reference_context(refs: list) -> str:
    """engineering_references table removed — no-op."""
    return ""


def _format_knowledge_context(records: list) -> str:
    if not records:
        return ""
    lines = ["\nLEARNED OBJECT KNOWLEDGE (reuse these engineering patterns when relevant):"]
    for record in records:
        lines.append(f"\n--- {record['title']} [{record['object_type']}] ---")
        lines.append(f"Summary: {record['summary']}")
        if record.get("components"):
            lines.append(f"Typical components: {', '.join(record['components'])}")
        if record.get("dimensions"):
            lines.append(f"Reference dimensions: {_json.dumps(record['dimensions'], indent=2)}")
        if record.get("generation_rules"):
            lines.append("Generation rules (MUST FOLLOW — override any prior scripts in conversation):")
            lines.extend(f"- {rule}" for rule in record["generation_rules"])
        if record.get("validation_rules"):
            lines.append("Validation rules (reject and redo if violated):")
            lines.extend(f"- {rule}" for rule in record["validation_rules"])
    return "\n".join(lines)


def _format_pattern_context(patterns: list) -> str:
    if not patterns:
        return ""
    lines = ["\nSUCCESSFUL GENERATION PATTERNS (adapt these, do not copy blindly):"]
    for idx, pattern in enumerate(patterns, 1):
        lines.append(
            f"\n--- Pattern {idx}: {pattern['object_type']} "
            f"(quality {pattern['quality_score']:.2f}, successes {pattern['success_count']}) ---"
        )
        lines.append(f"Prompt: {pattern['prompt_text']}")
        if pattern.get("part_names"):
            lines.append(f"Part names: {', '.join(pattern['part_names'])}")
        script_lines = pattern["script"].splitlines()
        preview = "\n".join(script_lines[:18])
        lines.append(f"Script preview:\n{preview}")
    return "\n".join(lines)


def build_full_prompt(assembly_id: Optional[int] = None, user_query: str = "") -> str:
    """Combine base prompt with assembly context."""
    prompt = BASE_SYSTEM_PROMPT
    if assembly_id:
        context = build_assembly_context(assembly_id)
        prompt += "\n\n" + context
    return prompt


def build_generation_prompt(
    assembly_id: Optional[int],
    user_query: str,
    spec: dict,
    plan: dict,
    references: list,
    knowledge_records: list,
    patterns: list,
) -> str:
    """Build the final code-generation prompt using structured spec and plan."""
    prompt = BASE_SYSTEM_PROMPT

    if references:
        ref_context = _format_reference_context(references)
        if ref_context:
            prompt += "\n" + ref_context
    # NOTE: knowledge_records and patterns are intentionally disabled.
    # They store low-quality past generation data that conflicts with Claude's
    # built-in engineering knowledge and causes worse output.
    # Claude knows more about engineering than anything in these tables.

    prompt += "\n\nSTRUCTURED ENGINEERING SPEC:\n"
    prompt += _json.dumps(spec, indent=2)
    prompt += "\n\nASSEMBLY PLAN:\n"
    prompt += _json.dumps(plan, indent=2)
    prompt += "\n\nMANDATORY GENERATION RULES:\n"
    prompt += "- Follow the STRUCTURED ENGINEERING SPEC exactly.\n"
    prompt += "- Generate ONLY the components listed in the ASSEMBLY PLAN.\n"
    prompt += "- Do NOT invent extra parts that are not required.\n"
    prompt += "- Do NOT generate forbidden components.\n"
    if plan.get("is_multi_part", True):
        prompt += "- All requested parts must be physically meaningful and named clearly in parts = {}.\n"
        prompt += "- Return a multi-part script using parts = {}.\n"
    else:
        prompt += "- Return a single unified solid using result = ... and do NOT use parts = {}.\n"
        prompt += "- Avoid stacked or overlapping internal solids unless the user explicitly requested a cutaway/component model.\n"
    if spec.get("object_type") == "tire":
        dims = spec.get("dimensions") or {}
        wall_thickness = dims.get("wall_thickness_mm")
        section_w = dims.get("section_width_mm", 225)
        aspect = dims.get("aspect_ratio_pct", 45)
        rim_d_in = dims.get("rim_diameter_in", 17)

        # Pre-calculate real tire dimensions for the AI
        rim_r = rim_d_in * 25.4 / 2  # rim radius in mm
        sidewall_h = section_w * aspect / 100  # sidewall height
        outer_r = rim_r + sidewall_h  # outer (tread) radius
        inner_r = rim_r  # bead seat radius (where tire meets rim)
        half_w = section_w / 2  # half section width

        tread_hw = half_w * 0.78
        sh_r = 15  # shoulder radius

        prompt += "\n- TIRE GEOMETRY — COPY THIS EXACT CODE (values pre-calculated for this tire):\n"
        prompt += "```python\n"
        prompt += "import cadquery as cq\n\n"
        prompt += f"rim_r = {rim_r:.1f}      # bead seat radius\n"
        prompt += f"outer_r = {outer_r:.1f}  # tread surface radius\n"
        prompt += f"half_w = {half_w:.1f}    # half section width\n"
        prompt += f"tread_hw = {tread_hw:.1f} # half tread width (narrower than section)\n"
        prompt += f"sh_r = {sh_r}            # shoulder fillet radius\n\n"
        prompt += "# Outer profile: bead -> straight sidewall -> shoulder arc -> FLAT tread -> mirror\n"
        prompt += "outer = (cq.Workplane('XZ')\n"
        prompt += "    .moveTo(rim_r, -half_w)\n"
        prompt += "    .lineTo(outer_r - sh_r, -tread_hw - sh_r * 0.3)\n"
        prompt += "    .threePointArc((outer_r - sh_r * 0.3, -tread_hw), (outer_r, -tread_hw + sh_r * 0.3))\n"
        prompt += "    .lineTo(outer_r, tread_hw - sh_r * 0.3)\n"
        prompt += "    .threePointArc((outer_r - sh_r * 0.3, tread_hw), (outer_r - sh_r, tread_hw + sh_r * 0.3))\n"
        prompt += "    .lineTo(rim_r, half_w)\n"
        prompt += "    .close()\n"
        prompt += "    .revolve(360, (0, 0), (0, 1, 0)))\n\n"

        if wall_thickness:
            wt = wall_thickness
            i_rim = rim_r + wt
            i_outer = outer_r - wt
            i_hw = half_w - wt
            i_thw = tread_hw - wt
            i_sr = max(sh_r - 1, 5)
            prompt += f"# Hollow: subtract inner shell (wall = {wt}mm)\n"
            prompt += f"wt = {wt}\n"
            prompt += f"i_rim = {i_rim:.1f}\n"
            prompt += f"i_outer = {i_outer:.1f}\n"
            prompt += f"i_hw = {i_hw:.1f}\n"
            prompt += f"i_thw = {i_thw:.1f}\n"
            prompt += f"i_sr = {i_sr}\n"
            prompt += "inner = (cq.Workplane('XZ')\n"
            prompt += "    .moveTo(i_rim, -i_hw)\n"
            prompt += "    .lineTo(i_outer - i_sr, -i_thw - i_sr * 0.3)\n"
            prompt += "    .threePointArc((i_outer - i_sr * 0.3, -i_thw), (i_outer, -i_thw + i_sr * 0.3))\n"
            prompt += "    .lineTo(i_outer, i_thw - i_sr * 0.3)\n"
            prompt += "    .threePointArc((i_outer - i_sr * 0.3, i_thw), (i_outer - i_sr, i_thw + i_sr * 0.3))\n"
            prompt += "    .lineTo(i_rim, i_hw)\n"
            prompt += "    .close()\n"
            prompt += "    .revolve(360, (0, 0), (0, 1, 0)))\n\n"
            prompt += "result = outer.cut(inner)\n"
        else:
            prompt += "result = outer\n"

        prompt += "```\n"
        prompt += "- COPY THIS CODE EXACTLY. Do NOT modify the profile shape. Do NOT use .circle().\n"
        prompt += "- The tread MUST be FLAT (two .lineTo at outer_r). NOT a sphere. NOT a balloon.\n"
        prompt += "- Only add tread grooves if the user explicitly asks for tread detail.\n"
        prompt += "- Output result = ..., NOT parts = {}. Tire is always ONE solid.\n"
    if spec.get("object_type") == "wheel":
        dims = spec.get("dimensions") or {}
        spoke_count = dims.get("spoke_count", 5)
        prompt += "\n- WHEEL GENERATION RULES (CRITICAL — follow exactly):\n"
        prompt += "  COORDINATE SYSTEM: Wheel axis = Z. Hub face in XY plane. ALL parts use Z-axis.\n"
        prompt += "  parts = {} with ONLY these part names: 'Hub', 'Barrel', 'Spoke_1'...'Spoke_N'\n"
        prompt += "  FORBIDDEN: Do NOT add visualization parts (bolt hole cylinders, bore cylinders,\n"
        prompt += "    drop center rings, well rings, lip parts). Only real physical geometry.\n"
        prompt += "\n"
        prompt += "  # 1. BARREL — revolve a closed profile around Z-axis:\n"
        prompt += "  # Build cross-section in XZ plane as (radius, z_position) polyline.\n"
        prompt += "  # Profile: outer lip -> bead seat -> taper to drop center -> well floor -> mirror back.\n"
        prompt += "  # Then inner wall closing inward. Revolve 360° around Z.\n"
        prompt += "  # In XZ workplane: local Y = global Z. Revolve around local Y to spin around global Z.\n"
        prompt += "  barrel = cq.Workplane('XZ').polyline(profile_pts).close().revolve(360, (0,0,0), (0,1,0))\n"
        prompt += "  parts['Barrel'] = barrel\n"
        prompt += "\n"
        prompt += "  # 2. HUB — cylinder in XY plane with bore and bolt holes CUT IN:\n"
        prompt += "  hub = cq.Workplane('XY').cylinder(hub_thickness, hub_radius)\n"
        prompt += "  # Cut center bore: hub = hub.cut(cq.Workplane('XY').cylinder(h+4, bore_r))\n"
        prompt += "  # Cut bolt holes in a loop: create cylinder at (bx,by,0), hub = hub.cut(bolt_cyl)\n"
        prompt += "  # Cut counterbores: smaller cylinder at top face, hub = hub.cut(cbore_cyl)\n"
        prompt += "  parts['Hub'] = hub\n"
        prompt += "\n"
        prompt += f"  # 3. SPOKES — {spoke_count} sculpted lofted parts, each separate:\n"
        prompt += "  # Use LOFT with 3 cross-sections in YZ plane at X offsets (hub, mid, tip).\n"
        prompt += "  # Hub end: thicker (16mm). Mid: medium (12mm). Tip: thin (8mm). Width increases outward.\n"
        prompt += "  # Pattern:\n"
        prompt += "  #   s1 = cq.Workplane('YZ').workplane(offset=spoke_inner_r).rect(w_hub, h_hub)\n"
        prompt += "  #   s2 = s1.workplane(offset=spoke_len*0.5).rect(w_mid, h_mid)\n"
        prompt += "  #   s3 = s2.workplane(offset=spoke_len*0.5).rect(w_tip, h_tip)\n"
        prompt += "  #   spoke = s1.add(s2).add(s3).loft()\n"
        prompt += "  #   spoke = spoke.translate((0, 0, hub_z)).rotate((0,0,0), (0,0,1), ang_deg)\n"
        prompt += "  # spoke_outer_r = drop_center_r - barrel_wall (NOT rim_radius — stop at inner wall)\n"
        prompt += f"  for i in range({spoke_count}):\n"
        prompt += f"      ang_deg = i * {360.0 / spoke_count:.1f} + {360.0 / (spoke_count * 2):.1f}  # offset between bolts\n"
        prompt += "      parts[f'Spoke_{{i+1}}'] = spoke\n"
        prompt += "\n"
        prompt += "  SPATIAL RULES:\n"
        prompt += "  1. Spokes sit BETWEEN bolt holes — offset by half-bolt-spacing.\n"
        prompt += "  2. Spoke inner end overlaps into hub but clears bolt PCD + bolt_hole_r + 2mm.\n"
        prompt += "  3. Center bore is fully clear — no spoke inside center_bore_r.\n"
        prompt += "  4. Hub thickness ≈ spoke thickness so they sit flush.\n"
        if dims.get("spoke_count"):
            prompt += f"- Use exactly {dims['spoke_count']} spokes.\n"
        if dims.get("pcd_mm"):
            prompt += f"- Bolt PCD: {dims['pcd_mm']} mm (radius = {dims['pcd_mm'] / 2:.2f} mm).\n"
        if dims.get("center_bore_mm"):
            prompt += f"- Center bore diameter: {dims['center_bore_mm']} mm (radius = {dims['center_bore_mm'] / 2:.1f} mm).\n"
    prompt += f"- Original user request: {user_query}\n"

    if assembly_id:
        context = build_assembly_context(assembly_id)
        prompt += "\n\n" + context

    return prompt


SYSTEM_PROMPT = BASE_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Schemas (legacy, kept for backward compat)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]


class ScriptRequest(BaseModel):
    script: str


# ---------------------------------------------------------------------------
# Helper: row -> dict
# ---------------------------------------------------------------------------

def _row_to_part(row, columns) -> dict:
    return dict(zip(columns, row))


PART_COLUMNS = [
    "id", "assembly_id", "name", "description", "cadquery_script",
    "position_x", "position_y", "position_z",
    "rotation_x", "rotation_y", "rotation_z",
    "scale_x", "scale_y", "scale_z",
    "material", "color", "visible", "locked",
    "bbox_min_x", "bbox_min_y", "bbox_min_z",
    "bbox_max_x", "bbox_max_y", "bbox_max_z",
    "parent_part_id", "part_type", "sort_order",
    "sketch_json", "sketch_plane",
    "parametric_type", "parametric_params",
    "created_at", "updated_at",
    "script_hash", "mesh_cache",
    "feature_tree_mode",
]

# ---------------------------------------------------------------------------
# Original endpoints (kept for backward compatibility)
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@app.post("/auth/register")
async def auth_register(body: RegisterRequest):
    return register_user(body.email, body.password, body.name)


@app.post("/auth/login")
async def auth_login(body: LoginRequest):
    return login_user(body.email, body.password)


@app.post("/auth/refresh")
async def auth_refresh(body: dict):
    return refresh_access_token(body.get("refresh_token", ""))


@app.get("/auth/me")
async def auth_me(user: UserInfo = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "avatar_url": user.avatar_url}


# ---------------------------------------------------------------------------
# Organization endpoints
# ---------------------------------------------------------------------------


class OrgCreate(BaseModel):
    name: str
    slug: Optional[str] = None


@app.post("/organizations")
async def create_org(body: OrgCreate, user: UserInfo = Depends(get_current_user)):
    import re
    slug = body.slug or re.sub(r'[^a-z0-9]+', '-', body.name.lower()).strip('-')
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO organizations (name, slug) VALUES (%s, %s) RETURNING id, name, slug, created_at",
                (body.name, slug),
            )
            row = cur.fetchone()
            org_id = row[0]
            # Creator becomes owner
            cur.execute(
                "INSERT INTO org_members (org_id, user_id, role) VALUES (%s, %s, 'owner')",
                (org_id, user.id),
            )
        conn.commit()
        return {"id": row[0], "name": row[1], "slug": row[2], "created_at": str(row[3])}
    except Exception as e:
        conn.rollback()
        if "unique" in str(e).lower():
            raise HTTPException(status_code=400, detail="Organization slug already taken")
        raise
    finally:
        put_connection(conn)


@app.get("/organizations")
async def list_orgs(user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT o.id, o.name, o.slug, om.role
                   FROM organizations o
                   JOIN org_members om ON om.org_id = o.id
                   WHERE om.user_id = %s
                   ORDER BY o.name""",
                (user.id,),
            )
            return [{"id": r[0], "name": r[1], "slug": r[2], "role": r[3]} for r in cur.fetchall()]
    finally:
        put_connection(conn)


class OrgMemberAdd(BaseModel):
    email: str
    role: str = "member"


@app.get("/organizations/{org_id}/members")
async def list_org_members(org_id: int, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Must be a member to list
            cur.execute("SELECT role FROM org_members WHERE org_id=%s AND user_id=%s", (org_id, user.id))
            if not cur.fetchone():
                raise HTTPException(status_code=403, detail="Not a member of this organization")
            cur.execute(
                """SELECT u.id, u.name, u.email, om.role, om.joined_at
                   FROM org_members om
                   JOIN users u ON u.id = om.user_id
                   WHERE om.org_id = %s
                   ORDER BY om.joined_at""",
                (org_id,),
            )
            return [{"id": r[0], "name": r[1], "email": r[2], "role": r[3], "joined_at": str(r[4])} for r in cur.fetchall()]
    finally:
        put_connection(conn)


@app.post("/organizations/{org_id}/members")
async def add_org_member(org_id: int, body: OrgMemberAdd, user: UserInfo = Depends(get_current_user)):
    """Invite a member by email. Creates an invitation record they must accept in-app.
    Works whether or not the person has an account yet."""
    import uuid
    from datetime import datetime, timedelta, timezone

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Check caller is admin/owner
            cur.execute(
                "SELECT role FROM org_members WHERE org_id = %s AND user_id = %s",
                (org_id, user.id),
            )
            role_row = cur.fetchone()
            if not role_row or role_row[0] not in ("admin", "owner"):
                raise HTTPException(status_code=403, detail="Only admins can add members")

            # Check org exists + get name
            cur.execute("SELECT name FROM organizations WHERE id = %s", (org_id,))
            org_row = cur.fetchone()
            if not org_row:
                raise HTTPException(status_code=404, detail="Organization not found")
            org_name = org_row[0]

            email_lower = body.email.strip().lower()

            # Check if already a member
            cur.execute(
                """SELECT om.role FROM org_members om
                   JOIN users u ON u.id = om.user_id
                   WHERE om.org_id = %s AND lower(u.email) = %s""",
                (org_id, email_lower),
            )
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="User is already a member of this organization")

            # Delete any existing pending invite for same email+org
            cur.execute(
                "DELETE FROM invitations WHERE org_id=%s AND lower(email)=%s AND accepted_at IS NULL",
                (org_id, email_lower),
            )

            # Create invitation token
            token = str(uuid.uuid4())
            expires = datetime.now(timezone.utc) + timedelta(days=7)
            cur.execute(
                """INSERT INTO invitations (org_id, email, role, token, invited_by, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, email_lower, body.role, token, user.id, expires),
            )
            inv_id = cur.fetchone()[0]

        conn.commit()

        # Send email if RESEND_API_KEY is configured
        resend_key = os.environ.get("RESEND_API_KEY")
        if resend_key:
            try:
                import httpx
                httpx.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                    json={
                        "from": "noreply@aleksma.ai",
                        "to": [email_lower],
                        "subject": f"You've been invited to join {org_name} on Aleksma AI",
                        "text": f"You've been invited to join {org_name}.\n\nLog in to Aleksma AI and accept the invitation from the notification bell in the top toolbar.\n\nInvitation token: {token}",
                    },
                    timeout=10,
                )
            except Exception:
                pass  # Email failure is non-fatal

        return {"invited": True, "invitation_id": inv_id, "email": email_lower}
    except HTTPException:
        raise
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Debug / knowledge inspection endpoints
# ---------------------------------------------------------------------------


@app.get("/admin/knowledge")
async def admin_list_knowledge():
    return {"message": "object_knowledge table removed", "items": []}


@app.get("/admin/knowledge/drafts")
async def admin_list_knowledge_drafts():
    return {"message": "draft_object_knowledge table removed", "items": []}


@app.get("/admin/patterns")
async def admin_list_patterns():
    return {"message": "generation_patterns table removed", "items": []}


@app.get("/admin/feedback")
async def admin_list_feedback():
    return {"message": "generation_feedback table removed", "items": []}


# Legacy /chat endpoint removed — all AI routes through feature-tree engine now


@app.post("/generate")
async def generate(request: ScriptRequest):
    """Execute a CadQuery script and return the tessellated mesh for Three.js."""
    try:
        workplane = execute_script(request.script)
        _state["workplane"] = workplane
        mesh = shape_to_mesh(workplane)
        return {"success": True, **mesh}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error:\n{traceback.format_exc()}",
        )


@app.get("/export/stl")
async def get_stl():
    """Download the current model as a binary STL file."""
    if _state["workplane"] is None:
        raise HTTPException(status_code=404, detail="No model has been generated yet.")
    try:
        data = export_stl(_state["workplane"])
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="model.stl"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/export/step")
async def get_step():
    """Download the current model as a STEP file."""
    if _state["workplane"] is None:
        raise HTTPException(status_code=404, detail="No model has been generated yet.")
    try:
        data = export_step(_state["workplane"])
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="model.step"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# NEW CRUD ENDPOINTS — Phase 1
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@app.post("/projects", response_model=ProjectResponse)
async def create_project(body: ProjectCreate, user: UserInfo = Depends(get_current_user_optional)):
    uid = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO projects (user_id, name, description, org_id)
                   VALUES (%s, %s, %s, %s)
                   RETURNING id, user_id, name, description, created_at, updated_at""",
                (uid, body.name, body.description, body.org_id),
            )
            proj = cur.fetchone()
            project_id = proj[0]

            cur.execute(
                """INSERT INTO assemblies (project_id, name, description)
                   VALUES (%s, %s, %s)
                   RETURNING id, project_id, name, description, created_at, updated_at""",
                (project_id, "Default Assembly", ""),
            )
            asm = cur.fetchone()
        conn.commit()
        return ProjectResponse(
            id=proj[0],
            name=proj[2],
            description=proj[3],
            created_at=proj[4],
            assemblies=[
                AssemblyResponse(
                    id=asm[0], project_id=asm[1], name=asm[2],
                    description=asm[3], parts=[],
                )
            ],
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.get("/projects", response_model=List[ProjectResponse])
async def list_projects(user: UserInfo = Depends(get_current_user_optional)):
    uid = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.id, p.name, p.description, p.created_at, p.org_id, o.name as org_name
                   FROM projects p
                   LEFT JOIN organizations o ON o.id = p.org_id
                   WHERE p.user_id = %s
                      OR EXISTS(
                          SELECT 1
                          FROM project_shares ps
                          WHERE ps.project_id = p.id AND ps.user_id = %s
                      )
                      OR EXISTS(
                          SELECT 1
                          FROM project_shares ps
                          JOIN team_members tm ON tm.team_id = ps.team_id
                          WHERE ps.project_id = p.id AND tm.user_id = %s
                      )
                      OR EXISTS(
                          SELECT 1
                          FROM project_shares ps
                          JOIN org_members om ON om.org_id = ps.org_id
                          WHERE ps.project_id = p.id AND om.user_id = %s
                      )
                      OR EXISTS(
                          SELECT 1
                          FROM org_members om
                          WHERE om.org_id = p.org_id AND om.user_id = %s
                      )
                   ORDER BY updated_at DESC""",
                (uid, uid, uid, uid, uid),
            )
            rows = cur.fetchall()
            projects = []
            for r in rows:
                cur.execute(
                    """SELECT id, project_id, name, description
                       FROM assemblies WHERE project_id = %s ORDER BY id""",
                    (r[0],),
                )
                asms = [
                    AssemblyResponse(
                        id=a[0], project_id=a[1], name=a[2],
                        description=a[3], parts=[],
                    )
                    for a in cur.fetchall()
                ]
                projects.append(ProjectResponse(
                    id=r[0], name=r[1], description=r[2],
                    created_at=r[3], assemblies=asms,
                    org_id=r[4], org_name=r[5],
                ))
            return projects
    finally:
        put_connection(conn)


@app.patch("/projects/{project_id}/assign-org")
async def assign_project_org(project_id: int, body: dict, user: UserInfo = Depends(get_current_user)):
    """Assign or remove a project from an organization. Pass org_id=null to unassign."""
    org_id = body.get("org_id")  # can be None to unassign
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Must be project owner
            cur.execute("SELECT user_id FROM projects WHERE id=%s", (project_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Project not found")
            if row[0] != user.id:
                raise HTTPException(status_code=403, detail="Only the project owner can change its organization")
            # If assigning to org, must be org member
            if org_id is not None:
                cur.execute("SELECT 1 FROM org_members WHERE org_id=%s AND user_id=%s", (org_id, user.id))
                if not cur.fetchone():
                    raise HTTPException(status_code=403, detail="You are not a member of that organization")
            cur.execute("UPDATE projects SET org_id=%s WHERE id=%s", (org_id, project_id))
        conn.commit()
        return {"ok": True, "org_id": org_id}
    except HTTPException:
        raise
    finally:
        put_connection(conn)


@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, created_at FROM projects WHERE id = %s",
                (project_id,),
            )
            proj = cur.fetchone()
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found")

            cur.execute(
                """SELECT id, project_id, name, description
                   FROM assemblies WHERE project_id = %s ORDER BY id""",
                (project_id,),
            )
            asms = []
            for a in cur.fetchall():
                cur.execute(
                    "SELECT COUNT(*) FROM parts WHERE assembly_id = %s AND (archived = false OR archived IS NULL)", (a[0],)
                )
                asms.append(AssemblyResponse(
                    id=a[0], project_id=a[1], name=a[2],
                    description=a[3], parts=[],
                ))

            return ProjectResponse(
                id=proj[0], name=proj[1], description=proj[2],
                created_at=proj[3], assemblies=asms,
            )
    finally:
        put_connection(conn)


@app.delete("/projects/{project_id}")
async def delete_project(project_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM projects WHERE id = %s RETURNING id", (project_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Project not found")
        conn.commit()
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Assemblies
# ---------------------------------------------------------------------------


@app.post("/projects/{project_id}/assemblies", response_model=AssemblyResponse)
async def create_assembly(project_id: int, body: AssemblyCreate):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Project not found")

            cur.execute(
                """INSERT INTO assemblies (project_id, name, description)
                   VALUES (%s, %s, %s)
                   RETURNING id, project_id, name, description""",
                (project_id, body.name, body.description),
            )
            a = cur.fetchone()
        conn.commit()
        return AssemblyResponse(
            id=a[0], project_id=a[1], name=a[2], description=a[3], parts=[],
        )
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.get("/assemblies/{assembly_id}", response_model=AssemblyResponse)
async def get_assembly(assembly_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, project_id, name, description FROM assemblies WHERE id = %s",
                (assembly_id,),
            )
            asm = cur.fetchone()
            if not asm:
                raise HTTPException(status_code=404, detail="Assembly not found")

            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS)} FROM parts WHERE assembly_id = %s AND (archived = false OR archived IS NULL) ORDER BY id",
                (assembly_id,),
            )
            parts = [
                PartResponse(**_row_to_part(r, PART_COLUMNS)) for r in cur.fetchall()
            ]

            return AssemblyResponse(
                id=asm[0], project_id=asm[1], name=asm[2],
                description=asm[3], parts=parts,
            )
    finally:
        put_connection(conn)


@app.post("/assemblies/{assembly_id}/import-step")
async def import_step_file(assembly_id: int, file: UploadFile = File(...)):
    """Import a STEP or IGES file as a new part in the assembly."""
    import tempfile, os
    import cadquery as cq
    from pathlib import Path as _Path

    suffix = ".step" if file.filename.lower().endswith((".step", ".stp")) else ".iges"
    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        fname_lower = file.filename.lower()
        if fname_lower.endswith((".iges", ".igs")):
            wp = cq.importers.importStep(tmp_path)  # OCC handles IGES via same importer
        else:
            wp = cq.importers.importStep(tmp_path)
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"Failed to import file: {exc}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Use coarser tessellation for large imports to avoid OOM / multi-hour runs
    file_size_mb = len(content) / (1024 * 1024)
    if file_size_mb > 20:
        tess_tol, tess_ang = 0.5, 0.3      # draft quality for large files
    elif file_size_mb > 5:
        tess_tol, tess_ang = 0.2, 0.15     # preview quality for medium files
    else:
        tess_tol, tess_ang = 0.05, 0.1     # fine quality for small files
    mesh = shape_to_topo_mesh(wp, tolerance=tess_tol, angular_tolerance=tess_ang)
    part_name = _Path(file.filename).stem

    # Extract geometry metadata so the AI can reason about this part
    bbox = extract_bounding_box(wp)
    try:
        vol_mm3 = compute_volume(wp)
    except Exception:
        vol_mm3 = None

    # Count faces and edges for the AI context summary
    try:
        occ_shape = wp.val()
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE
        face_exp = TopExp_Explorer(occ_shape.wrapped, TopAbs_FACE)
        face_count = 0
        while face_exp.More():
            face_count += 1
            face_exp.Next()
        edge_exp = TopExp_Explorer(occ_shape.wrapped, TopAbs_EDGE)
        edge_count = 0
        while edge_exp.More():
            edge_count += 1
            edge_exp.Next()
    except Exception:
        face_count = None
        edge_count = None

    # Build a geometry summary the AI can read like a script
    dx = (bbox.get("bbox_max_x", 0) - bbox.get("bbox_min_x", 0)) if bbox else 0
    dy = (bbox.get("bbox_max_y", 0) - bbox.get("bbox_min_y", 0)) if bbox else 0
    dz = (bbox.get("bbox_max_z", 0) - bbox.get("bbox_min_z", 0)) if bbox else 0

    geo_lines = [
        f"# Imported STEP/IGES geometry: {file.filename}",
        f"# This part was imported from a CAD file — no parametric script available.",
        f"# Bounding box (mm):",
        f"#   Width  (X): {dx:.2f} mm",
        f"#   Depth  (Y): {dy:.2f} mm",
        f"#   Height (Z): {dz:.2f} mm",
    ]
    if vol_mm3 is not None:
        geo_lines.append(f"#   Volume:      {vol_mm3:.1f} mm³  ({vol_mm3/1000:.2f} cm³)")
    if face_count is not None:
        geo_lines.append(f"#   Faces: {face_count},  Edges: {edge_count}")
    geo_lines += [
        f"#",
        f"# When generating parts that mate with this geometry use these key dimensions:",
        f"IMPORT_WIDTH_MM  = {dx:.2f}",
        f"IMPORT_DEPTH_MM  = {dy:.2f}",
        f"IMPORT_HEIGHT_MM = {dz:.2f}",
    ]
    geo_script = "\n".join(geo_lines)

    geo_params = {
        "IMPORT_WIDTH_MM": round(dx, 2),
        "IMPORT_DEPTH_MM": round(dy, 2),
        "IMPORT_HEIGHT_MM": round(dz, 2),
    }
    if vol_mm3 is not None:
        geo_params["IMPORT_VOLUME_MM3"] = round(vol_mm3, 1)

    # Serialize mesh for caching — strip the 'id' key that hasn't been set yet
    mesh_to_cache = {k: v for k, v in mesh.items() if k != "id"}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO parts
                   (assembly_id, name, cadquery_script, color, part_type, step_data,
                    parametric_params,
                    bbox_min_x, bbox_min_y, bbox_min_z,
                    bbox_max_x, bbox_max_y, bbox_max_z,
                    mesh_cache, script_hash)
                   VALUES (%s, %s, %s, %s, 'imported', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    assembly_id, part_name, geo_script, '#888888', content,
                    _json.dumps(geo_params),
                    bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                    bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                    _json.dumps(mesh_to_cache), _script_hash(geo_script),
                )
            )
            part_id = cur.fetchone()[0]
        conn.commit()
    finally:
        put_connection(conn)

    mesh["id"] = part_id
    return {"part_id": part_id, "name": part_name, "mesh": mesh}


class AssemblyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


@app.put("/assemblies/{assembly_id}")
async def update_assembly(assembly_id: int, body: AssemblyUpdate):
    updates = body.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    set_clauses = []
    values = []
    for field, val in updates.items():
        set_clauses.append(f"{field} = %s")
        values.append(val)
    set_clauses.append("updated_at = now()")
    values.append(assembly_id)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE assemblies SET {', '.join(set_clauses)} WHERE id = %s RETURNING id, name",
                values,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Assembly not found")
        conn.commit()
        return {"id": row[0], "name": row[1]}
    finally:
        put_connection(conn)


@app.delete("/assemblies/{assembly_id}")
async def delete_assembly(assembly_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM assemblies WHERE id = %s RETURNING id", (assembly_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Assembly not found")
        conn.commit()
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------


@app.post("/assemblies/{assembly_id}/parts", response_model=PartResponse)
async def create_part(assembly_id: int, body: PartCreate):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM assemblies WHERE id = %s", (assembly_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Assembly not found")

            bbox = {}
            try:
                wp = execute_script(body.cadquery_script)
                bbox = extract_bounding_box(wp)
            except Exception:
                pass

            sketch_json_str = _json.dumps(body.sketch_json) if body.sketch_json else None
            sketch_plane_str = _json.dumps(body.sketch_plane) if body.sketch_plane else None
            parametric_params_str = _json.dumps(body.parametric_params) if body.parametric_params else None
            cur.execute(
                """INSERT INTO parts
                   (assembly_id, name, description, cadquery_script,
                    position_x, position_y, position_z,
                    material, color,
                    bbox_min_x, bbox_min_y, bbox_min_z,
                    bbox_max_x, bbox_max_y, bbox_max_z,
                    parent_part_id, part_type, sort_order,
                    sketch_json, sketch_plane,
                    parametric_type, parametric_params)
                   VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s, %s,%s)
                   RETURNING """ + ', '.join(PART_COLUMNS[:-2]),
                (
                    assembly_id, body.name, body.description, body.cadquery_script,
                    body.position_x, body.position_y, body.position_z,
                    body.material, body.color,
                    bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                    bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                    body.parent_part_id, body.part_type, body.sort_order,
                    sketch_json_str, sketch_plane_str,
                    body.parametric_type, parametric_params_str,
                ),
            )
            row = cur.fetchone()
            part_id = row[0]

            parsed_ops = parse_script_to_operations(body.cadquery_script)
            for seq, op_data in enumerate(parsed_ops, 1):
                cur.execute(
                    """INSERT INTO operations (part_id, sequence, operation, parameters)
                       VALUES (%s, %s, %s, %s)""",
                    (part_id, seq, op_data["operation"],
                     _json.dumps(op_data["parameters"])),
                )

        conn.commit()

        return PartResponse(**dict(zip(PART_COLUMNS[:-2], row)))
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.post("/assemblies/{assembly_id}/parametric-part")
async def create_parametric_part(assembly_id: int, body: ParametricPartCreate):
    """Create a new part from a parametric template."""
    if body.template not in TEMPLATE_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown template: {body.template}")

    tmpl = TEMPLATE_REGISTRY[body.template]
    # Fill defaults
    full_params = {}
    for key, schema in tmpl["param_schema"].items():
        full_params[key] = body.params.get(key, schema["default"])

    script = generate_from_template(body.template, full_params)
    part_name = body.name or tmpl["label"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM assemblies WHERE id = %s", (assembly_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Assembly not found")

            wp = execute_script(script)
            bbox = extract_bounding_box(wp)

            cur.execute(
                """INSERT INTO parts
                   (assembly_id, name, description, cadquery_script,
                    position_x, position_y, position_z,
                    material, color,
                    bbox_min_x, bbox_min_y, bbox_min_z,
                    bbox_max_x, bbox_max_y, bbox_max_z,
                    parametric_type, parametric_params)
                   VALUES (%s,%s,%s,%s, 0,0,0, 'aluminum','#A0A0A0',
                           %s,%s,%s, %s,%s,%s, %s,%s)
                   RETURNING id""",
                (
                    assembly_id, part_name, f"Parametric {tmpl['label']}", script,
                    bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                    bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                    body.template, _json.dumps(full_params),
                ),
            )
            part_id = cur.fetchone()[0]

            # Create raw_script operation
            cur.execute(
                """INSERT INTO operations (part_id, sequence, operation, parameters)
                   VALUES (%s, 1, 'raw_script', %s)""",
                (part_id, _json.dumps({"script": script})),
            )

        conn.commit()
        mesh = shape_to_mesh(wp)
        return {"success": True, "part_id": part_id, **mesh}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        put_connection(conn)


class MultiPartScriptRequest(BaseModel):
    script: str
    base_name: str = "Part"


@app.post("/assemblies/{assembly_id}/parts-from-script")
async def create_parts_from_script(assembly_id: int, body: MultiPartScriptRequest):
    """Execute a script and create parts. Multi-part scripts get a parent group + children."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM assemblies WHERE id = %s", (assembly_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Assembly not found")
    finally:
        put_connection(conn)

    try:
        multi_parts = execute_script_multi(body.script)
    except (ValueError, Exception) as exc:
        raise HTTPException(status_code=400, detail=f"Script execution failed: {exc}")

    is_multi = len(multi_parts) > 1
    created = []
    colors = [
        '#4488CC', '#CC4444', '#44AA44', '#CC8844', '#8844CC',
        '#44AAAA', '#AA44AA', '#6688CC', '#CC6688', '#88AA44',
    ]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            parent_id = None

            if is_multi:
                # Create a parent group part (component type, no geometry of its own)
                # It holds the full script so the assembly can be re-generated
                combined_bbox = {}
                try:
                    combined_wp = execute_script(body.script)
                    combined_bbox = extract_bounding_box(combined_wp)
                except Exception:
                    pass

                cur.execute(
                    """INSERT INTO parts
                       (assembly_id, name, description, cadquery_script,
                        position_x, position_y, position_z,
                        material, color, part_type,
                        bbox_min_x, bbox_min_y, bbox_min_z,
                        bbox_max_x, bbox_max_y, bbox_max_z)
                       VALUES (%s,%s,%s,%s, 0,0,0, 'steel','#888888','component',
                               %s,%s,%s, %s,%s,%s)
                       RETURNING id""",
                    (
                        assembly_id, body.base_name,
                        f"Multi-part assembly with {len(multi_parts)} components",
                        body.script,
                        combined_bbox.get("bbox_min_x"), combined_bbox.get("bbox_min_y"),
                        combined_bbox.get("bbox_min_z"), combined_bbox.get("bbox_max_x"),
                        combined_bbox.get("bbox_max_y"), combined_bbox.get("bbox_max_z"),
                    ),
                )
                parent_id = cur.fetchone()[0]
                created.append({
                    "id": parent_id,
                    "name": body.base_name,
                    "color": "#888888",
                    "is_group": True,
                })

            for idx, (part_name, wp) in enumerate(multi_parts.items()):
                bbox = {}
                try:
                    bbox = extract_bounding_box(wp)
                except Exception:
                    pass

                color = colors[idx % len(colors)]

                part_script = (
                    f"{body.script}\n"
                    f"result = parts[\"{part_name}\"]\n"
                ) if is_multi else body.script

                cur.execute(
                    """INSERT INTO parts
                       (assembly_id, name, description, cadquery_script,
                        position_x, position_y, position_z,
                        material, color, parent_part_id, sort_order,
                        bbox_min_x, bbox_min_y, bbox_min_z,
                        bbox_max_x, bbox_max_y, bbox_max_z)
                       VALUES (%s,%s,%s,%s, 0,0,0, 'steel',%s, %s,%s,
                               %s,%s,%s, %s,%s,%s)
                       RETURNING id, name""",
                    (
                        assembly_id, part_name,
                        f"Component of {body.base_name}" if is_multi else "",
                        part_script, color, parent_id, idx,
                        bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                        bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                    ),
                )
                row = cur.fetchone()
                created.append({
                    "id": row[0],
                    "name": row[1],
                    "color": color,
                    "is_group": False,
                })

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)

    return {"parts": created, "count": len(created), "parent_id": parent_id}


@app.post("/parts/batch-visibility")
async def batch_visibility(body: dict):
    """Set visibility for multiple parts at once."""
    part_ids = body.get("part_ids", [])
    visible = body.get("visible", True)
    if not part_ids:
        return {"updated": 0}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE parts SET visible = %s WHERE id = ANY(%s::int[])",
                (visible, part_ids),
            )
        conn.commit()
        return {"updated": cur.rowcount}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.put("/parts/{part_id}", response_model=PartResponse)
async def update_part(part_id: int, body: PartUpdate, user: UserInfo = Depends(get_current_user_optional)):
    user_id = get_user_id_for_request(user)
    updates = body.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clauses = []
    values = []
    for field, val in updates.items():
        set_clauses.append(f"{field} = %s")
        if field in ("sketch_json", "sketch_plane") and val is not None:
            values.append(_json.dumps(val))
        else:
            values.append(val)
    # Invalidate mesh cache when the script changes
    if "cadquery_script" in updates:
        set_clauses.append("script_hash = NULL")
        set_clauses.append("mesh_cache = NULL")
        _mesh_lru.invalidate(part_id)
    set_clauses.append("updated_at = now()")
    values.append(part_id)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            returning_cols = ', '.join(PART_COLUMNS[:-2])
            cur.execute(
                f"""UPDATE parts SET {', '.join(set_clauses)}
                    WHERE id = %s
                    RETURNING {returning_cols}""",
                values,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
            # Auto-checkpoint on user's working branch when script changes
            if "cadquery_script" in updates:
                try:
                    branch_id = _ensure_user_working_branch(cur, user_id, part_id)
                    _create_version(cur, part_id, branch_id, updates["cadquery_script"],
                                    label="Manual edit", author_type="human", auto=True)
                except Exception:
                    pass  # version control is best-effort
        conn.commit()
        return PartResponse(**dict(zip(PART_COLUMNS[:-2], row)))
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.delete("/parts/{part_id}")
async def delete_part(part_id: int):
    """Soft-delete (archive) a part and all its children — preserves version history."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Recursively archive the part and all descendants
            cur.execute(
                """
                WITH RECURSIVE descendants AS (
                    SELECT id FROM parts
                    WHERE id = %s AND (archived = false OR archived IS NULL)
                    UNION ALL
                    SELECT p.id FROM parts p
                    INNER JOIN descendants d ON p.parent_part_id = d.id
                    WHERE p.archived = false OR p.archived IS NULL
                )
                UPDATE parts SET archived = true, archived_at = now(), visible = false
                WHERE id IN (SELECT id FROM descendants)
                RETURNING id
                """,
                (part_id,),
            )
            archived_ids = [row[0] for row in cur.fetchall()]
            if not archived_ids:
                raise HTTPException(status_code=404, detail="Part not found or already archived")
        conn.commit()
        return {"deleted": True, "archived": True, "part_id": part_id, "archived_ids": archived_ids}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/unarchive")
async def unarchive_part(part_id: int):
    """Restore an archived part."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE parts SET archived = false, archived_at = NULL, visible = true "
                "WHERE id = %s AND archived = true RETURNING id",
                (part_id,),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Part not found or not archived")
        conn.commit()
        return {"restored": True, "part_id": part_id}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


class FaceAtPointRequest(BaseModel):
    point: list
    normal: list


@app.post("/parts/{part_id}/face-at-point")
async def get_face_at_point(part_id: int, body: FaceAtPointRequest):
    """Find the face on a part closest to the given point+normal."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT cadquery_script FROM parts WHERE id = %s", (part_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
        wp = execute_script(row[0])
        return find_face_at_point(wp, body.point, body.normal)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_connection(conn)


@app.get("/parts/{part_id}/topo-mesh")
async def get_part_topo_mesh(part_id: int):
    """Return topology-enriched mesh for a single part (cached by script hash)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cadquery_script, script_hash, mesh_cache, feature_tree_mode FROM parts WHERE id = %s",
                (part_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
        script, stored_hash, mesh_cache, feature_tree_mode = row
        current_hash = _script_hash(script)

        # 0. In-memory LRU check (avoids DB deserialization)
        lru_hit = _mesh_lru.get(part_id, current_hash)
        if lru_hit:
            return lru_hit

        # Feature-tree parts: mesh managed by _do_rebuild — return cache directly
        if feature_tree_mode and mesh_cache:
            if not isinstance(mesh_cache, dict):
                mesh_cache = json.loads(mesh_cache)
            mesh_cache["id"] = part_id
            _mesh_lru.put(part_id, current_hash, mesh_cache)
            return mesh_cache

        # Return cached mesh if script hasn't changed
        if stored_hash == current_hash and mesh_cache:
            if not isinstance(mesh_cache, dict):
                mesh_cache = json.loads(mesh_cache)
            mesh_cache["id"] = part_id
            _mesh_lru.put(part_id, current_hash, mesh_cache)
            return mesh_cache

        # Recompute
        wp = execute_script(script)
        mesh = shape_to_topo_mesh(wp)
        mesh["id"] = part_id

        # Persist cache
        conn2 = get_connection()
        try:
            with conn2.cursor() as cur:
                cur.execute(
                    "UPDATE parts SET script_hash=%s, mesh_cache=%s WHERE id=%s",
                    (current_hash, json.dumps(mesh), part_id),
                )
            conn2.commit()
        finally:
            put_connection(conn2)

        _mesh_lru.put(part_id, current_hash, mesh)
        return mesh
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_connection(conn)


@app.get("/parts/{part_id}/topo-mesh-bin")
async def get_part_topo_mesh_bin(part_id: int, quality: str = "preview"):
    """Return topology-enriched mesh in compact binary format.

    quality: 'draft' (fast/coarse), 'preview' (default), 'precise' (fine).
    Draft skips DB cache and uses coarse tessellation for instant display.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cadquery_script, script_hash, mesh_cache, feature_tree_mode FROM parts WHERE id = %s",
                (part_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
        script, stored_hash, mesh_cache, feature_tree_mode = row
        current_hash = _script_hash(script)

        # For non-draft quality, use caches
        if quality != "draft":
            # 1. Check in-memory LRU first
            cached = _mesh_lru.get(part_id, current_hash)
            if cached:
                return Response(content=_pack_binary_mesh(cached), media_type="application/octet-stream")

            # 2. Feature-tree parts
            if feature_tree_mode and mesh_cache:
                if not isinstance(mesh_cache, dict):
                    mesh_cache = json.loads(mesh_cache)
                mesh_cache["id"] = part_id
                _mesh_lru.put(part_id, current_hash, mesh_cache)
                return Response(content=_pack_binary_mesh(mesh_cache), media_type="application/octet-stream")

            # 3. DB cache hit
            if stored_hash == current_hash and mesh_cache:
                if not isinstance(mesh_cache, dict):
                    mesh_cache = json.loads(mesh_cache)
                mesh_cache["id"] = part_id
                _mesh_lru.put(part_id, current_hash, mesh_cache)
                return Response(content=_pack_binary_mesh(mesh_cache), media_type="application/octet-stream")

        # 4. Recompute at requested quality
        wp = execute_script(script)
        mesh = shape_to_topo_mesh(wp, quality=quality)
        mesh["id"] = part_id

        # Only persist preview/precise to DB cache (not draft)
        if quality != "draft":
            conn2 = get_connection()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "UPDATE parts SET script_hash=%s, mesh_cache=%s WHERE id=%s",
                        (current_hash, json.dumps(mesh), part_id),
                    )
                conn2.commit()
            finally:
                put_connection(conn2)
            _mesh_lru.put(part_id, current_hash, mesh)

        _mesh_lru.put(part_id, current_hash, mesh)
        return Response(content=_pack_binary_mesh(mesh), media_type="application/octet-stream")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_connection(conn)


@app.get("/parts/{part_id}/mass-properties")
async def get_mass_properties(part_id: int):
    """Compute volume, surface area, center of mass for a part."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT cadquery_script, material FROM parts WHERE id = %s", (part_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Part not found")
        script, material = row
        if not script:
            raise HTTPException(status_code=400, detail="Part has no geometry script")

        wp = execute_script(script)
        shape = wp.val()

        volume_mm3 = shape.Volume()
        surface_area_mm2 = shape.Area() if hasattr(shape, 'Area') else 0
        center = shape.Center()

        # MATERIAL_DENSITIES is in kg/m³; volume is in mm³
        density_kg_m3 = MATERIAL_DENSITIES.get(material or "steel", 7850)
        mass_kg = volume_mm3 * density_kg_m3 * 1e-9  # mm³ → m³

        return {
            "part_id": part_id,
            "material": material or "steel",
            "volume_mm3": round(volume_mm3, 2),
            "volume_cm3": round(volume_mm3 / 1000, 4),
            "surface_area_mm2": round(surface_area_mm2, 2),
            "center_of_mass": [round(center.x, 2), round(center.y, 2), round(center.z, 2)],
            "density_g_cm3": round(density_kg_m3 / 1000, 3),
            "mass_grams": round(mass_kg * 1000, 2),
            "mass_kg": round(mass_kg, 4),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/suggestions")
async def get_design_suggestions(part_id: int):
    """AI-powered design improvement suggestions."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, cadquery_script, material FROM parts WHERE id = %s", (part_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Part not found")
        name, script, material = row

        import anthropic as _anthropic
        import json as _json
        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": f"""Analyze this CadQuery CAD part and provide exactly 3 practical design improvement suggestions.

Part name: {name}
Material: {material or 'steel'}
CadQuery script:
```python
{script or '# no script'}
```

For each suggestion provide:
- title: short name (5-8 words)
- description: 1-2 sentences explaining the improvement
- category: one of [structural, manufacturing, weight, aesthetics, cost]

Return ONLY valid JSON array: [{{"title": "...", "description": "...", "category": "..."}}]"""
            }]
        )

        text = response.content[0].text
        start = text.find('[')
        end = text.rfind(']') + 1
        suggestions = _json.loads(text[start:end]) if start >= 0 else []

        return {"part_id": part_id, "suggestions": suggestions}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/apply-suggestion")
async def apply_suggestion(part_id: int, body: dict):
    """Apply an AI design suggestion — routes through the feature-tree engine."""
    title = body.get("title", "")
    description = body.get("description", "")
    if not title and not description:
        raise HTTPException(status_code=400, detail="Provide title or description")

    # Convert suggestion into an instruction for the feature-tree AI editor
    instruction = f"Apply this improvement: {title}"
    if description:
        instruction += f" — {description}"

    return await edit_part_with_ai(part_id, {"instruction": instruction})


@app.post("/parts/{part_id}/edit-with-ai")
async def edit_part_with_ai(part_id: int, body: dict):
    """Apply a free-form AI instruction to modify a part's feature tree (or script for legacy parts)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    instruction = body.get("instruction", "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="Provide an instruction")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, cadquery_script, part_type, feature_tree_mode, parametric_type FROM parts WHERE id = %s", (part_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Part not found")
        name, script, part_type, feature_tree_mode, parametric_type = row

        # Imported STEP/IGES parts are locked — AI must never overwrite them
        if part_type == "imported":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{name}' is an imported STEP/IGES part and cannot be modified by AI. "
                    "Read its bounding-box dimensions from the assembly context and create a "
                    "new part that fits around it instead."
                ),
            )

        # Component (group) parts → multi-part edit on all children
        if part_type == "component":
            return await _edit_component_group_with_ai(part_id, name, instruction, body, conn)

        # Child part of a component group → auto-route to group edit for full sibling context
        with conn.cursor() as cur:
            cur.execute("SELECT parent_part_id FROM parts WHERE id = %s", (part_id,))
            parent_row = cur.fetchone()
        if parent_row and parent_row[0]:
            parent_id = parent_row[0]
            with conn.cursor() as cur:
                cur.execute("SELECT name, part_type FROM parts WHERE id = %s", (parent_id,))
                parent_info = cur.fetchone()
            if parent_info and parent_info[1] == "component":
                print(f"[AI-EDIT] Part {part_id} is child of component group '{parent_info[0]}' (id={parent_id}), routing to group edit")
                return await _edit_component_group_with_ai(parent_id, parent_info[0], instruction, body, conn)

        # ---------------------------------------------------------------
        # Parametric template parts: edit the CadQuery script directly
        # instead of destroying geometry via feature-tree auto-migration.
        # ---------------------------------------------------------------
        if parametric_type and script and script.strip():
            print(f"[AI-EDIT] Parametric template part '{name}' (type={parametric_type}), using script-based edit")
            return await _edit_part_with_ai_script(part_id, name, script, instruction, conn)

        # ---------------------------------------------------------------
        # All parts use the feature-tree approach now.
        # Legacy parts without feature_tree_mode get auto-migrated.
        # ---------------------------------------------------------------
        if not feature_tree_mode:
            # Auto-migrate: enable feature_tree_mode and seed a base feature from existing script
            print(f"[AI-EDIT] Auto-migrating part {part_id} '{name}' to feature-tree mode")
            with conn.cursor() as cur:
                cur.execute("UPDATE parts SET feature_tree_mode = true WHERE id = %s", (part_id,))
                main_branch = _ensure_main_branch(cur, part_id)
                # Check if part already has features (from manual creation)
                cur.execute("SELECT count(*) FROM features WHERE part_id = %s AND branch_id = %s", (part_id, main_branch))
                feat_count = cur.fetchone()[0]
                if feat_count == 0 and script and script.strip() and not script.startswith("# Feature tree"):
                    # Try to extract bbox from existing geometry for a seed box feature
                    try:
                        wp = execute_script(script)
                        bbox = extract_bounding_box(wp)
                        if bbox.get("bbox_min_x") is not None:
                            w = bbox["bbox_max_x"] - bbox["bbox_min_x"]
                            d = bbox["bbox_max_y"] - bbox["bbox_min_y"]
                            h = bbox["bbox_max_z"] - bbox["bbox_min_z"]
                            cx = (bbox["bbox_max_x"] + bbox["bbox_min_x"]) / 2
                            cy = (bbox["bbox_max_y"] + bbox["bbox_min_y"]) / 2
                            cz = (bbox["bbox_max_z"] + bbox["bbox_min_z"]) / 2
                            # Seed a box feature approximating the original shape
                            cur.execute(
                                "INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, source) "
                                "VALUES (%s, %s, 'box', %s, 1, %s, 'auto-migrate')",
                                (part_id, main_branch, f"{name} (base)",
                                 _json.dumps({"length": round(w, 2), "width": round(d, 2), "height": round(h, 2),
                                              "centered": True})),
                            )
                            print(f"[AI-EDIT] Seeded box feature {w:.1f}x{d:.1f}x{h:.1f} for part {part_id}")
                    except Exception as e:
                        print(f"[AI-EDIT] Could not extract bbox for seed feature: {e}")
            conn.commit()

        return await _edit_part_with_ai_features(part_id, name, instruction, body, conn)

    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


async def _edit_part_with_ai_script(part_id: int, name: str, current_script: str, instruction: str, conn) -> dict:
    """Edit a parametric template part by having AI modify the CadQuery script directly.

    This preserves the original parametric geometry instead of destroying it
    via feature-tree auto-migration (which replaces complex geometry with a bounding box).
    """
    import anthropic as _anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    prompt = f"""You are modifying an existing CadQuery script for a CAD part called "{name}".

Current CadQuery script:
```python
{current_script}
```

User instruction: {instruction}

Rules:
1. Return ONLY the complete modified CadQuery script — no explanation, no markdown fences.
2. The script must be valid CadQuery code that produces a single `result` variable (a cq.Workplane or cq.Shape).
3. Keep all the original geometry intact. Only add/modify what the user requested.
4. Use CadQuery operations like .edges().chamfer(), .edges().fillet(), .cut(), .union(), etc.
5. For "all edges" chamfer/fillet, use result.edges().chamfer(size) or result.edges().fillet(size).
6. If the instruction references specific edges, use selectors like "|Z", ">Z", "<Z", etc.
7. Make sure the final line assigns to `result`.
8. Do NOT import anything — `import cadquery as cq` is already available.
9. Keep the script self-contained and working.
"""

    client = _anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    new_script = response.content[0].text.strip()
    # Strip markdown fences if AI included them despite instructions
    if new_script.startswith("```"):
        lines = new_script.split("\n")
        # Remove first line (```python) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        new_script = "\n".join(lines)

    print(f"[AI-EDIT-SCRIPT] Generated new script for part {part_id} ({len(new_script)} chars)")

    # Execute the new script to validate it and get mesh + bbox
    try:
        wp_result = execute_script(new_script)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI-generated script failed to execute: {e}")

    bbox = extract_bounding_box(wp_result)
    mesh_result = shape_to_topo_mesh(wp_result)
    mesh_result["id"] = part_id

    # Save on AI branch (same pattern as feature-tree edits)
    with conn.cursor() as cur:
        ai_uid = _get_ai_user_id(cur)
        branch_id = _ensure_user_working_branch(cur, ai_uid, part_id)
        version_id = _create_version(
            cur, part_id, branch_id, new_script,
            label=f"AI: {instruction[:80]}",
            author_type="ai",
            author_info=instruction,
        )
        # Update bbox on the part (lightweight, non-destructive)
        cur.execute(
            """UPDATE parts SET
                   bbox_min_x = %s, bbox_min_y = %s, bbox_min_z = %s,
                   bbox_max_x = %s, bbox_max_y = %s, bbox_max_z = %s,
                   updated_at = now()
               WHERE id = %s""",
            (
                bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                part_id,
            ),
        )
    conn.commit()

    print(f"[AI-EDIT-SCRIPT] Saved version {version_id} on AI branch {branch_id} for part {part_id}")
    return {
        "part_id": part_id,
        "mesh": mesh_result,
        "on_branch": True,
        "ai_operations": [{"type": "script_edit", "instruction": instruction}],
        "message": f"Applied: {instruction}",
    }


async def _edit_component_group_with_ai(component_id: int, group_name: str, instruction: str, body: dict, conn) -> dict:
    """Edit all child parts of a component group via a single AI call."""
    import anthropic as _anthropic
    import time as _time
    from feature_engine import FEATURE_SCHEMAS
    from feature_routes import _load_features, _do_rebuild

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # 1. Load all child parts
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, material, color, bbox_min_x, bbox_min_y, bbox_min_z, "
            "       bbox_max_x, bbox_max_y, bbox_max_z "
            "FROM parts WHERE parent_part_id = %s ORDER BY sort_order",
            (component_id,),
        )
        children = cur.fetchall()

    if not children:
        raise HTTPException(status_code=400, detail=f"Component group '{group_name}' has no child parts to edit")

    # 2. Load features for each child part
    child_contexts = []
    for child_row in children:
        cid, cname, cmat, ccolor = child_row[0], child_row[1], child_row[2], child_row[3]
        cbbox_min = child_row[4:7]
        cbbox_max = child_row[7:10]

        # Get AI branch for this child
        with conn.cursor() as cur:
            ai_uid = _get_ai_user_id(cur)
            branch_id = _ensure_user_working_branch(cur, ai_uid, cid)
        conn.commit()

        features = _load_features(conn, cid, branch_id)
        features_desc_lines = []
        for f in features:
            params_str = _json.dumps(f["params"], default=str)
            suppressed_tag = " [SUPPRESSED]" if f.get("suppressed") else ""
            features_desc_lines.append(
                f'    - id={f["id"]}, seq={f["sequence"]}, type="{f["feature_type"]}", '
                f'name="{f["name"]}"{suppressed_tag}, params={params_str}'
            )
        features_desc = "\n".join(features_desc_lines)

        bbox_desc = ""
        if cbbox_min[0] is not None:
            size_x = cbbox_max[0] - cbbox_min[0]
            size_y = cbbox_max[1] - cbbox_min[1]
            size_z = cbbox_max[2] - cbbox_min[2]
            bbox_desc = (
                f"    Bounding box: min=({cbbox_min[0]:.1f}, {cbbox_min[1]:.1f}, {cbbox_min[2]:.1f}), "
                f"max=({cbbox_max[0]:.1f}, {cbbox_max[1]:.1f}, {cbbox_max[2]:.1f}), "
                f"size=({size_x:.1f} x {size_y:.1f} x {size_z:.1f})"
            )

        child_contexts.append({
            "part_id": cid,
            "name": cname,
            "material": cmat,
            "color": ccolor,
            "branch_id": branch_id,
            "features": features,
            "features_desc": features_desc,
            "bbox_desc": bbox_desc,
        })

    # 3. Build schema description
    schema_lines = []
    skip_types = {"sketch_extrude", "sketch_revolve", "sketch_cut"}
    for ftype, schema in FEATURE_SCHEMAS.items():
        if ftype in skip_types:
            continue
        params_desc = []
        for pname, pinfo in schema["params"].items():
            ptype = pinfo.get("type", "float")
            default = pinfo.get("default")
            extra = ""
            if "min" in pinfo:
                extra += f", min={pinfo['min']}"
            if "max" in pinfo:
                extra += f", max={pinfo['max']}"
            if "options" in pinfo:
                extra += f", options={pinfo['options']}"
            params_desc.append(f'      "{pname}": {ptype} (default={default}{extra})')
        params_block = "\n".join(params_desc)
        schema_lines.append(f'  "{ftype}" ({schema["category"]}):\n{params_block}')
    schemas_desc = "\n".join(schema_lines)

    # 4. Build multi-part context
    parts_context = []
    for cc in child_contexts:
        parts_context.append(
            f'  Part "{cc["name"]}" (id={cc["part_id"]}, material={cc["material"]}, color={cc["color"]}):\n'
            f'{cc["features_desc"]}\n{cc["bbox_desc"]}'
        )
    all_parts_desc = "\n\n".join(parts_context)

    # 5. System prompt for multi-part editing
    system_prompt = f"""You are a parametric CAD feature editor. You are editing a MULTI-PART component group called "{group_name}".
The group contains {len(child_contexts)} child parts. You can modify features on ANY of these parts.

AVAILABLE FEATURE TYPES AND PARAMETERS:
{schemas_desc}

OUTPUT FORMAT:
Return a JSON object mapping part_id to an array of operations for that part.
Use "new_1", "new_2", etc. as keys to CREATE new parts in this group.

{{
  "<existing_part_id>": [
    {{"action": "add", "feature_type": "<type>", "name": "<name>", "params": {{...}} }},
    {{"action": "modify", "feature_id": <id>, "params": {{...}} }},
    {{"action": "delete", "feature_id": <id> }},
    {{"action": "set_material", "material": "<material_key>", "color": "#hex" }}
  ],
  "new_1": {{
    "action": "create_part",
    "name": "Grip Tape",
    "material": "rubber_natural",
    "color": "#2C2C2C",
    "features": [
      {{"feature_type": "box", "name": "Tape Base", "params": {{"width": 200, "depth": 800, "height": 2, "centered": true, "position": [0, 0, 105]}} }},
      {{"feature_type": "fillet", "name": "Tape Fillet", "params": {{"radius": 55, "edge_refs": ["|Z"]}} }}
    ]
  }}
}}

Only include part IDs that need changes. Use "new_N" keys to add new parts to the group.

RULES:
- CRITICAL: Return ONLY the JSON object. No thinking, no explanations, no text before or after. Just the raw JSON.
- All dimensions are in millimetres. Z is UP (vertical).
- Feature IDs for modify/delete must reference existing feature IDs from the current features list.
- Use "position": [x, y, z] in feature params to place primitives at specific world coordinates.
- Cylinders and torus support "axis": "X"/"Y"/"Z" (default "Z"). Torus axis="Z" = flat donut; axis="Y" = upright wheel.
- All primitives support "rotation": [rx, ry, rz] degrees for arbitrary angles (applied before position).
- All parts share the SAME world coordinate system. Parts must connect properly at shared boundaries.
- For fillet/chamfer: use "edge_refs" with CadQuery selectors like "|Z", ">Z", etc.
- When user says "align part A with part B" or "make part A match part B", modify part A's features to match part B's dimensions/shape/position.
- When parts need the same shape, copy the relevant feature params (width, depth, height, position, fillet radius, etc.)

COORDINATE SYSTEM:
CadQuery Z-up: X = right, Y = forward/depth, Z = UP (vertical/height).
- Ground plane is at Z=0. Objects sit ON the ground.
- Face selectors: ">Z" = top, "<Z" = bottom, ">X" = right, ">Y" = front."""

    # 6. User message
    user_message = f"""Component group: {group_name}
Instruction: {instruction}

Child parts and their current features:

{all_parts_desc}

Return a JSON object mapping part_id → operations array. Only include parts that need changes."""

    # 7. Call Claude
    client = _anthropic.Anthropic(api_key=api_key)
    _ai_model = "claude-sonnet-4-6"
    print(f"[AI-GROUP-EDIT] Editing component '{group_name}' (id={component_id}) with {len(child_contexts)} children")
    print(f"[AI-GROUP-EDIT] Instruction: {instruction}")

    _t0 = _time.time()
    response = client.messages.create(
        model=_ai_model,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    _duration_ms = int((_time.time() - _t0) * 1000)

    raw_response = response.content[0].text.strip()
    print(f"[AI-GROUP-EDIT] Raw response ({_duration_ms}ms): {raw_response[:1500]}")

    # 8. Parse response
    def _parse_ai_json(text: str):
        jt = text.strip()
        if jt.startswith("```"):
            lines = jt.split("\n")
            jt = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        if not jt.startswith("{"):
            start = jt.find("{")
            end = jt.rfind("}")
            if start >= 0 and end > start:
                jt = jt[start:end + 1]
        return _json.loads(jt)

    try:
        result = _parse_ai_json(raw_response)
    except (_json.JSONDecodeError, ValueError) as e:
        print(f"[AI-GROUP-EDIT] JSON parse failed: {e}, retrying...")
        retry_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system="You are a JSON-only responder. Return ONLY a valid JSON object mapping part_id to operations arrays.",
            messages=[
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": raw_response},
                {"role": "user", "content": f"Your response was not valid JSON. Error: {e}\nReturn ONLY the JSON object."},
            ],
        )
        raw_response = retry_response.content[0].text.strip()
        try:
            result = _parse_ai_json(raw_response)
        except (_json.JSONDecodeError, ValueError) as e2:
            raise HTTPException(status_code=400, detail=f"AI returned invalid JSON: {e2}")

    if not isinstance(result, dict):
        raise HTTPException(status_code=400, detail=f"Expected JSON object mapping part_id → operations, got {type(result).__name__}")

    # 9. Apply operations per child part (and create new parts)
    all_applied = []
    all_errors = []
    all_meshes = []
    child_by_id = {str(cc["part_id"]): cc for cc in child_contexts}

    # Get assembly_id from component
    with conn.cursor() as cur:
        cur.execute("SELECT assembly_id FROM parts WHERE id = %s", (component_id,))
        assembly_id = cur.fetchone()[0]

    # Count existing children for sort_order
    existing_child_count = len(child_contexts)

    for part_id_str, operations in result.items():
        # --- Handle new part creation ---
        if part_id_str.startswith("new"):
            if not isinstance(operations, dict) or operations.get("action") != "create_part":
                all_errors.append(f"Key '{part_id_str}': expected create_part action object")
                continue
            p_name = operations.get("name", f"New Part")
            p_material = operations.get("material", "steel")
            p_color = operations.get("color", "#888888")
            feature_defs = operations.get("features", [])
            if not feature_defs:
                all_errors.append(f"create_part '{p_name}': no features")
                continue

            existing_child_count += 1
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO parts
                           (assembly_id, name, description, cadquery_script,
                            position_x, position_y, position_z,
                            material, color, feature_tree_mode, parent_part_id, sort_order)
                           VALUES (%s, %s, %s, %s, 0, 0, 0, %s, %s, true, %s, %s)
                           RETURNING id""",
                        (assembly_id, p_name, f"AI-created in group: {group_name}",
                         "# Feature tree part", p_material, p_color, component_id, existing_child_count),
                    )
                    new_part_id = cur.fetchone()[0]
                    branch_id = _ensure_main_branch(cur, new_part_id)

                    for seq, fdef in enumerate(feature_defs, 1):
                        ftype = fdef.get("feature_type")
                        if ftype not in FEATURE_SCHEMAS:
                            all_errors.append(f"create_part '{p_name}' feature {seq}: unknown type '{ftype}'")
                            continue
                        fname = fdef.get("name", f"{FEATURE_SCHEMAS[ftype]['label']}")
                        params = fdef.get("params", {})
                        cur.execute(
                            "INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, source) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                            (new_part_id, branch_id, ftype, fname, seq, _json.dumps(params), "ai"),
                        )
                conn.commit()
                print(f"[AI-GROUP-EDIT] Created new part '{p_name}' (id={new_part_id}) in group '{group_name}'")
                all_applied.append({"action": "create_part", "part_id": new_part_id, "name": p_name})

                # Rebuild the new part
                try:
                    rebuild_result = _do_rebuild(new_part_id, conn, branch_id=branch_id)
                    mesh = rebuild_result.get("mesh")
                    if mesh:
                        mesh["id"] = new_part_id
                        all_meshes.append(mesh)
                except Exception as e:
                    all_errors.append(f"New part '{p_name}' rebuild failed: {e}")
            except Exception as e:
                all_errors.append(f"create_part '{p_name}': {e}")
            continue

        # --- Handle operations on existing parts ---
        cc = child_by_id.get(part_id_str)
        if not cc:
            all_errors.append(f"Part ID {part_id_str} not found in component group")
            continue

        cid = cc["part_id"]
        branch_id = cc["branch_id"]
        features = cc["features"]
        existing_ids = {f["id"] for f in features}
        max_seq = max((f["sequence"] for f in features), default=0)

        if not isinstance(operations, list):
            all_errors.append(f"Part {cid}: operations must be a list")
            continue

        part_applied = []
        with conn.cursor() as cur:
            for i, op in enumerate(operations):
                action = op.get("action")
                try:
                    if action == "add":
                        ftype = op.get("feature_type")
                        if ftype not in FEATURE_SCHEMAS:
                            all_errors.append(f"Part {cid} op {i}: unknown type '{ftype}'")
                            continue
                        max_seq += 1
                        fname = op.get("name", f"{FEATURE_SCHEMAS[ftype]['label']} (AI)")
                        params = op.get("params", {})
                        cur.execute(
                            "INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, source) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                            (cid, branch_id, ftype, fname, max_seq, _json.dumps(params), "ai"),
                        )
                        new_id = cur.fetchone()[0]
                        part_applied.append({"action": "add", "feature_id": new_id, "type": ftype, "name": fname, "part_id": cid})

                    elif action == "modify":
                        fid = op.get("feature_id")
                        if fid not in existing_ids:
                            all_errors.append(f"Part {cid} op {i}: feature_id {fid} not found")
                            continue
                        new_params = op.get("params", {})
                        if not new_params:
                            continue
                        existing_feat = next(f for f in features if f["id"] == fid)
                        merged = {**existing_feat["params"], **new_params}
                        cur.execute(
                            "UPDATE features SET params = %s, updated_at = now() WHERE id = %s AND branch_id = %s",
                            (_json.dumps(merged), fid, branch_id),
                        )
                        part_applied.append({"action": "modify", "feature_id": fid, "params": new_params, "part_id": cid})

                    elif action == "delete":
                        fid = op.get("feature_id")
                        if fid not in existing_ids:
                            all_errors.append(f"Part {cid} op {i}: feature_id {fid} not found")
                            continue
                        cur.execute("DELETE FROM features WHERE id = %s AND branch_id = %s", (fid, branch_id))
                        existing_ids.discard(fid)
                        part_applied.append({"action": "delete", "feature_id": fid, "part_id": cid})

                    elif action == "set_material":
                        mat = op.get("material", "steel")
                        color = op.get("color", "#888888")
                        cur.execute(
                            "UPDATE parts SET material = %s, color = %s, updated_at = now() WHERE id = %s",
                            (mat, color, cid),
                        )
                        part_applied.append({"action": "set_material", "material": mat, "color": color, "part_id": cid})

                    else:
                        all_errors.append(f"Part {cid} op {i}: unknown action '{action}'")
                except Exception as e:
                    all_errors.append(f"Part {cid} op {i}: {e}")

        conn.commit()
        all_applied.extend(part_applied)

        # Rebuild this child part
        if part_applied:
            try:
                rebuild_result = _do_rebuild(cid, conn, branch_id=branch_id)
                mesh = rebuild_result.get("mesh")
                if mesh:
                    mesh["id"] = cid
                    all_meshes.append(mesh)
            except Exception as e:
                all_errors.append(f"Part {cid} rebuild failed: {e}")

    if not all_applied:
        detail = "AI produced no valid operations for any part."
        if all_errors:
            detail += " Errors: " + "; ".join(all_errors)
        raise HTTPException(status_code=400, detail=detail)

    # 10. Log
    _log_ai_interaction(
        conn,
        interaction_type="group_edit",
        instruction=instruction,
        part_id=component_id,
        system_prompt=system_prompt,
        user_message=user_message,
        raw_response=raw_response,
        parsed_result=result,
        applied_ops=all_applied,
        errors=all_errors if all_errors else None,
        model=_ai_model,
        duration_ms=_duration_ms,
        success=bool(all_applied),
    )

    # Build summary message
    parts_modified = set(a.get("part_id") for a in all_applied)
    part_names = []
    for pid in parts_modified:
        cc = child_by_id.get(str(pid))
        if cc:
            part_names.append(cc["name"])
    summary = f"Modified {len(parts_modified)} part(s): {', '.join(part_names)}. {len(all_applied)} operations applied."

    return {
        "part_id": component_id,
        "mesh": all_meshes[0] if len(all_meshes) == 1 else None,
        "meshes": all_meshes,
        "on_branch": True,
        "ai_operations": all_applied,
        "ai_errors": all_errors if all_errors else None,
        "ai_message": summary,
    }


async def _edit_part_with_ai_features(part_id: int, name: str, instruction: str, body: dict, conn) -> dict:
    """Feature-tree AI editing: Claude returns structured feature operations instead of raw scripts."""
    import anthropic as _anthropic
    from feature_engine import FEATURE_SCHEMAS
    from feature_routes import _load_features, _do_rebuild

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # 1. Get or create the AI's working branch with copied features
    with conn.cursor() as cur:
        ai_uid = _get_ai_user_id(cur)
        branch_id = _ensure_user_working_branch(cur, ai_uid, part_id)
    conn.commit()

    # 2. Load current features from AI's branch
    features = _load_features(conn, part_id, branch_id)
    if not features:
        raise HTTPException(status_code=400, detail="Feature-tree part has no features to edit")

    # 3. Build context: current features description
    features_desc_lines = []
    for f in features:
        params_str = _json.dumps(f["params"], default=str)
        suppressed_tag = " [SUPPRESSED]" if f.get("suppressed") else ""
        features_desc_lines.append(
            f'  - id={f["id"]}, seq={f["sequence"]}, type="{f["feature_type"]}", '
            f'name="{f["name"]}"{suppressed_tag}, params={params_str}'
        )
    features_desc = "\n".join(features_desc_lines)

    # 4. Build bbox context for spatial awareness
    bbox_desc = ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT bbox_min_x, bbox_min_y, bbox_min_z, bbox_max_x, bbox_max_y, bbox_max_z FROM parts WHERE id = %s",
            (part_id,),
        )
        brow = cur.fetchone()
        if brow and brow[0] is not None:
            bbox_desc = (
                f"\nCurrent bounding box (mm): "
                f"min=({brow[0]:.1f}, {brow[1]:.1f}, {brow[2]:.1f}), "
                f"max=({brow[3]:.1f}, {brow[4]:.1f}, {brow[5]:.1f}), "
                f"size=({brow[3]-brow[0]:.1f} x {brow[4]-brow[1]:.1f} x {brow[5]-brow[2]:.1f})"
            )

    # 5. Build schema description for available feature types (exclude sketch-based ones the AI can't use)
    schema_lines = []
    skip_types = {"sketch_extrude", "sketch_revolve", "sketch_cut"}
    for ftype, schema in FEATURE_SCHEMAS.items():
        if ftype in skip_types:
            continue
        params_desc = []
        for pname, pinfo in schema["params"].items():
            ptype = pinfo.get("type", "float")
            default = pinfo.get("default")
            extra = ""
            if "min" in pinfo:
                extra += f", min={pinfo['min']}"
            if "max" in pinfo:
                extra += f", max={pinfo['max']}"
            if "options" in pinfo:
                extra += f", options={pinfo['options']}"
            params_desc.append(f'      "{pname}": {ptype} (default={default}{extra})')
        params_block = "\n".join(params_desc)
        schema_lines.append(f'  "{ftype}" ({schema["category"]}):\n{params_block}')
    schemas_desc = "\n".join(schema_lines)

    # 6. Build the system prompt
    system_prompt = f"""You are a parametric CAD feature editor. You modify parts by outputting structured feature operations.

AVAILABLE FEATURE TYPES AND PARAMETERS:
{schemas_desc}

OPERATION FORMAT:
Return ONLY a JSON array of operations. Each operation is one of:

1. Add a new feature:
   {{"action": "add", "feature_type": "<type>", "name": "<descriptive name>", "params": {{...}}}}

2. Modify an existing feature's parameters:
   {{"action": "modify", "feature_id": <id>, "params": {{...only changed params...}}}}

3. Delete an existing feature:
   {{"action": "delete", "feature_id": <id>}}

4. Change the part's material and/or color:
   {{"action": "set_material", "material": "<material_key>", "color": "#hexcolor"}}

   AVAILABLE MATERIALS:
   Metals: steel (#888888), stainless_304 (#AAAAAA), stainless_316 (#B0B0B0), chromoly_4130 (#666677),
     cast_iron (#555555), aluminum_6061 (#C0C0C0), aluminum_7075 (#B0B0B8), aluminum_anodized_black (#1A1A1A),
     titanium_gr5 (#8899AA), copper (#B87333), brass (#B5A642), bronze (#CD7F32), gold (#FFD700),
     silver (#E8E8E8), nickel (#A0A5A8), zinc (#9EAEB5), chrome (#D4D4D4)
   Composites: carbon_fiber (#222222), fiberglass (#E8E4C9)
   Plastics: abs_plastic (#F5F5DC), pla_plastic (#E0E0E0), petg_plastic (#DAE8F0), nylon_pa6 (#E8E8D0),
     polycarbonate (#E8EEF2), acetal_pom (#F0F0E8), polypropylene (#F5F5F0), hdpe (#F0F0F0), acrylic_pmma (#F0F8FF)
   Rubber: rubber_natural (#2C2C2C), rubber_silicone (#D8D8D8), rubber_neoprene (#333333), rubber_epdm (#1A1A1A), tpu_flexible (#E8E0D0)
   Wood: wood_oak (#8B6914), wood_walnut (#5C4033), wood_maple (#C4A35A), wood_cherry (#9B4722),
     wood_pine (#DEB887), wood_bamboo (#C9B57A), plywood (#C8AD7F), mdf (#A0845C)
   Stone: glass (#E0F0FF), ceramic (#F5F0E8), concrete (#A0A0A0), marble (#F0EDE6), granite (#696969)
   Soft: leather (#8B4513), fabric_canvas (#C4B99A), foam_pu (#F5F0C8), cork (#B5904E)

   NOTE: This sets material for the ENTIRE part. All features in one part share the same material.
   If the user wants different materials on different sections, tell them to create the object as
   separate parts in the assembly (e.g., "Create a chair" makes seat, legs, and backrest as separate parts).

COORDINATE SYSTEM:
CadQuery Z-up: X = right, Y = forward/depth, Z = UP (vertical/height).
The frontend auto-converts Z-up to Y-up for display — you just use Z as vertical.
- "height" of an object = Z axis. "width" = X axis. "depth" = Y axis.
- Ground plane is at Z=0. Objects sit ON the ground, so position them with Z >= 0.
- Face selectors: ">Z" = top face, "<Z" = bottom face, ">X" = right face, ">Y" = front face.
- Edge selectors: "|Z" = vertical edges, "|X" = edges parallel to X, "|Y" = edges parallel to Y.

CRITICAL — HOW THE FEATURE ENGINE WORKS:
Features are applied SEQUENTIALLY to ONE shape. There is NO concept of separate bodies.

1. The FIRST feature must be a primitive (box, cylinder, sphere) — this creates the base shape.
2. Every SUBSEQUENT primitive is COMBINED with the existing shape:
   - "operation": "boss" (default) → UNION (adds material)
   - "operation": "cut" → SUBTRACTION (removes material from existing shape)
3. Every primitive supports a "position": [x, y, z] param that offsets it BEFORE combining.
   Use "position" to place cut/boss primitives at the right location.
   Example: {{"feature_type": "box", "params": {{"width": 50, "height": 20, "depth": 30, "position": [0, 60, -50], "operation": "cut"}}}}
   This creates a box centered at (0, 60, -50) and subtracts it from the existing shape.
4. Cylinders and torus support "axis": "X"/"Y"/"Z" (default "Z"). Torus axis="Z" = flat donut; axis="Y" = upright wheel.
5. All primitives support "rotation": [rx, ry, rz] in degrees for arbitrary angles. Applied before position.
6. NEVER use "translate" to position primitives — translate moves the ENTIRE accumulated shape.
   Only use translate if you genuinely want to reposition the whole part.
7. "shell" hollows out the shape — use this instead of creating an inner sphere/box to subtract.
8. "hole" creates a cylindrical hole on a face — use face_selector and click_point.

RULES:
- CRITICAL: Return ONLY the JSON array. No thinking, no analysis, no explanations, no text before or after the JSON. Just the raw JSON array.
- All dimensions are in millimetres. Z is UP (vertical).
- For "add" operations, new features are appended to the end of the feature tree.
- For "modify", only include the params you want to change; others are kept as-is.
- To ADD material: use a primitive with "operation": "boss" and "position": [x,y,z].
- To REMOVE material: use a primitive with "operation": "cut" and "position": [x,y,z].
- For holes: use "face_selector" (e.g. ">Z" for top face, "<Z" for bottom, ">X" for right, ">Y" for front) and optionally "click_point" [x, y, z] for positioning on that face.
  IMPORTANT: click_point must be in WORLD coordinates (not face-local). Use the bounding box to calculate positions.
  For a centered box (width x depth x height): X ranges ±width/2, Y ranges ±depth/2, Z ranges ±height/2.
  Face ">Z" (top): click_point [x, y, z_max]. Face ">Y" (front): click_point [x, y_max, z]. Face ">X" (right): click_point [x_max, y, z].
  Keep hole centers at least hole_radius + 0.5mm away from face edges to avoid geometry failures.
  When the user says "1mm from the edge", place the center at edge_position + hole_radius + 1mm.
- For fillet/chamfer: use "edge_refs" with CadQuery edge selectors like "|Z" (vertical edges), ">Z" (top edges), etc.
- To MOVE a hole: use "modify" on its feature_id with updated "click_point" coordinates.
- You can combine multiple operations (e.g., add several holes, modify a dimension and add a fillet).
- Feature IDs for modify/delete must reference existing feature IDs from the current features list.
- If the instruction says to adjust positions, use "modify" with new click_point values calculated from the bounding box.
- Use real engineering dimensions: fillet=10-25% of wall, chamfer=0.5-2mm, walls plastic 1.5-3mm / metal 0.8-3mm.
- Screw holes: M3=3.4mm, M4=4.4mm, M5=5.4mm, M6=6.4mm, M8=8.4mm close fit.
- Counterbore: dia=1.7x screw, depth=1x screw diameter."""

    # 7. Build the user message
    user_message = f"""Part name: {name}
Instruction: {instruction}

Current features on this part:
{features_desc}
{bbox_desc}

Return a JSON array of feature operations to apply."""

    # 8. Call Claude
    import time as _time
    client = _anthropic.Anthropic(api_key=api_key)
    _ai_model = "claude-sonnet-4-6"
    print(f"[AI-FEATURES] Calling Claude for part {part_id} branch {branch_id}, instruction: {instruction}")
    print(f"[AI-FEATURES] Current features: {features_desc}")

    _t0 = _time.time()
    response = client.messages.create(
        model=_ai_model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    _duration_ms = int((_time.time() - _t0) * 1000)

    raw_response = response.content[0].text.strip()
    print(f"[AI-FEATURES] Raw response ({_duration_ms}ms): {raw_response[:1000]}")

    # 9. Parse JSON response (strip markdown fences if present)
    def _parse_ai_json(text: str):
        jt = text.strip()
        if jt.startswith("```"):
            lines = jt.split("\n")
            jt = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        # Also handle case where AI wraps in extra text before/after JSON
        if not jt.startswith("["):
            # Try to find JSON array within the text
            start = jt.find("[")
            end = jt.rfind("]")
            if start >= 0 and end > start:
                jt = jt[start:end + 1]
        return _json.loads(jt)

    try:
        operations = _parse_ai_json(raw_response)
    except (_json.JSONDecodeError, ValueError) as e:
        # Retry once — send error back to Claude for self-correction
        print(f"[AI-FEATURES] JSON parse failed: {e}, retrying...")
        retry_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system="You are a JSON-only responder. Return ONLY a valid JSON array, no text before or after.",
            messages=[
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": raw_response},
                {"role": "user", "content": f"Your response was not valid JSON. Error: {e}\nReturn ONLY the JSON array of operations, nothing else."},
            ],
        )
        raw_response = retry_response.content[0].text.strip()
        print(f"[AI-FEATURES] Retry response: {raw_response[:1000]}")
        try:
            operations = _parse_ai_json(raw_response)
        except (_json.JSONDecodeError, ValueError) as e2:
            raise HTTPException(status_code=400, detail=f"AI returned invalid JSON after retry: {e2}\nResponse: {raw_response[:500]}")

    if not isinstance(operations, list):
        raise HTTPException(status_code=400, detail=f"AI response must be a JSON array of operations, got: {type(operations).__name__}")

    # 9b. Validate operations with Pydantic models
    from feature_models import parse_ai_operations, validate_feature_params
    try:
        validated_ops = parse_ai_operations(operations)
    except ValueError as e:
        print(f"[AI-FEATURES] Operation validation failed: {e}")
        # Fall back to raw ops for backward compatibility
        validated_ops = None

    # 10. Apply each operation to the AI's branch feature tree
    existing_ids = {f["id"] for f in features}
    max_seq = max((f["sequence"] for f in features), default=0)
    applied = []
    errors = []

    with conn.cursor() as cur:
        ops_to_apply = validated_ops if validated_ops else [type('', (), op)() if False else op for op in operations]
        for i, op in enumerate(operations):
            # Use validated op if available
            if validated_ops and i < len(validated_ops):
                vop = validated_ops[i]
                action = vop.action
            else:
                action = op.get("action")
            try:
                if action == "add":
                    ftype = op.get("feature_type") if not validated_ops else validated_ops[i].feature_type
                    if ftype not in FEATURE_SCHEMAS:
                        errors.append(f"Op {i}: unknown feature type '{ftype}'")
                        continue
                    max_seq += 1
                    fname = op.get("name", f"{FEATURE_SCHEMAS[ftype]['label']} (AI)") if not validated_ops else (validated_ops[i].name or f"{FEATURE_SCHEMAS[ftype]['label']} (AI)")
                    params = op.get("params", {}) if not validated_ops else (validated_ops[i].params or {})
                    # Validate params against feature schema
                    try:
                        params = validate_feature_params(ftype, params, FEATURE_SCHEMAS)
                    except ValueError as ve:
                        errors.append(f"Op {i}: param validation failed — {ve}")
                        continue
                    cur.execute(
                        "INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, source) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (part_id, branch_id, ftype, fname, max_seq,
                         _json.dumps(params), "ai"),
                    )
                    new_id = cur.fetchone()[0]
                    applied.append({"action": "add", "feature_id": new_id, "type": ftype, "name": fname})

                elif action == "modify":
                    fid = validated_ops[i].feature_id if validated_ops and i < len(validated_ops) else op.get("feature_id")
                    if fid not in existing_ids:
                        errors.append(f"Op {i}: feature_id {fid} not found on this branch")
                        continue
                    new_params = validated_ops[i].params if validated_ops and i < len(validated_ops) else op.get("params", {})
                    if not new_params:
                        errors.append(f"Op {i}: modify with no params")
                        continue
                    # Merge new params into existing, validate merged result
                    existing_feat = next(f for f in features if f["id"] == fid)
                    merged = {**existing_feat["params"], **new_params}
                    try:
                        merged = validate_feature_params(existing_feat["feature_type"], merged, FEATURE_SCHEMAS)
                    except ValueError as ve:
                        errors.append(f"Op {i}: param validation failed — {ve}")
                        continue
                    cur.execute(
                        "UPDATE features SET params = %s, updated_at = now() WHERE id = %s AND branch_id = %s",
                        (_json.dumps(merged), fid, branch_id),
                    )
                    applied.append({"action": "modify", "feature_id": fid, "params": new_params})

                elif action == "delete":
                    fid = op.get("feature_id")
                    if fid not in existing_ids:
                        errors.append(f"Op {i}: feature_id {fid} not found on this branch")
                        continue
                    cur.execute(
                        "DELETE FROM features WHERE id = %s AND branch_id = %s",
                        (fid, branch_id),
                    )
                    existing_ids.discard(fid)
                    applied.append({"action": "delete", "feature_id": fid})

                elif action == "set_material":
                    mat = op.get("material", "steel")
                    color = op.get("color", "#888888")
                    cur.execute(
                        "UPDATE parts SET material = %s, color = %s, updated_at = now() WHERE id = %s",
                        (mat, color, part_id),
                    )
                    msg = op.get("message", f"Material set to {mat}")
                    applied.append({"action": "set_material", "material": mat, "color": color, "message": msg})

                else:
                    errors.append(f"Op {i}: unknown action '{action}'")

            except Exception as e:
                errors.append(f"Op {i} ({action}): {e}")

    conn.commit()

    print(f"[AI-FEATURES] Applied {len(applied)} operations, {len(errors)} errors")
    if errors:
        print(f"[AI-FEATURES] Errors: {errors}")

    if not applied:
        detail = "AI produced no valid operations."
        if errors:
            detail += " Errors: " + "; ".join(errors)
        print(f"[AI-FEATURES] FAILED: {detail}")
        raise HTTPException(status_code=400, detail=detail)

    # 11. Rebuild the part from the AI's branch features
    try:
        rebuild_result = _do_rebuild(part_id, conn, branch_id=branch_id)
    except Exception as rebuild_exc:
        print(f"[AI-FEATURES] Rebuild exception: {rebuild_exc}")
        rebuild_result = {"rebuild_status": "failed", "feature_errors": {"exception": str(rebuild_exc)}}

    if rebuild_result.get("rebuild_status") == "failed":
        # Rebuild failed — still return what we can, the operations are saved on the branch
        errors.append(f"Rebuild failed: {rebuild_result.get('feature_errors', {})}")
        print(f"[AI-FEATURES] Rebuild failed: {rebuild_result.get('feature_errors', {})}")

    mesh = rebuild_result.get("mesh")
    if mesh:
        mesh["id"] = part_id

    # 12. Create a version checkpoint on the AI's branch
    with conn.cursor() as cur:
        instruction_preview = instruction[:60]
        try:
            _create_version(cur, part_id, branch_id, "",
                            label=f"AI edit: {instruction_preview}",
                            author_type="ai", author_info="edit-with-ai-features", auto=True)
        except Exception:
            pass
    conn.commit()

    # 13. Log AI interaction to database
    _log_ai_interaction(
        conn,
        interaction_type="feature_edit",
        instruction=instruction,
        part_id=part_id,
        branch_id=branch_id,
        system_prompt=system_prompt,
        user_message=user_message,
        raw_response=raw_response,
        parsed_result=operations,
        applied_ops=applied,
        errors=errors if errors else None,
        model=_ai_model,
        duration_ms=_duration_ms,
        success=bool(applied),
    )

    return {
        "part_id": part_id,
        "mesh": mesh,
        "on_branch": True,
        "ai_operations": applied,
        "ai_errors": errors if errors else None,
    }


@app.post("/assemblies/{assembly_id}/create-part-with-ai-features")
async def create_part_with_ai_features(assembly_id: int, body: dict, user: UserInfo = Depends(get_current_user)):
    """AI creates a brand-new part using structured feature operations (parametric from day one)."""
    import anthropic as _anthropic
    from feature_engine import FEATURE_SCHEMAS
    from feature_routes import _load_features, _do_rebuild

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    instruction = (body.get("instruction") or "").strip()
    part_name = (body.get("name") or "").strip() or "AI Part"
    if not instruction:
        raise HTTPException(status_code=400, detail="Provide 'instruction'")

    conn = get_connection()
    try:
        # 1. Verify assembly exists
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM assemblies WHERE id = %s", (assembly_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Assembly not found")

        # 2. Build schema description for available feature types
        schema_lines = []
        skip_types = {"sketch_extrude", "sketch_revolve", "sketch_cut"}
        for ftype, schema in FEATURE_SCHEMAS.items():
            if ftype in skip_types:
                continue
            params_desc = []
            for pname, pinfo in schema["params"].items():
                ptype = pinfo.get("type", "float")
                default = pinfo.get("default")
                extra = ""
                if "min" in pinfo:
                    extra += f", min={pinfo['min']}"
                if "max" in pinfo:
                    extra += f", max={pinfo['max']}"
                if "options" in pinfo:
                    extra += f", options={pinfo['options']}"
                params_desc.append(f'      "{pname}": {ptype} (default={default}{extra})')
            params_block = "\n".join(params_desc)
            schema_lines.append(f'  "{ftype}" ({schema["category"]}):\n{params_block}')
        schemas_desc = "\n".join(schema_lines)

        # 3. System prompt for part creation
        system_prompt = f"""You are a parametric CAD feature designer. You create new parts by outputting a structured feature tree.

CRITICAL: Return ONLY the raw JSON object. No planning, no thinking, no explanations, no markdown fences, no text before or after. Just the JSON.

AVAILABLE FEATURE TYPES AND PARAMETERS:
{schemas_desc}

OUTPUT FORMAT:
Return a JSON object. You can create MULTIPLE parts when an object needs different materials/colors
(e.g., a chair with wooden seat + metal legs = 3+ separate parts).

For a single part:
{{
  "name": "<short descriptive part name>",
  "material": "<material_key>",
  "color": "#hexcolor",
  "features": [
    {{"feature_type": "<type>", "name": "<descriptive name>", "params": {{...}}}}
  ]
}}

For multiple parts (PREFERRED for objects with different materials):
{{
  "group_name": "Chair",
  "parts": [
    {{
      "name": "Chair Seat",
      "material": "wood_oak",
      "color": "#8B6914",
      "features": [ ... ]
    }},
    {{
      "name": "Front Left Leg",
      "material": "chrome",
      "color": "#D4D4D4",
      "features": [ ... ]
    }}
  ]
}}
IMPORTANT: Always include "group_name" when returning multiple parts. It becomes the parent group name in the part tree.

AVAILABLE MATERIALS (use the key, not the name):
Metals: steel, stainless_304, stainless_316, chromoly_4130, cast_iron, aluminum_6061, aluminum_7075,
  aluminum_anodized_black, titanium_gr5, copper, brass, bronze, gold, silver, nickel, zinc, chrome
Composites: carbon_fiber, fiberglass
Plastics: abs_plastic, pla_plastic, petg_plastic, nylon_pa6, polycarbonate, acetal_pom, polypropylene, hdpe, acrylic_pmma
Rubber: rubber_natural, rubber_silicone, rubber_neoprene, rubber_epdm, tpu_flexible
Wood: wood_oak, wood_walnut, wood_maple, wood_cherry, wood_pine, wood_bamboo, plywood, mdf
Stone: glass, ceramic, concrete, marble, granite
Soft: leather, fabric_canvas, foam_pu, cork

WHEN TO USE MULTI-PART:
- Object has parts with different materials (wooden seat + metal legs)
- Object has clearly distinct components (chair = seat + legs + backrest)
- Assembly of different sub-objects
Use single part for: simple solid objects made from one material (bracket, housing, gear)

CRITICAL — MULTI-PART POSITIONING:
All parts share the SAME world coordinate system. You MUST position features so parts connect properly.
Think of the COMPLETE object first, decide exact world coordinates for every component, then split into parts.

Example — Chair (Z is up):
  Step 1: Plan the full chair: seat at Z=450, legs Z=0→450, backrest Z=450→900
  Step 2: Split into parts, using the SAME world coordinates:
    Part "Seat": box at position [0, 0, 450] — the seat sits at Z=450
    Part "Chair Legs": 4 cylinders at positions [±170, ±170, 225] height=450 — legs touch ground (Z=0) and reach seat (Z=450)
    Part "Backrest": box at position [0, -185, 675] — backrest sits on top of seat and rises up

  The legs MUST reach exactly to Z=450 (seat bottom). The backrest MUST start at Z=450+seat_thickness/2.
  Parts connect at shared coordinate boundaries — no gaps, no overlaps.

Example — Skateboard (Z is up):
  Step 1: Plan: deck at Z=80 (above trucks), trucks at Z=40, wheels at Z=26 (radius=26, touching ground)
  Step 2: Split into parts:
    Part "Deck": box 800×200×10mm at position [0,0,85], material=wood_maple, color="#C4A35A"
      → fillet all top edges (2mm) for smooth deck surface
      → fillet the short-end edges with larger radius for nose/tail rounding
    Part "Front Truck": box 150×30×15 at position [0,230,40] + cylinder axis="Z" r=5 h=200 for axle, material=aluminum_6061
    Part "Rear Truck": same as front at position [0,-230,40]
    Part "Wheels": 4 cylinders with "axis":"Y" (wheels spin on Y-axis), radius=26, height=20
      → positions: [±90, 230, 26] and [±90, -230, 26] — wheels sit on ground (Z=0+radius)
  CRITICAL: wheels use "axis":"Y" so they are oriented correctly (rolling along Y). Default "axis":"Z" would make them upright like coins!

WRONG: Creating each part centered at [0,0,0] independently — parts will overlap at origin.
CORRECT: Plan world positions first, then each part's features use those WORLD positions.

COORDINATE SYSTEM:
CadQuery Z-up: X = right, Y = forward/depth, Z = UP (vertical/height).
The frontend auto-converts Z-up to Y-up for display — you just use Z as vertical.
- "height" of an object = Z axis. "width" = X axis. "depth" = Y axis.
- Ground plane is at Z=0. Objects sit ON the ground, so position them with Z >= 0.
- CadQuery "centered: true" centers on all axes. For objects sitting on ground, you may need to position Z = height/2.
- Face selectors: ">Z" = top face, "<Z" = bottom face, ">X" = right face, ">Y" = front face.
- Edge selectors: "|Z" = vertical edges, "|X" = edges parallel to X, "|Y" = edges parallel to Y.

CRITICAL — HOW THE FEATURE ENGINE WORKS:
Features are applied SEQUENTIALLY to ONE shape. There is NO concept of separate bodies.

1. The FIRST feature must be a primitive (box, cylinder, sphere) — this creates the base shape.
2. Every SUBSEQUENT primitive is COMBINED with the existing shape:
   - "operation": "boss" (default) → UNION (adds material)
   - "operation": "cut" → SUBTRACTION (removes material from existing shape)
3. Every primitive supports a "position": [x, y, z] param that offsets it BEFORE combining.
   Use "position" to place cut/boss primitives at the right location relative to the base shape.
   Example: {{"feature_type": "box", "params": {{"width": 50, "height": 20, "depth": 30, "position": [0, 60, -50], "operation": "cut"}}}}
   This creates a box centered at (0, 60, -50) and subtracts it from the existing shape.
4. Cylinders and torus support "axis": "X", "Y", or "Z" (default "Z") to set their orientation:
   - "axis": "Z" → vertical (default). Cylinder height along Z. Torus lies FLAT (horizontal donut).
   - "axis": "X" → axle along X. Wheel stands upright facing left/right.
   - "axis": "Y" → axle along Y. Wheel stands upright facing front/back (bicycle wheels, car wheels).
   CRITICAL for wheels: Default torus axis="Z" makes the wheel LIE FLAT on the ground like a frisbee!
   For bicycle/car wheels that stand UPRIGHT, you MUST use axis="Y" (or axis="X").
   Example bicycle wheel: {{"feature_type": "torus", "params": {{"major_radius": 330, "minor_radius": 20, "axis": "Y", "position": [0, -600, 330]}}}}
5. All primitives support "rotation": [rx, ry, rz] in degrees for arbitrary orientation.
   Rotation is applied BEFORE position. Use for diagonal tubes, angled brackets, tilted parts.
   Example diagonal frame tube: {{"feature_type": "cylinder", "params": {{"radius": 15, "height": 500, "rotation": [0, 0, 30], "position": [0, 0, 400]}}}}
   This creates a cylinder tilted 30° around Z axis, then moved to position.
6. NEVER use "translate" to position primitives — translate moves the ENTIRE accumulated shape.
6. "shell" hollows out the shape — use this instead of creating an inner sphere/box to subtract.
7. "hole" creates a cylindrical hole on a face — use face_selector and click_point.
8. "fillet" and "chamfer" modify edges of the existing shape.

CORRECT PATTERNS:
- Hollow box: box → shell (wall thickness)
- Box with holes: box → hole (face=">Z", click_point=[x,y,z]) → hole → hole
- L-bracket: box (main) → box with "operation":"boss", "position":[x,y,z] (arm)
- Bracket with cutout: box → box with "operation":"cut", "position":[x,y,z]
- Cylinder with slot: cylinder → box with "operation":"cut", "position":[x,y,z]
- Chair: box (seat in XY plane at Z=seat_height) → 4x cylinder legs "position":[±x, ±y, seat_height/2] → box backrest "position":[0, -depth/2, seat+back/2]
- Helmet: sphere → scale (x=1.0, y=1.07, z=0.93 for oval) → shell (wall=4) → positioned cuts for neck/visor → boss for chin bar → fillet
- Elongated shapes: sphere → scale (non-uniform) to make ovals/eggs/capsules
- Skateboard deck: box (length=800 along Y, width=200 along X, height=10 along Z) → fillet top edges → fillet short ends with large radius for nose/tail curves
- Wheels on axles: cylinder with "axis":"Y" (axle along Y direction) for skateboard/car wheels

WRONG PATTERNS (NEVER DO THESE):
- ❌ Using "translate" to position sub-parts — it moves EVERYTHING built so far
- ❌ Creating separate bodies without "position" — they all end up at origin
- ❌ Creating an inner sphere/box to hollow — use "shell" instead
- ❌ Assuming Y is up — Z is the vertical axis in CadQuery (auto-converted for display)

For holes: use "face_selector" (">Z" for top, "<Z" for bottom, ">X" for right, ">Y" for front) and "click_point" [x, y, z] in WORLD coordinates.
  Keep hole centers at least hole_radius + 0.5mm from edges.

For fillet/chamfer: use "edge_refs" with CadQuery selectors like "|Z" (vertical edges), ">Z" (top edges).

DESIGN PHILOSOPHY:
- Keep it simple — use the FEWEST features that achieve the shape.
- Prefer box/cylinder + shell + holes + fillets. This covers 90% of mechanical parts.
- For organic/complex shapes (helmets, housings, etc.), use sphere/cylinder + shell + positioned cuts + fillets.
- "name" should be a short descriptive name (e.g. "Mounting Bracket", "Motor Housing").
- If the user provides dimensions, use them exactly. If not, use the ENGINEERING REFERENCE DATA below.
- All dimensions are in millimetres.

ENGINEERING REFERENCE DATA — USE THESE REAL DIMENSIONS:

Proportional rules for realistic parts:
- Fillet radius = 10-25% of wall thickness (min 0.5mm, cosmetic 0.5-1mm, structural 1-3mm)
- Chamfer = 45° x 0.5-2mm typical, thread entry = 45° x 1 pitch
- Wall thickness: plastic 1.5-3mm, aluminum 0.8-3mm, steel 0.8-2mm, 3D-print 2-3mm
- Mounting holes: clearance = bolt dia + 0.4mm (close) to +1mm (normal)
- Counterbore diameter = 1.7x screw diameter, depth = 1.0x screw diameter
- Standoff/boss diameter = 2-2.5x hole diameter
- Rib thickness = 50-75% of adjacent wall
- Mounting hole spacing = 60-75% of part length

Screw/bolt hole sizes (metric, close fit):
  M3: hole 3.4mm, counterbore 6.5mm deep 3mm | M4: hole 4.4mm, cbore 8.25mm deep 4mm
  M5: hole 5.4mm, cbore 9.75mm deep 5mm | M6: hole 6.4mm, cbore 11.25mm deep 6mm
  M8: hole 8.4mm, cbore 14.25mm deep 8mm | M10: hole 10.5mm, cbore 17.25mm deep 10mm

L-bracket standard sizes:
  Small: 25x25x19mm, t=2mm, holes 4mm | Medium: 50x50x35mm, t=2.5mm, holes 5mm
  Large: 100x100x50mm, t=3mm, holes 6mm | Heavy: 180x100x20mm, t=8mm, holes 5mm

Electronics enclosures:
  Raspberry Pi 4: 85x56mm board, M2.5 holes at 58x49mm spacing
  Arduino Uno: M3 mounting holes, typical case 100x60x30mm
  Wall 2mm, M3 standoffs 5mm dia x 5mm tall, PCB edge clearance 1-2mm

Bearings (6200 series):
  6200: bore 10, OD 30, width 9mm | 6202: bore 15, OD 35, width 11mm
  6205: bore 25, OD 52, width 15mm | 6210: bore 50, OD 90, width 20mm
  Housing wall = 20-30% of bearing OD

Spur gear formulas (module m, teeth z):
  Pitch dia = z*m | Tip dia = z*m + 2*m | Root dia = z*m - 2.5*m
  Tooth depth = 2.25*m | Face width = 8-12*m typical
  Standard modules: 0.5, 0.8, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5mm

Hex bolt heads (ISO 4014):
  M6: across flats 10mm, head height 4.2mm | M8: flats 13mm, height 5.5mm
  M10: flats 16mm, height 6.7mm | M12: flats 18mm, height 7.8mm

Motorcycle helmet (full-face, adult) — Z is UP:
  Base: sphere radius 140mm → scale(x=1.0, y=1.07, z=0.93) to make oval (280 wide x 300 front-back x 260 tall)
  Shell: shell wall 4mm (outer shell only, EPS liner separate)
  Neck opening: large box cut at position [0, 0, -150], ~240x260x120mm (below center, cuts bottom)
  Visor opening: box cut at position [0, 130, 30], ~180x80x80mm (front-upper area)
  Chin bar: box boss at position [0, 115, -55], ~140x50x60mm, then interior cut to hollow it
  Vent holes: cylinder cuts, 10mm radius, 2 on top at z=120, 2 on chin bar
  Fillet: all external edges 3-8mm
  Key: use scale after sphere to get oval shape, NOT a perfect sphere

Chair (dining, standard) — Z is UP, multi-part for different materials:
  Part "Seat" (wood_oak, #8B6914):
    - box: width=400, depth=400, height=30, centered=true, position=[0, 0, 450]
    - fillet: radius=3, edge_refs=[">Z"]
  Part "Chair Legs" (chrome, #D4D4D4):
    - cylinder: radius=15, height=450, centered=true, position=[-170, -170, 225]  (front-left, Z=0 to 450)
    - cylinder: radius=15, height=450, centered=true, position=[170, -170, 225], operation=boss  (front-right)
    - cylinder: radius=15, height=450, centered=true, position=[-170, 170, 225], operation=boss  (back-left)
    - cylinder: radius=15, height=450, centered=true, position=[170, 170, 225], operation=boss  (back-right)
    - box: width=340, depth=20, height=20, centered=true, position=[0, -170, 300], operation=boss  (front rail)
    - box: width=340, depth=20, height=20, centered=true, position=[0, 170, 300], operation=boss  (back rail)
    - box: width=20, depth=340, height=20, centered=true, position=[-170, 0, 300], operation=boss  (left rail)
    - box: width=20, depth=340, height=20, centered=true, position=[170, 0, 300], operation=boss  (right rail)
  Part "Backrest" (wood_oak, #8B6914):
    - box: width=400, depth=30, height=400, centered=true, position=[0, 185, 665]  (from seat top at Z=465 upward)
    - fillet: radius=5, edge_refs=[">Z"]
  Key: All parts use SAME world coordinates. Legs top (Z=450) meets seat bottom. Backrest starts at seat top."""

        # 4. User message
        user_message = f"""Create a new CAD part based on this description:
{instruction}

Return a JSON object with "name" and "features" array. For multi-material objects, use the "parts" array format."""

        # 5. Call Claude
        import time as _time
        client = _anthropic.Anthropic(api_key=api_key)
        _ai_model = "claude-sonnet-4-6"
        print(f"[AI-CREATE] Creating new part for assembly {assembly_id}: {instruction}")

        _t0 = _time.time()
        response = client.messages.create(
            model=_ai_model,
            max_tokens=16384,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        _duration_ms = int((_time.time() - _t0) * 1000)

        raw_response = response.content[0].text.strip()
        print(f"[AI-CREATE] Raw response ({_duration_ms}ms, stop={response.stop_reason}): {raw_response[:1000]}")

        # 6. Parse JSON response
        def _parse_ai_json(text: str):
            jt = text.strip()
            if jt.startswith("```"):
                lines = jt.split("\n")
                jt = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            if not jt.startswith("{"):
                start = jt.find("{")
                end = jt.rfind("}")
                if start >= 0 and end > start:
                    jt = jt[start:end + 1]
            return _json.loads(jt)

        try:
            result = _parse_ai_json(raw_response)
        except (_json.JSONDecodeError, ValueError) as e:
            # Retry once
            print(f"[AI-CREATE] JSON parse failed: {e}, retrying...")
            retry_response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16384,
                system="You are a JSON-only responder. Return ONLY a valid JSON object. No text, no markdown, no explanations. Just raw JSON.",
                messages=[
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": raw_response},
                    {"role": "user", "content": f"Your response was not valid JSON. Error: {e}\nReturn ONLY the JSON object, nothing else."},
                ],
            )
            raw_response = retry_response.content[0].text.strip()
            try:
                result = _parse_ai_json(raw_response)
            except (_json.JSONDecodeError, ValueError) as e2:
                raise HTTPException(status_code=400, detail=f"AI returned invalid JSON: {e2}")

        # 7. Normalize response: support both single-part and multi-part formats
        is_multi_part = "parts" in result and isinstance(result["parts"], list)
        if is_multi_part:
            # Multi-part response: e.g. chair with different material parts
            part_defs = result["parts"]
            group_name = result.get("group_name", part_name or "AI Component")
        else:
            # Single-part response
            part_defs = [{
                "name": result.get("name", part_name),
                "material": result.get("material", "steel"),
                "color": result.get("color", "#888888"),
                "features": result.get("features", []),
            }]
            group_name = None

        if not part_defs or not any(pd.get("features") for pd in part_defs):
            raise HTTPException(status_code=400, detail="AI returned no features")

        # 8. Create parent group part for multi-part creations
        parent_part_id = None
        if is_multi_part and len(part_defs) > 1:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO parts
                       (assembly_id, name, description, cadquery_script,
                        position_x, position_y, position_z,
                        material, color, feature_tree_mode, part_type)
                       VALUES (%s, %s, %s, %s, 0, 0, 0, %s, %s, true, 'component')
                       RETURNING id""",
                    (assembly_id, group_name, f"AI-created group: {instruction[:100]}",
                     "# Component group", "steel", "#888888"),
                )
                parent_part_id = cur.fetchone()[0]
            conn.commit()
            print(f"[AI-CREATE] Created parent group '{group_name}' (id={parent_part_id}) for {len(part_defs)} sub-parts")

        # 9. Create each part in the database
        all_created = []
        all_errors = []
        all_meshes = []
        colors = ['#4488CC', '#CC4444', '#44AA44', '#CC8844', '#8844CC', '#44CCCC', '#CC44CC']

        for pi, pdef in enumerate(part_defs):
            p_name = pdef.get("name", f"Part {pi + 1}")
            p_material = pdef.get("material", "steel")
            p_color = pdef.get("color", colors[pi % len(colors)])
            feature_defs = pdef.get("features", [])
            if not feature_defs:
                all_errors.append(f"Part '{p_name}': no features")
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO parts
                       (assembly_id, name, description, cadquery_script,
                        position_x, position_y, position_z,
                        material, color, feature_tree_mode, parent_part_id, sort_order)
                       VALUES (%s, %s, %s, %s, 0, 0, 0, %s, %s, true, %s, %s)
                       RETURNING id""",
                    (assembly_id, p_name, f"AI-created: {instruction[:100]}",
                     "# Feature tree part", p_material, p_color, parent_part_id, pi),
                )
                part_id = cur.fetchone()[0]

                branch_id = _ensure_main_branch(cur, part_id)

                applied = []
                for seq, fdef in enumerate(feature_defs, 1):
                    ftype = fdef.get("feature_type")
                    if ftype not in FEATURE_SCHEMAS:
                        all_errors.append(f"Part '{p_name}' feature {seq}: unknown type '{ftype}'")
                        continue
                    fname = fdef.get("name", f"{FEATURE_SCHEMAS[ftype]['label']}")
                    params = fdef.get("params", {})
                    try:
                        cur.execute(
                            "INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, source) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                            (part_id, branch_id, ftype, fname, seq,
                             _json.dumps(params), "ai"),
                        )
                        new_id = cur.fetchone()[0]
                        applied.append({"feature_id": new_id, "type": ftype, "name": fname})
                    except Exception as e:
                        all_errors.append(f"Part '{p_name}' feature {seq} ({ftype}): {e}")

            conn.commit()

            if not applied:
                all_errors.append(f"Part '{p_name}': no features could be created")
                continue

            print(f"[AI-CREATE] Created part {part_id} ('{p_name}') with {len(applied)} features, material={p_material}")

            # Rebuild mesh from features
            try:
                rebuild_result = _do_rebuild(part_id, conn, branch_id=branch_id)
            except Exception as rebuild_exc:
                print(f"[AI-CREATE] Rebuild exception for part {part_id}: {rebuild_exc}")
                rebuild_result = {"rebuild_status": "failed", "feature_errors": {"exception": str(rebuild_exc)}}

            mesh = rebuild_result.get("mesh")
            if mesh:
                mesh["id"] = part_id
                all_meshes.append(mesh)

            # Create initial version
            with conn.cursor() as cur:
                try:
                    _create_version(cur, part_id, branch_id, "# Feature tree part",
                                    label=f"AI created: {instruction[:50]}",
                                    author_type="ai", author_info="create-with-ai-features", auto=True)
                except Exception:
                    pass
            conn.commit()

            all_created.append({
                "part_id": part_id,
                "name": p_name,
                "material": p_material,
                "color": p_color,
                "features": applied,
                "mesh": mesh,
            })

        if not all_created:
            raise HTTPException(status_code=400, detail=f"No parts could be created. Errors: {'; '.join(all_errors)}")

        # 9. Log AI interaction (log against first part)
        first_part_id = all_created[0]["part_id"] if all_created else None
        _log_ai_interaction(
            conn,
            interaction_type="feature_create",
            instruction=instruction,
            part_id=first_part_id,
            assembly_id=assembly_id,
            branch_id=None,
            user_id=user.id if user else None,
            system_prompt=system_prompt,
            user_message=user_message,
            raw_response=raw_response,
            parsed_result=result,
            applied_ops=[c["features"] for c in all_created],
            errors=all_errors if all_errors else None,
            model=_ai_model,
            duration_ms=_duration_ms,
            success=bool(all_created),
        )

        # Return first part's mesh for backward compatibility, plus all parts
        return {
            "success": True,
            "part_id": all_created[0]["part_id"] if all_created else None,
            "name": all_created[0]["name"] if all_created else part_name,
            "mesh": all_created[0].get("mesh") if all_created else None,
            "features": all_created[0].get("features", []) if all_created else [],
            "errors": all_errors if all_errors else None,
            "parts_created": all_created,
            "parent_part_id": parent_part_id,
            "group_name": group_name,
        }

    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        put_connection(conn)


@app.get("/ai-interactions")
async def list_ai_interactions(part_id: int = None, limit: int = 50, offset: int = 0):
    """List AI interactions for review — what was asked and what Claude returned."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if part_id:
                cur.execute(
                    """SELECT id, created_at, interaction_type, part_id, assembly_id, branch_id,
                              instruction, user_message, raw_response, parsed_result,
                              applied_ops, errors, model, duration_ms, success
                       FROM ai_interactions
                       WHERE part_id = %s
                       ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                    (part_id, limit, offset),
                )
            else:
                cur.execute(
                    """SELECT id, created_at, interaction_type, part_id, assembly_id, branch_id,
                              instruction, user_message, raw_response, parsed_result,
                              applied_ops, errors, model, duration_ms, success
                       FROM ai_interactions
                       ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                    (limit, offset),
                )
            rows = cur.fetchall()
            cols = [
                "id", "created_at", "interaction_type", "part_id", "assembly_id", "branch_id",
                "instruction", "user_message", "raw_response", "parsed_result",
                "applied_ops", "errors", "model", "duration_ms", "success",
            ]
            return [dict(zip(cols, r)) for r in rows]
    finally:
        put_connection(conn)


@app.get("/ai-interactions/{interaction_id}")
async def get_ai_interaction(interaction_id: int):
    """Get full details of a single AI interaction including system prompt."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, created_at, interaction_type, part_id, assembly_id, branch_id,
                          instruction, system_prompt, user_message, raw_response,
                          parsed_result, applied_ops, errors, model, duration_ms, success
                   FROM ai_interactions WHERE id = %s""",
                (interaction_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Interaction not found")
            cols = [
                "id", "created_at", "interaction_type", "part_id", "assembly_id", "branch_id",
                "instruction", "system_prompt", "user_message", "raw_response",
                "parsed_result", "applied_ops", "errors", "model", "duration_ms", "success",
            ]
            return dict(zip(cols, row))
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/restore-snapshot")
async def restore_script_snapshot(part_id: int, body: dict):
    """Restore a part's CadQuery script to a previously saved snapshot."""
    snapshot_id = body.get("snapshot_id")
    if not snapshot_id:
        raise HTTPException(status_code=400, detail="Provide snapshot_id")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT script FROM part_script_snapshots WHERE id = %s AND part_id = %s",
                (snapshot_id, part_id)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Snapshot not found")
            script = row[0]

            cur.execute(
                "UPDATE parts SET cadquery_script = %s, updated_at = now() WHERE id = %s",
                (script, part_id)
            )
        conn.commit()

        wp = execute_script(script)
        mesh = shape_to_topo_mesh(wp)
        mesh["id"] = part_id
        return {"part_id": part_id, "mesh": mesh}

    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Version Control System — checkpoints, branches, merging
# ---------------------------------------------------------------------------


def _ensure_main_branch(cur, part_id: int) -> int:
    """Lazily create a 'main' branch + initial version for a part. Returns branch_id."""
    cur.execute("SELECT active_branch_id FROM parts WHERE id = %s", (part_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Part not found")
    branch_id = row[0]
    if branch_id:
        return branch_id

    # Create main branch
    cur.execute(
        "INSERT INTO part_branches (part_id, name) VALUES (%s, 'main') "
        "ON CONFLICT (part_id, name) DO UPDATE SET name='main' RETURNING id",
        (part_id,),
    )
    branch_id = cur.fetchone()[0]

    # Seed initial version with current script
    cur.execute("SELECT cadquery_script FROM parts WHERE id = %s", (part_id,))
    script = cur.fetchone()[0] or ""
    cur.execute(
        "INSERT INTO part_versions (part_id, parent_id, branch_id, script, label, author_type, auto) "
        "VALUES (%s, NULL, %s, %s, 'Initial', 'system', true) RETURNING id",
        (part_id, branch_id, script),
    )
    version_id = cur.fetchone()[0]
    cur.execute("UPDATE part_branches SET head_id = %s WHERE id = %s", (version_id, branch_id))
    cur.execute("UPDATE parts SET active_branch_id = %s WHERE id = %s", (branch_id, part_id))

    # Backfill: assign orphan features (no branch_id) to this main branch
    cur.execute(
        "UPDATE features SET branch_id = %s WHERE part_id = %s AND branch_id IS NULL",
        (branch_id, part_id),
    )
    return branch_id


def _create_version(cur, part_id: int, branch_id: int, script: str,
                    label: str, author_type: str = "human",
                    author_info: str = "", auto: bool = False) -> int:
    """Insert a new version on the given branch and advance HEAD. Returns version_id."""
    cur.execute("SELECT head_id FROM part_branches WHERE id = %s", (branch_id,))
    row = cur.fetchone()
    parent_id = row[0] if row else None

    cur.execute(
        "INSERT INTO part_versions (part_id, parent_id, branch_id, script, label, author_type, author_info, auto) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (part_id, parent_id, branch_id, script, label, author_type, author_info, auto),
    )
    version_id = cur.fetchone()[0]
    cur.execute("UPDATE part_branches SET head_id = %s WHERE id = %s", (version_id, branch_id))
    return version_id


# --- Per-user branch helpers ---

_AI_USER_ID: Optional[int] = None


def _get_ai_user_id(cur) -> int:
    """Return the sentinel AI user ID, creating it lazily."""
    global _AI_USER_ID
    if _AI_USER_ID is not None:
        return _AI_USER_ID
    cur.execute("SELECT id FROM users WHERE email = 'ai@system.internal'")
    row = cur.fetchone()
    if row:
        _AI_USER_ID = row[0]
    else:
        cur.execute(
            "INSERT INTO users (email, name) VALUES ('ai@system.internal', 'AI Assistant') "
            "ON CONFLICT (email) DO NOTHING RETURNING id"
        )
        row = cur.fetchone()
        if row:
            _AI_USER_ID = row[0]
        else:
            cur.execute("SELECT id FROM users WHERE email = 'ai@system.internal'")
            _AI_USER_ID = cur.fetchone()[0]
    return _AI_USER_ID


def _get_user_branch(cur, user_id: int, part_id: int) -> int:
    """Get the user's active branch for a part. Falls back to main branch."""
    if _user_exists(cur, user_id):
        cur.execute(
            "SELECT branch_id FROM user_branch_state WHERE user_id = %s AND part_id = %s",
            (user_id, part_id),
        )
        row = cur.fetchone()
        if row:
            return row[0]
    return _ensure_main_branch(cur, part_id)


def _user_exists(cur, user_id: int) -> bool:
    """Check if user_id exists in users table."""
    cur.execute("SELECT 1 FROM users WHERE id = %s", (user_id,))
    return cur.fetchone() is not None


def _set_user_branch(cur, user_id: int, part_id: int, branch_id: int):
    """Set the user's active branch for a part (upsert). Skips if user doesn't exist."""
    if not _user_exists(cur, user_id):
        return
    cur.execute(
        "INSERT INTO user_branch_state (user_id, part_id, branch_id) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, part_id) DO UPDATE SET branch_id = %s, checked_out_at = now()",
        (user_id, part_id, branch_id, branch_id),
    )


def _get_script_for_user(cur, user_id: int, part_id: int) -> str:
    """Resolve the script that a user should see, based on their active branch."""
    branch_id = _get_user_branch(cur, user_id, part_id)
    cur.execute(
        "SELECT v.script FROM part_versions v "
        "JOIN part_branches b ON b.head_id = v.id "
        "WHERE b.id = %s",
        (branch_id,),
    )
    row = cur.fetchone()
    if row and row[0]:
        return row[0]
    # Fallback to parts.cadquery_script
    cur.execute("SELECT cadquery_script FROM parts WHERE id = %s", (part_id,))
    row = cur.fetchone()
    return row[0] if row else ""


def _ensure_user_working_branch(cur, user_id: int, part_id: int) -> int:
    """Ensure the user has a working branch for a part.
    - Owner works on main directly.
    - Everyone else (AI, team members) gets their own auto-created branch.
    Returns the branch_id to work on."""
    if not _user_exists(cur, user_id):
        return _ensure_main_branch(cur, part_id)

    # Check if user is the owner first — owners always work on main
    if _is_part_owner(cur, user_id, part_id):
        return _ensure_main_branch(cur, part_id)

    # Check if user already has an active branch via user_branch_state
    cur.execute(
        "SELECT ubs.branch_id, b.is_merged, b.name "
        "FROM user_branch_state ubs "
        "JOIN part_branches b ON b.id = ubs.branch_id "
        "WHERE ubs.user_id = %s AND ubs.part_id = %s",
        (user_id, part_id),
    )
    row = cur.fetchone()
    if row:
        branch_id, is_merged, bname = row
        if not is_merged and bname != 'main':
            # User has an active non-main branch — use it
            return branch_id
        # On main or merged branch — fall through to create new branch

    # Check if user already has a non-merged branch for this part
    cur.execute(
        "SELECT id FROM part_branches "
        "WHERE part_id = %s AND created_by = %s AND is_merged = false AND name != 'main'",
        (part_id, user_id),
    )
    existing = cur.fetchone()
    if existing:
        branch_id = existing[0]
        _set_user_branch(cur, user_id, part_id, branch_id)
        return branch_id

    # Auto-create a personal branch — get user name for branch naming
    cur.execute("SELECT name, email FROM users WHERE id = %s", (user_id,))
    urow = cur.fetchone()
    if urow:
        prefix = (urow[0] or urow[1] or "user").split()[0].split("@")[0].lower()
    else:
        prefix = f"user-{user_id}"

    # Ensure unique branch name
    branch_name = prefix
    suffix = 0
    while True:
        cur.execute(
            "SELECT 1 FROM part_branches WHERE part_id = %s AND name = %s",
            (part_id, branch_name),
        )
        if not cur.fetchone():
            break
        suffix += 1
        branch_name = f"{prefix}-{suffix}"

    # Fork from main HEAD
    main_branch = _ensure_main_branch(cur, part_id)
    cur.execute(
        "SELECT v.id, v.script FROM part_versions v "
        "JOIN part_branches b ON b.head_id = v.id "
        "WHERE b.id = %s",
        (main_branch,),
    )
    src = cur.fetchone()
    source_parent = src[0] if src else None
    source_script = src[1] if src else ""

    cur.execute(
        "INSERT INTO part_branches (part_id, name, created_by) VALUES (%s, %s, %s) RETURNING id",
        (part_id, branch_name, user_id),
    )
    branch_id = cur.fetchone()[0]

    cur.execute(
        "INSERT INTO part_versions (part_id, parent_id, branch_id, script, label, author_type, auto) "
        "VALUES (%s, %s, %s, %s, %s, 'system', true) RETURNING id",
        (part_id, source_parent, branch_id, source_script, f"Branch from {branch_name}"),
    )
    version_id = cur.fetchone()[0]
    cur.execute("UPDATE part_branches SET head_id=%s WHERE id=%s", (version_id, branch_id))

    # Copy features from main branch to the new branch
    cur.execute("""
        INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, suppressed, source)
        SELECT part_id, %s, feature_type, name, sequence, params, suppressed, source
        FROM features WHERE part_id = %s AND branch_id = %s
    """, (branch_id, part_id, main_branch))

    # Auto-checkout to the new branch
    _set_user_branch(cur, user_id, part_id, branch_id)
    return branch_id


def _features_to_cadquery_script(conn, part_id: int, branch_id: int = None) -> str:
    """Generate a CadQuery script from a part's feature tree.
    This gives the AI the actual geometry code to modify."""
    with conn.cursor() as cur:
        if branch_id:
            cur.execute(
                "SELECT feature_type, name, params, sequence FROM features "
                "WHERE part_id = %s AND branch_id = %s AND NOT suppressed ORDER BY sequence",
                (part_id, branch_id),
            )
        else:
            cur.execute(
                "SELECT feature_type, name, params, sequence FROM features "
                "WHERE part_id = %s AND NOT suppressed ORDER BY sequence",
                (part_id,),
            )
        features = cur.fetchall()
    if not features:
        return ""

    lines = ["import cadquery as cq", ""]

    for ftype, fname, params_json, seq in features:
        p = _json.loads(params_json) if isinstance(params_json, str) else (params_json or {})

        if ftype == "box":
            w, d, h = p.get("width", 50), p.get("depth", 30), p.get("height", 20)
            centered = p.get("centered", True)
            if seq == 1 or not any(f[0] in ("box", "cylinder", "sphere") for f in features[:seq-1]):
                lines.append(f"# Feature: {fname or 'Box'}")
                lines.append(f"result = cq.Workplane('XY').box({w}, {d}, {h}, centered=({centered}, {centered}, {centered}))")
            else:
                lines.append(f"# Feature: {fname or 'Box'} (union)")
                lines.append(f"result = result.union(cq.Workplane('XY').box({w}, {d}, {h}, centered=({centered}, {centered}, {centered})))")

        elif ftype == "cylinder":
            r, h = p.get("radius", 15), p.get("height", 40)
            axis = p.get("axis", "Z").upper()
            wp_map = {"X": "YZ", "Y": "XZ", "Z": "XY"}
            wp_name = wp_map.get(axis, "XY")
            if seq == 1:
                lines.append(f"result = cq.Workplane('{wp_name}').cylinder({h}, {r})")
            else:
                lines.append(f"result = result.union(cq.Workplane('{wp_name}').cylinder({h}, {r}))")

        elif ftype == "sphere":
            r = p.get("radius", 20)
            if seq == 1:
                lines.append(f"result = cq.Workplane('XY').sphere({r})")
            else:
                lines.append(f"result = result.union(cq.Workplane('XY').sphere({r}))")

        elif ftype == "hole":
            diameter = p.get("diameter", 10)
            depth = p.get("depth")
            face_sel = p.get("face_selector", ">Z")
            click_point = p.get("click_point")
            lines.append(f"# Feature: {fname or 'Hole'}")
            if click_point and isinstance(click_point, (list, tuple)) and len(click_point) >= 3:
                cx, cy, cz = click_point[0], click_point[1], click_point[2]
                lines.append(f"_face_wp = result.faces('{face_sel}').workplane()")
                lines.append(f"_local = _face_wp.plane.toLocalCoords(cq.Vector({cx}, {cy}, {cz}))")
                lines.append(f"_face_wp = _face_wp.center(_local.x, _local.y)")
                if depth:
                    lines.append(f"result = _face_wp.hole({diameter}, {depth})")
                else:
                    lines.append(f"result = _face_wp.hole({diameter})")
            else:
                if depth:
                    lines.append(f"result = result.faces('{face_sel}').workplane().hole({diameter}, {depth})")
                else:
                    lines.append(f"result = result.faces('{face_sel}').workplane().hole({diameter})")

        elif ftype == "fillet":
            radius = p.get("radius", 3)
            lines.append(f"# Feature: {fname or 'Fillet'}")
            edge_refs = p.get("edge_refs", [])
            if edge_refs:
                lines.append(f"result = result.edges('{edge_refs[0]}').fillet({radius})")
            else:
                lines.append(f"result = result.edges().fillet({radius})")

        elif ftype == "chamfer":
            dist = p.get("distance", 2)
            lines.append(f"# Feature: {fname or 'Chamfer'}")
            edge_refs = p.get("edge_refs", [])
            if edge_refs:
                lines.append(f"result = result.edges('{edge_refs[0]}').chamfer({dist})")
            else:
                lines.append(f"result = result.edges().chamfer({dist})")

        elif ftype == "shell":
            thickness = p.get("thickness", 2)
            faces = p.get("faces_to_remove", [])
            sel = faces[0] if faces and isinstance(faces[0], str) else ">Z"
            lines.append(f"result = result.faces('{sel}').shell(-{thickness})")

        else:
            lines.append(f"# Feature: {fname or ftype} (type={ftype}, params={p})")

        lines.append("")

    return "\n".join(lines)


def _is_part_owner(cur, user_id: int, part_id: int) -> bool:
    """Check if user is the project owner for a part."""
    cur.execute(
        "SELECT p.user_id FROM projects p "
        "JOIN assemblies a ON a.project_id = p.id "
        "JOIN parts pt ON pt.assembly_id = a.id "
        "WHERE pt.id = %s",
        (part_id,),
    )
    row = cur.fetchone()
    return row is not None and row[0] == user_id


# --- Branches ---

@app.get("/parts/{part_id}/branches")
async def list_branches(part_id: int, user: UserInfo = Depends(get_current_user_optional)):
    user_id = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_main_branch(cur, part_id)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT b.id, b.name, b.head_id, b.created_at, b.is_merged, b.created_by, "
                "(SELECT COUNT(*) FROM part_versions WHERE branch_id = b.id) as version_count "
                "FROM part_branches b WHERE b.part_id = %s AND b.is_merged = false "
                "ORDER BY b.created_at",
                (part_id,),
            )
            rows = cur.fetchall()
            active = _get_user_branch(cur, user_id, part_id)
        return {
            "branches": [
                {"id": r[0], "name": r[1], "head_id": r[2],
                 "created_at": r[3].isoformat() if r[3] else None,
                 "is_merged": r[4], "created_by": r[5],
                 "version_count": r[6]}
                for r in rows
            ],
            "active_branch_id": active,
        }
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/branches")
async def create_branch(part_id: int, body: dict, user: UserInfo = Depends(get_current_user_optional)):
    user_id = get_user_id_for_request(user)
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Branch name required")
    from_version_id = body.get("from_version_id")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            main_branch = _ensure_main_branch(cur, part_id)

            # Determine source version — always resolve parent ID for fork edge
            if from_version_id:
                cur.execute("SELECT script FROM part_versions WHERE id=%s AND part_id=%s",
                            (from_version_id, part_id))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Source version not found")
                source_script = row[0]
                source_parent = from_version_id
            else:
                cur.execute(
                    "SELECT v.id, v.script FROM part_versions v "
                    "JOIN part_branches b ON b.head_id = v.id "
                    "WHERE b.id = %s", (main_branch,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Source version not found")
                source_parent = row[0]
                source_script = row[1]

            # Create branch with created_by tracking
            created_by = user_id if _user_exists(cur, user_id) else None
            cur.execute(
                "INSERT INTO part_branches (part_id, name, created_by) VALUES (%s, %s, %s) RETURNING id",
                (part_id, name, created_by),
            )
            branch_id = cur.fetchone()[0]

            # Create initial version on new branch
            cur.execute(
                "INSERT INTO part_versions (part_id, parent_id, branch_id, script, label, author_type, auto) "
                "VALUES (%s, %s, %s, %s, %s, 'system', true) RETURNING id",
                (part_id, source_parent, branch_id, source_script, f"Branch from {name}"),
            )
            version_id = cur.fetchone()[0]
            cur.execute("UPDATE part_branches SET head_id=%s WHERE id=%s", (version_id, branch_id))

            # Auto-checkout: creator is immediately on the new branch
            _set_user_branch(cur, user_id, part_id, branch_id)
        conn.commit()
        return {"branch_id": branch_id, "version_id": version_id, "name": name}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"Branch '{name}' already exists")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.delete("/parts/{part_id}/branches/{branch_id}")
async def delete_branch(part_id: int, branch_id: int, user: UserInfo = Depends(get_current_user_optional)):
    user_id = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM part_branches WHERE id=%s AND part_id=%s", (branch_id, part_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Branch not found")
            if row[0] == "main":
                raise HTTPException(status_code=400, detail="Cannot delete main branch")
            # Move all users on this branch back to main
            main_branch = _ensure_main_branch(cur, part_id)
            cur.execute(
                "UPDATE user_branch_state SET branch_id = %s WHERE part_id = %s AND branch_id = %s",
                (main_branch, part_id, branch_id),
            )
            cur.execute("DELETE FROM part_versions WHERE branch_id=%s", (branch_id,))
            cur.execute("DELETE FROM part_branches WHERE id=%s", (branch_id,))
        conn.commit()
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/branches/{branch_id}/checkout")
async def checkout_branch(part_id: int, branch_id: int, user: UserInfo = Depends(get_current_user_optional)):
    user_id = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT v.script FROM part_versions v "
                "JOIN part_branches b ON b.head_id = v.id "
                "WHERE b.id=%s AND b.part_id=%s",
                (branch_id, part_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Branch not found or empty")
            script = row[0]
            # Per-user checkout — only update user_branch_state, not the global part
            _set_user_branch(cur, user_id, part_id, branch_id)
            cur.execute("SELECT name FROM part_branches WHERE id=%s", (branch_id,))
            brow = cur.fetchone()
            branch_name = brow[0] if brow else "?"
        conn.commit()
        # Generate mesh from the branch features (AI now edits features, not scripts)
        mesh = None
        try:
            from feature_routes import _load_features, _do_rebuild
            features = _load_features(conn, part_id, branch_id)
            if features:
                result = _do_rebuild(part_id, conn, branch_id=branch_id)
                mesh = result.get("mesh")
                if mesh:
                    mesh["id"] = part_id
            if not mesh and script and script.strip() and not script.startswith("# Feature tree"):
                # Fallback: legacy script-based branch
                wp = execute_script(script)
                mesh = shape_to_topo_mesh(wp)
                mesh["id"] = part_id
        except Exception:
            pass
        return {"part_id": part_id, "branch_id": branch_id, "branch_name": branch_name, "script": script, "mesh": mesh}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/branches/{branch_id}/merge")
async def merge_branch(part_id: int, branch_id: int, user: UserInfo = Depends(get_current_user_optional)):
    """Merge a branch into main (theirs-wins: takes the branch HEAD script).
    Branch is marked as merged (not deleted) — history preserved in version graph."""
    user_id = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Get source branch info
            cur.execute(
                "SELECT b.name, v.script FROM part_branches b "
                "JOIN part_versions v ON v.id = b.head_id "
                "WHERE b.id=%s AND b.part_id=%s",
                (branch_id, part_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Branch not found or empty")
            source_name, source_script = row

            if source_name == "main":
                raise HTTPException(status_code=400, detail="Cannot merge main into itself")

            # Find main branch
            main_branch = _ensure_main_branch(cur, part_id)

            # Create merge version on main
            version_id = _create_version(
                cur, part_id, main_branch, source_script,
                label=f"Merged from '{source_name}'",
                author_type="system", auto=True,
            )

            # Update part script to merged version (main branch canonical script)
            cur.execute("SELECT feature_tree_mode FROM parts WHERE id=%s", (part_id,))
            ftm = cur.fetchone()[0]
            if not ftm:
                cur.execute(
                    "UPDATE parts SET cadquery_script=%s, "
                    "script_hash=NULL, mesh_cache=NULL, updated_at=now() WHERE id=%s",
                    (source_script, part_id),
                )

            # Merge feature tree: replace main's features with branch's features
            cur.execute("DELETE FROM features WHERE part_id = %s AND branch_id = %s", (part_id, main_branch))
            cur.execute("""
                INSERT INTO features (part_id, branch_id, feature_type, name, sequence, params, suppressed, source)
                SELECT part_id, %s, feature_type, name, sequence, params, suppressed, source
                FROM features WHERE part_id = %s AND branch_id = %s
            """, (main_branch, part_id, branch_id))

            # Mark branch as merged (preserve history, don't delete)
            cur.execute(
                "UPDATE part_branches SET is_merged = true, merged_at = now() WHERE id = %s",
                (branch_id,),
            )

            # Move all users on the merged branch back to main
            cur.execute(
                "UPDATE user_branch_state SET branch_id = %s WHERE part_id = %s AND branch_id = %s",
                (main_branch, part_id, branch_id),
            )
        conn.commit()
        _mesh_lru.invalidate(part_id)

        # Rebuild mesh from merged features on main branch
        mesh = None
        try:
            from feature_routes import _do_rebuild
            rebuild_result = _do_rebuild(part_id, conn, branch_id=main_branch)
            mesh = rebuild_result.get("mesh")
            if mesh:
                mesh["id"] = part_id
        except Exception as e:
            print(f"[MERGE] Rebuild after merge failed: {e}")

        return {"part_id": part_id, "version_id": version_id, "merged_from": source_name, "mesh": mesh}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


# --- Versions / Checkpoints ---

@app.get("/parts/{part_id}/versions")
async def list_versions(part_id: int, branch_id: int = 0, limit: int = 50):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            active_branch = _ensure_main_branch(cur, part_id)
        conn.commit()
        target = branch_id if branch_id else active_branch
        with conn.cursor() as cur:
            cur.execute(
                "SELECT v.id, v.parent_id, v.branch_id, v.label, v.author_type, "
                "v.author_info, v.auto, v.created_at, b.name "
                "FROM part_versions v JOIN part_branches b ON b.id = v.branch_id "
                "WHERE v.part_id=%s AND v.branch_id=%s "
                "ORDER BY v.created_at DESC LIMIT %s",
                (part_id, target, limit),
            )
            rows = cur.fetchall()
        return {
            "versions": [
                {"id": r[0], "parent_id": r[1], "branch_id": r[2], "label": r[3],
                 "author_type": r[4], "author_info": r[5], "auto": r[6],
                 "created_at": r[7].isoformat() if r[7] else None,
                 "branch_name": r[8]}
                for r in rows
            ],
            "branch_id": target,
        }
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/versions")
async def create_checkpoint(part_id: int, body: dict, user: UserInfo = Depends(get_current_user_optional)):
    """Create a named checkpoint (manual save point) on the user's working branch."""
    user_id = get_user_id_for_request(user)
    label = body.get("label", "Checkpoint").strip()
    author_type = body.get("author_type", "human")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            branch_id = _ensure_user_working_branch(cur, user_id, part_id)
            script = _get_script_for_user(cur, user_id, part_id)
            version_id = _create_version(
                cur, part_id, branch_id, script,
                label=label, author_type=author_type,
            )
        conn.commit()
        return {"version_id": version_id, "branch_id": branch_id}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/versions/{version_id}/restore")
async def restore_version(part_id: int, version_id: int, user: UserInfo = Depends(get_current_user_optional)):
    """Roll back to a previous version on the user's current branch."""
    user_id = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT script, label FROM part_versions WHERE id=%s AND part_id=%s",
                (version_id, part_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Version not found")
            script, old_label = row

            branch_id = _ensure_user_working_branch(cur, user_id, part_id)
            new_vid = _create_version(
                cur, part_id, branch_id, script,
                label=f"Restored '{old_label}' (v{version_id})",
                author_type="system", auto=True,
            )
        conn.commit()
        _mesh_lru.invalidate(part_id)
        return {"part_id": part_id, "version_id": new_vid, "script": script}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


@app.get("/parts/{part_id}/version-tree")
async def get_version_tree(part_id: int, user: UserInfo = Depends(get_current_user_optional)):
    """Return branch + version tree for visualization.
    Owner sees everything. Other users see main + their own branches only."""
    user_id = get_user_id_for_request(user)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_main_branch(cur, part_id)
        conn.commit()
        with conn.cursor() as cur:
            is_owner = _is_part_owner(cur, user_id, part_id)

            if is_owner:
                # Owner sees ALL branches (including merged ones for history)
                cur.execute(
                    "SELECT id, name, head_id, created_at, is_merged, created_by "
                    "FROM part_branches WHERE part_id=%s ORDER BY created_at",
                    (part_id,),
                )
            else:
                # Others see main + their own branches (including merged for history)
                cur.execute(
                    "SELECT id, name, head_id, created_at, is_merged, created_by "
                    "FROM part_branches WHERE part_id=%s "
                    "AND (name = 'main' OR created_by = %s) "
                    "ORDER BY created_at",
                    (part_id, user_id),
                )
            branches = [{"id": r[0], "name": r[1], "head_id": r[2],
                         "created_at": r[3].isoformat() if r[3] else None,
                         "is_merged": r[4], "created_by": r[5]}
                        for r in cur.fetchall()]

            visible_branch_ids = [b["id"] for b in branches]

            if visible_branch_ids:
                placeholders = ",".join(["%s"] * len(visible_branch_ids))
                cur.execute(
                    f"SELECT v.id, v.parent_id, v.branch_id, v.label, v.author_type, "
                    f"v.author_info, v.auto, v.created_at, b.name "
                    f"FROM part_versions v JOIN part_branches b ON b.id = v.branch_id "
                    f"WHERE v.part_id=%s AND v.branch_id IN ({placeholders}) ORDER BY v.created_at",
                    [part_id] + visible_branch_ids,
                )
                versions = [
                    {"id": r[0], "parent_id": r[1], "branch_id": r[2], "label": r[3],
                     "author_type": r[4], "author_info": r[5], "auto": r[6],
                     "created_at": r[7].isoformat() if r[7] else None,
                     "branch_name": r[8]}
                    for r in cur.fetchall()
                ]
            else:
                versions = []

            active = _get_user_branch(cur, user_id, part_id)

        return {"branches": branches, "versions": versions, "active_branch_id": active, "is_owner": is_owner}
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Assembly tree API (Phase 2B)
# ---------------------------------------------------------------------------


def _build_tree(parts, operations_by_part):
    """Build a hierarchical tree from flat parts list."""
    by_id = {p["id"]: {**p, "children": [], "features": operations_by_part.get(p["id"], [])} for p in parts}
    roots = []
    for p in by_id.values():
        parent_id = p.get("parent_part_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(p)
        else:
            roots.append(p)
    # Sort children by sort_order
    for p in by_id.values():
        p["children"].sort(key=lambda c: c.get("sort_order", 0))
    roots.sort(key=lambda c: c.get("sort_order", 0))
    return roots


@app.get("/assemblies/{assembly_id}/tree")
async def get_assembly_tree(assembly_id: int):
    """Return hierarchical tree of parts with their operations."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS[:-2])} FROM parts WHERE assembly_id = %s AND (archived = false OR archived IS NULL) ORDER BY sort_order, id",
                (assembly_id,),
            )
            rows = cur.fetchall()
            parts = [dict(zip(PART_COLUMNS[:-2], r)) for r in rows]

            # Load operations for all parts
            part_ids = [p["id"] for p in parts]
            ops_by_part = {}
            if part_ids:
                cur.execute(
                    "SELECT id, part_id, sequence, operation, parameters FROM operations WHERE part_id = ANY(%s) ORDER BY sequence",
                    (part_ids,),
                )
                for row in cur.fetchall():
                    pid = row[1]
                    if pid not in ops_by_part:
                        ops_by_part[pid] = []
                    ops_by_part[pid].append({
                        "id": row[0], "part_id": row[1], "sequence": row[2],
                        "operation": row[3], "parameters": row[4],
                    })

            tree = _build_tree(parts, ops_by_part)
            return {"assembly_id": assembly_id, "tree": tree}
    finally:
        put_connection(conn)


class MovePartRequest(BaseModel):
    parent_part_id: Optional[int] = None


@app.put("/parts/{part_id}/move")
async def move_part(part_id: int, body: MovePartRequest):
    """Reparent a part (drag-and-drop in tree)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE parts SET parent_part_id = %s, updated_at = now() WHERE id = %s RETURNING id",
                (body.parent_part_id, part_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Part not found")
        conn.commit()
        return {"moved": True}
    except HTTPException:
        raise
    finally:
        put_connection(conn)


class ReorderPartRequest(BaseModel):
    sort_order: int


@app.put("/parts/{part_id}/reorder")
async def reorder_part(part_id: int, body: ReorderPartRequest):
    """Change sort order of a part in the tree."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE parts SET sort_order = %s, updated_at = now() WHERE id = %s RETURNING id",
                (body.sort_order, part_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Part not found")
        conn.commit()
        return {"reordered": True}
    except HTTPException:
        raise
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/duplicate")
async def duplicate_part(part_id: int):
    """Deep duplicate a part with all its operations."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS[:-2])} FROM parts WHERE id = %s",
                (part_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
            src = dict(zip(PART_COLUMNS[:-2], row))

            sketch_json_str = _json.dumps(src["sketch_json"]) if src.get("sketch_json") else None
            sketch_plane_str = _json.dumps(src["sketch_plane"]) if src.get("sketch_plane") else None
            cur.execute(
                """INSERT INTO parts (assembly_id, name, description, cadquery_script,
                   position_x, position_y, position_z,
                   rotation_x, rotation_y, rotation_z,
                   scale_x, scale_y, scale_z,
                   material, color,
                   parent_part_id, part_type, sort_order,
                   sketch_json, sketch_plane,
                   bbox_min_x, bbox_min_y, bbox_min_z, bbox_max_x, bbox_max_y, bbox_max_z)
                   VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (
                    src["assembly_id"], f"{src['name']} (copy)", src["description"], src["cadquery_script"],
                    src["position_x"] + 20, src["position_y"], src["position_z"],
                    src["rotation_x"], src["rotation_y"], src["rotation_z"],
                    src.get("scale_x", 1), src.get("scale_y", 1), src.get("scale_z", 1),
                    src["material"], src["color"],
                    src.get("parent_part_id"), src.get("part_type", "body"), src.get("sort_order", 0),
                    sketch_json_str, sketch_plane_str,
                    src.get("bbox_min_x"), src.get("bbox_min_y"), src.get("bbox_min_z"),
                    src.get("bbox_max_x"), src.get("bbox_max_y"), src.get("bbox_max_z"),
                ),
            )
            new_id = cur.fetchone()[0]

            # Copy operations
            cur.execute(
                "SELECT sequence, operation, parameters, parent_op_id FROM operations WHERE part_id = %s ORDER BY sequence",
                (part_id,),
            )
            for op_row in cur.fetchall():
                cur.execute(
                    "INSERT INTO operations (part_id, sequence, operation, parameters, parent_op_id) VALUES (%s,%s,%s,%s,%s)",
                    (new_id, op_row[0], op_row[1], _json.dumps(op_row[2]) if isinstance(op_row[2], dict) else op_row[2], op_row[3]),
                )

        conn.commit()
        return {"duplicated": True, "new_part_id": new_id}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


# ---------------------------------------------------------------------------
# Chat (project-scoped)
# ---------------------------------------------------------------------------


@app.get("/projects/{project_id}/chat", response_model=List[ChatMessageResponse])
async def get_chat_history(project_id: int, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, project_id, role, content, script, part_id, created_at
                   FROM chat_messages
                   WHERE project_id = %s AND user_id = %s
                   ORDER BY created_at""",
                (project_id, user.id),
            )
            return [
                ChatMessageResponse(
                    id=r[0], project_id=r[1], role=r[2], content=r[3],
                    script=r[4], part_id=r[5], created_at=r[6],
                )
                for r in cur.fetchall()
            ]
    finally:
        put_connection(conn)


PART_COLORS = [
    '#4488CC', '#CC4444', '#44AA44', '#CC8844', '#8844CC',
    '#44AAAA', '#AA44AA', '#6688CC', '#CC6688', '#88AA44',
]

BEST_OF_N_OBJECTS = {
    "wheel": 3,
}


def _candidate_instruction(object_type: Optional[str], idx: int, total: int) -> str:
    if object_type == "wheel":
        wheel_variants = [
            "Candidate 1: prioritize a clean one-piece road alloy rim with a strong barrel, realistic hub face, and classic evenly spaced spokes.",
            "Candidate 2: prioritize a more sculpted spoke form and clearer rim lip detail while keeping the wheel body fully connected.",
            "Candidate 3: prioritize robust geometry and manufacturing realism over decorative detail; keep the center bore, lug-hole region, and spoke-to-barrel transitions very clear.",
        ]
        return wheel_variants[min(idx, len(wheel_variants) - 1)]
    return f"Candidate {idx + 1} of {total}: explore a distinct but valid geometry strategy while preserving the structured engineering spec."


def _evaluate_candidate(spec: dict, plan: dict, script: str) -> dict:
    candidate = {
        "script": script,
        "score": -1000,
        "multi_parts": None,
        "part_names": [],
        "validation_errors": [],
        "exec_error": None,
    }
    try:
        multi_parts = execute_script_multi(script)
        part_names = list(multi_parts.keys())
        ok, validation_errors = validate_generation(spec, plan, part_names, script)
        candidate["multi_parts"] = multi_parts
        candidate["part_names"] = part_names
        candidate["validation_errors"] = validation_errors
        if ok:
            score = 100
            if not plan.get("is_multi_part", True) and len(part_names) == 1:
                score += 20
            if spec.get("object_type") == "wheel":
                script_l = script.lower()
                if "revolve" in script_l:
                    score += 8
                if "for " in script_l or "math.sin" in script_l or "math.cos" in script_l:
                    score += 8
                if "hole(" in script_l or "cutthruall" in script_l or ".cut(" in script_l:
                    score += 8
                # Reward proper sub-parts (Hub, Barrel, Spokes)
                has_spokes = any("spoke" in n.lower() for n in part_names)
                has_hub = any("hub" in n.lower() for n in part_names)
                has_barrel = any("barrel" in n.lower() or "rim" in n.lower() for n in part_names)
                if has_spokes:
                    score += 10
                if has_hub:
                    score += 5
                if has_barrel:
                    score += 5
                # Penalize visualization-only parts
                viz_keywords = ["bolt hole", "center bore", "drop center", "well ring",
                                "counterbore", "visualization", "highlight", "viz"]
                viz_count = sum(1 for n in part_names
                                if any(kw in n.lower() for kw in viz_keywords))
                score -= viz_count * 15
                # Reward loft usage for sculpted spokes
                if ".loft(" in script_l and has_spokes:
                    score += 5
                # Penalize Y-axis revolve ONLY if NOT using XZ workplane
                # In XZ workplane, (0,1,0) is correct (local Y = global Z)
                # But in XY workplane, (0,1,0) would be wrong
                uses_xz = '"XZ"' in script or "'XZ'" in script
                if not uses_xz:
                    if "(0,1,0)" in script.replace(" ", "") or "(0, 1, 0)" in script:
                        score -= 20
            candidate["score"] = score
        else:
            candidate["exec_error"] = build_retry_feedback(spec, plan, validation_errors)
            candidate["score"] = 20 - (len(validation_errors) * 4)
    except Exception as exc:
        candidate["exec_error"] = str(exc)
        candidate["score"] = -200
    return candidate


def _generate_best_candidate(client, system_prompt: str, messages: list, spec: dict, plan: dict) -> dict:
    object_type = spec.get("object_type")
    attempts = BEST_OF_N_OBJECTS.get(object_type, 1)
    candidates = []
    api_errors = []

    for idx in range(attempts):
        attempt_prompt = system_prompt
        if attempts > 1:
            attempt_prompt += "\n\nCANDIDATE EXPLORATION:\n" + _candidate_instruction(object_type, idx, attempts)
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=attempt_prompt,
                messages=messages,
            )
            script = response.content[0].text.strip()
            candidate = _evaluate_candidate(spec, plan, script)
            candidate["candidate_index"] = idx + 1
            candidates.append(candidate)
        except anthropic.APIError as exc:
            api_errors.append(exc)

    if not candidates:
        if api_errors:
            raise api_errors[-1]
        raise RuntimeError("No generation candidates produced")

    best = max(candidates, key=lambda c: c["score"])
    best["candidate_count"] = attempts
    best["candidates"] = candidates
    return best


@app.post("/assemblies/{assembly_id}/quick-generate")
async def quick_generate(assembly_id: int, request: dict):
    """Generate a new part from a text description and/or image, add it to the assembly."""
    description = request.get("description", "").strip()
    image_base64 = request.get("image_base64")
    part_name = request.get("part_name", "Generated Part")

    if not description and not image_base64:
        raise HTTPException(status_code=400, detail="Provide a description or image")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    conn = get_connection()
    project_id = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT project_id FROM assemblies WHERE id=%s", (assembly_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Assembly not found")
            project_id = row[0]
    finally:
        put_connection(conn)

    client = anthropic.Anthropic(api_key=api_key)

    system = (
        "You are a CadQuery expert. Generate a single CadQuery Python script for the described part.\n"
        "Rules:\n"
        "- Use realistic engineering dimensions\n"
        "- Assign the final shape to a variable named `result`\n"
        "- Return ONLY the Python script, no explanation, no markdown fences\n"
        "- Do NOT use show_object()\n"
        "- Script must be self-contained (import cadquery as cq at the top)\n"
    )

    user_content = []
    if image_base64:
        user_content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_base64}})
    user_content.append({"type": "text", "text": description or "Generate a part based on the image above."})

    resp = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    script = resp.content[0].text.strip()
    # Strip markdown fences if present
    if script.startswith("```"):
        lines = script.split("\n")
        script = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Validate by executing
    try:
        wp = execute_script(script)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Generated script failed: {e}")

    # Compute mesh + bbox
    mesh = shape_to_topo_mesh(wp)
    bbox_data = extract_bounding_box(wp)
    script_hash = _script_hash(script)

    # Save part to assembly
    conn2 = get_connection()
    try:
        with conn2.cursor() as cur:
            cur.execute(
                """INSERT INTO parts
                   (assembly_id, name, cadquery_script, script_hash, mesh_cache,
                    bbox_min_x, bbox_min_y, bbox_min_z, bbox_max_x, bbox_max_y, bbox_max_z)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    assembly_id, part_name, script, script_hash, json.dumps(mesh),
                    bbox_data.get("bbox_min_x"), bbox_data.get("bbox_min_y"), bbox_data.get("bbox_min_z"),
                    bbox_data.get("bbox_max_x"), bbox_data.get("bbox_max_y"), bbox_data.get("bbox_max_z"),
                ),
            )
            part_id = cur.fetchone()[0]
            from parametric_templates import extract_script_params as _extract_params
            _extracted = _extract_params(script)
            if _extracted:
                cur.execute("UPDATE parts SET parametric_params=%s WHERE id=%s",
                            (_json.dumps(_extracted), part_id))
        conn2.commit()
    finally:
        put_connection(conn2)

    mesh["id"] = part_id
    mesh["name"] = part_name
    # Broadcast new part mesh to all collaborators in the project
    await broadcast_mesh_update(project_id, part_id)
    return {"part_id": part_id, "part_name": part_name, "mesh": mesh}


@app.post("/projects/{project_id}/chat")
async def project_chat(project_id: int, request: ProjectChatRequest, user: UserInfo = Depends(get_current_user)):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY is not set. Add it to backend/.env",
        )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Project not found")
        put_connection(conn)
        conn = None
    except HTTPException:
        put_connection(conn)
        raise

    client = anthropic.Anthropic(api_key=api_key)
    # Build messages for the API, but truncate old assistant scripts to prevent
    # the model from blindly copying previous (potentially bad) generations.
    messages = []
    last_idx = len(request.messages) - 1
    for i, m in enumerate(request.messages):
        role = m["role"]
        content = m["content"]
        if role == "assistant" and len(content) > 500 and i < last_idx:
            # Summarize old scripts — keep first 3 lines and last line only
            lines = content.strip().split("\n")
            summary = "\n".join(lines[:3]) + "\n# ... (previous script, do NOT copy) ...\n" + lines[-1]
            messages.append({"role": role, "content": summary})
        elif role == "user" and i == last_idx and request.image_base64:
            # Attach image to the last user message
            messages.append({"role": role, "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": request.image_base64}},
                {"type": "text", "text": content},
            ]})
        else:
            messages.append({"role": role, "content": content})

    user_query = ""
    if request.messages:
        user_query = request.messages[-1].get("content", "")

    spec = build_engineering_spec(client, user_query, [], [])
    plan = build_assembly_plan(spec)

    # NOTE: knowledge_records and patterns lookups are disabled.
    # Claude's built-in knowledge vastly outperforms our stored patterns.
    # Assembly context (with actual scripts) provides the fitting data the AI needs.
    system_prompt = build_generation_prompt(
        request.assembly_id,
        user_query=user_query,
        spec=spec,
        plan=plan,
        references=[],
        knowledge_records=[],
        patterns=[],
    )

    candidate_results = []
    candidate_count = BEST_OF_N_OBJECTS.get(spec.get("object_type"), 1)
    try:
        best_candidate = _generate_best_candidate(client, system_prompt, messages, spec, plan)
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}")

    script = best_candidate["script"]
    multi_parts = best_candidate["multi_parts"]
    validation_errors = list(best_candidate["validation_errors"])
    exec_error = best_candidate["exec_error"]
    candidate_results = best_candidate.get("candidates", [])
    candidate_count = best_candidate.get("candidate_count", candidate_count)

    if exec_error:
        retry_messages = messages + [
            {"role": "assistant", "content": script},
            {"role": "user", "content": (
                f"{exec_error}\n\n"
                "Fix it and return only the corrected Python script."
            )},
        ]
        try:
            retry_response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=system_prompt,
                messages=retry_messages,
            )
            script = retry_response.content[0].text.strip()
            try:
                multi_parts = execute_script_multi(script)
                ok, validation_errors = validate_generation(spec, plan, list(multi_parts.keys()), script)
                if not ok:
                    raise ValueError(build_retry_feedback(spec, plan, validation_errors))
            except Exception as exc:
                exec_error = str(exc)
        except anthropic.APIError:
            pass

    part_names = list(multi_parts.keys()) if multi_parts else []
    reference_match_names = []
    knowledge_match_titles = []
    pattern_match_ids = []
    success = bool(script and not exec_error and part_names)

    # generation_feedback table removed — logging disabled
    if exec_error:
        raise HTTPException(status_code=400, detail=exec_error)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if request.messages:
                last = request.messages[-1]
                cur.execute(
                    """INSERT INTO chat_messages (project_id, role, content, user_id)
                       VALUES (%s, %s, %s, %s)""",
                    (project_id, last["role"], last["content"], user.id),
                )
            cur.execute(
                """INSERT INTO chat_messages (project_id, role, content, script, user_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (project_id, "assistant", script, script, user.id),
            )
        conn.commit()
    finally:
        put_connection(conn)

    return {
        "script": script,
        "multi_parts": part_names,
        "part_count": len(part_names),
        "spec": spec,
        "assembly_plan": plan,
        "reference_matches": reference_match_names,
        "knowledge_matches": knowledge_match_titles,
        "pattern_match_count": len(pattern_match_ids),
        "candidate_count": candidate_count,
    }


# ---------------------------------------------------------------------------
# Batch mesh (Phase 2)
# ---------------------------------------------------------------------------


@app.post("/assemblies/{assembly_id}/mesh-all")
async def mesh_all(assembly_id: int, topo: int = 0):
    """Execute every part script in an assembly, return all meshes.

    Pass ?topo=1 to include topology data (topo_faces, topo_edges) for each part.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, project_id, name, description FROM assemblies WHERE id = %s",
                (assembly_id,),
            )
            asm = cur.fetchone()
            if not asm:
                raise HTTPException(status_code=404, detail="Assembly not found")

            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS)} FROM parts WHERE assembly_id = %s AND (archived = false OR archived IS NULL) ORDER BY id",
                (assembly_id,),
            )
            rows = cur.fetchall()
    finally:
        put_connection(conn)

    use_topo = bool(topo)

    # Filter to renderable parts only
    parts_to_mesh = [
        _row_to_part(row, PART_COLUMNS)
        for row in rows
        if _row_to_part(row, PART_COLUMNS).get("part_type") != "component"
    ]

    def _compute_one(part: dict) -> dict:
        """Run in thread pool — compute or return cached mesh for one part."""
        entry = {
            "id": part["id"],
            "name": part["name"],
            "color": part["color"],
            "position": [part["position_x"], part["position_y"], part["position_z"]],
            "visible": part["visible"],
        }
        try:
            script = part["cadquery_script"]
            current_hash = _script_hash(script)

            # Feature-tree parts: mesh is managed by _do_rebuild, not script execution.
            # Always use mesh_cache for these; ignore cadquery_script entirely.
            if part.get("feature_tree_mode") and part.get("mesh_cache"):
                cached = part["mesh_cache"] if isinstance(part["mesh_cache"], dict) else json.loads(part["mesh_cache"])
                entry.update(cached)
                return entry

            # Use cached mesh if script unchanged
            if part.get("script_hash") == current_hash and part.get("mesh_cache"):
                cached = part["mesh_cache"] if isinstance(part["mesh_cache"], dict) else json.loads(part["mesh_cache"])
                entry.update(cached)
                return entry

            # Imported STEP/IGES parts: use step_data stored in DB, not the script
            if part.get("part_type") == "imported":
                if part.get("mesh_cache"):
                    # Use whatever cache is there even if hash mismatched
                    cached = part["mesh_cache"] if isinstance(part["mesh_cache"], dict) else json.loads(part["mesh_cache"])
                    entry.update(cached)
                    return entry
                # Cache missing — fetch step_data separately (it's bytea, don't bulk-load)
                import tempfile as _tf, os as _os, cadquery as _cq
                conn2 = get_connection()
                try:
                    with conn2.cursor() as cur2:
                        cur2.execute("SELECT step_data FROM parts WHERE id=%s", (part["id"],))
                        row2 = cur2.fetchone()
                finally:
                    put_connection(conn2)
                raw = row2[0] if row2 else None
                if not raw:
                    entry["error"] = "Imported part has no step_data and no mesh cache"
                    return entry
                with _tf.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
                    tmp.write(bytes(raw) if not isinstance(raw, bytes) else raw)
                    tmp_path = tmp.name
                try:
                    wp = _cq.importers.importStep(tmp_path)
                finally:
                    if _os.path.exists(tmp_path):
                        _os.unlink(tmp_path)
                mesh = shape_to_topo_mesh(wp) if use_topo else shape_to_mesh(wp)
                entry.update(mesh)
                # Cache this mesh so we don't re-import next time
                conn3 = get_connection()
                try:
                    with conn3.cursor() as cur3:
                        cur3.execute(
                            "UPDATE parts SET script_hash=%s, mesh_cache=%s WHERE id=%s",
                            (current_hash, json.dumps(mesh), part["id"]),
                        )
                    conn3.commit()
                finally:
                    put_connection(conn3)
                return entry

            # Recompute from script
            wp = execute_script(script)
            mesh = shape_to_topo_mesh(wp) if use_topo else shape_to_mesh(wp)
            entry.update(mesh)

            # Persist cache back to DB
            conn3 = get_connection()
            try:
                with conn3.cursor() as cur:
                    cur.execute(
                        "UPDATE parts SET script_hash=%s, mesh_cache=%s WHERE id=%s",
                        (current_hash, json.dumps(mesh), part["id"]),
                    )
                conn3.commit()
            finally:
                put_connection(conn3)

        except Exception as exc:
            entry["error"] = str(exc)
        return entry

    import asyncio
    loop = asyncio.get_event_loop()
    futures = [loop.run_in_executor(_mesh_executor, _compute_one, p) for p in parts_to_mesh]
    results = await asyncio.gather(*futures)

    return {"assembly_id": assembly_id, "parts": list(results)}


@app.post("/assemblies/{assembly_id}/mesh-all-bin")
async def mesh_all_bin(assembly_id: int):
    """Return all assembly meshes as a single binary blob.

    Layout:
      [4 bytes]  part_count (uint32 LE)
      For each part:
        [4 bytes]  chunk_length (uint32 LE) — total bytes for this part chunk
        [chunk_length bytes]  binary mesh data (same format as /topo-mesh-bin)
    """
    # Reuse existing mesh_all to compute meshes (with topo)
    json_result = await mesh_all(assembly_id, topo=1)
    parts = json_result.get("parts", [])

    buf = bytearray()
    buf.extend(struct.pack("<I", len(parts)))

    for p in parts:
        if p.get("error") or not p.get("vertices"):
            # Pack an empty chunk with just the header
            header = json.dumps({"id": p.get("id"), "name": p.get("name"), "error": p.get("error", "no mesh"),
                                 "vertex_count": 0, "face_count": 0,
                                 "color": p.get("color"), "position": p.get("position"),
                                 "visible": p.get("visible")}, separators=(",", ":")).encode()
            chunk = struct.pack("<I", len(header)) + header
        else:
            # Add part metadata into the header
            mesh_dict = {
                "id": p["id"],
                "vertex_count": p.get("vertex_count", 0),
                "face_count": p.get("face_count", 0),
                "name": p.get("name"),
                "color": p.get("color"),
                "position": p.get("position"),
                "visible": p.get("visible"),
            }
            if p.get("topo_faces"):
                mesh_dict["topo_faces"] = p["topo_faces"]
            if p.get("topo_edges"):
                mesh_dict["topo_edges"] = p["topo_edges"]
            # Copy vertices/faces for packing
            mesh_dict["vertices"] = p["vertices"]
            mesh_dict["faces"] = p["faces"]
            chunk = _pack_binary_mesh(mesh_dict)

        buf.extend(struct.pack("<I", len(chunk)))
        buf.extend(chunk)

    return Response(content=bytes(buf), media_type="application/octet-stream")


# ═══════════════════════════════════════════════════════════════════════════
# EXPORT & DFM ENDPOINTS — Phase 6
# ═══════════════════════════════════════════════════════════════════════════


def _load_assembly_parts(assembly_id: int):
    """Shared helper: load parts from DB and execute their scripts."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, project_id, name, description FROM assemblies WHERE id = %s",
                (assembly_id,),
            )
            asm = cur.fetchone()
            if not asm:
                raise HTTPException(status_code=404, detail="Assembly not found")

            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS)} FROM parts WHERE assembly_id = %s AND (archived = false OR archived IS NULL) ORDER BY id",
                (assembly_id,),
            )
            rows = cur.fetchall()
    finally:
        put_connection(conn)

    asm_name = asm[2]
    parts_with_wp = []
    for row in rows:
        part = _row_to_part(row, PART_COLUMNS)
        try:
            wp = execute_script(part["cadquery_script"])
            parts_with_wp.append({"part": part, "workplane": wp})
        except Exception:
            pass

    return asm_name, parts_with_wp


# ── Per-part exports ─────────────────────────────────────────────────────

@app.get("/parts/{part_id}/export/stl")
async def export_part_stl(part_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS)} FROM parts WHERE id = %s",
                (part_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
    finally:
        put_connection(conn)

    part = _row_to_part(row, PART_COLUMNS)
    try:
        wp = execute_script(part["cadquery_script"])
        data = export_stl(wp)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    safe_name = part["name"].replace("/", "_").replace("\\", "_")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.stl"'},
    )


@app.get("/parts/{part_id}/export/step")
async def export_part_step(part_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS)} FROM parts WHERE id = %s",
                (part_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
    finally:
        put_connection(conn)

    part = _row_to_part(row, PART_COLUMNS)
    try:
        wp = execute_script(part["cadquery_script"])
        data = export_step(wp)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    safe_name = part["name"].replace("/", "_").replace("\\", "_")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.step"'},
    )


@app.get("/parts/{part_id}/brep")
async def get_part_brep(part_id: int):
    """Return B-Rep data for client-side tessellation."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(PART_COLUMNS)} FROM parts WHERE id = %s",
                (part_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Part not found")
    finally:
        put_connection(conn)

    part = _row_to_part(row, PART_COLUMNS)
    try:
        wp = execute_script(part["cadquery_script"])
        data = export_brep(wp)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    safe_name = part["name"].replace("/", "_").replace("\\", "_")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.brep"'},
    )


# ── Assembly exports ─────────────────────────────────────────────────────

@app.get("/assemblies/{assembly_id}/export/stl-zip")
async def export_assembly_stl_zip(assembly_id: int):
    asm_name, parts_with_wp = _load_assembly_parts(assembly_id)
    if not parts_with_wp:
        raise HTTPException(status_code=404, detail="No parts with valid geometry")

    parts_data = [{"name": p["part"]["name"], "workplane": p["workplane"]} for p in parts_with_wp]
    try:
        data = export_parts_stl_zip(parts_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    safe_name = asm_name.replace("/", "_").replace("\\", "_")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_stl.zip"'},
    )


@app.get("/assemblies/{assembly_id}/export/step")
async def export_assembly_step_file(assembly_id: int):
    asm_name, parts_with_wp = _load_assembly_parts(assembly_id)
    if not parts_with_wp:
        raise HTTPException(status_code=404, detail="No parts with valid geometry")

    parts_data = [{"name": p["part"]["name"], "workplane": p["workplane"]} for p in parts_with_wp]
    try:
        data = export_assembly_step(parts_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    safe_name = asm_name.replace("/", "_").replace("\\", "_")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.step"'},
    )


# ── BOM ──────────────────────────────────────────────────────────────────

@app.get("/assemblies/{assembly_id}/export/bom")
async def export_bom(assembly_id: int, format: str = "json"):
    asm_name, parts_with_wp = _load_assembly_parts(assembly_id)

    bom_parts = []
    total_weight = 0.0
    total_volume = 0.0

    for entry in parts_with_wp:
        part = entry["part"]
        wp = entry["workplane"]
        vol_mm3 = compute_volume(wp)
        vol_cm3 = vol_mm3 / 1000.0
        density = MATERIAL_DENSITIES.get(part["material"], 7850)
        weight_g = (vol_cm3 / 1e3) * density

        material_names = {
            "steel": "Mild Steel", "chromoly_4130": "Chromoly 4130",
            "aluminum_6061": "Aluminum 6061", "aluminum_7075": "Aluminum 7075",
            "carbon_fiber": "Carbon Fiber", "titanium_gr5": "Titanium Grade 5",
            "abs_plastic": "ABS Plastic", "nylon_pa6": "Nylon (PA6)",
            "stainless_304": "Stainless Steel 304",
        }

        bbox = extract_bounding_box(wp)
        if bbox:
            dx = bbox["bbox_max_x"] - bbox["bbox_min_x"]
            dy = bbox["bbox_max_y"] - bbox["bbox_min_y"]
            dz = bbox["bbox_max_z"] - bbox["bbox_min_z"]
            dims = f"{dx:.1f} x {dy:.1f} x {dz:.1f}"
        else:
            dims = "—"

        bom_parts.append({
            "name": part["name"],
            "material": part["material"],
            "material_name": material_names.get(part["material"], part["material"]),
            "volume_cm3": round(vol_cm3, 1),
            "weight_g": round(weight_g, 0),
            "dimensions_mm": dims,
            "quantity": 1,
        })
        total_weight += weight_g
        total_volume += vol_cm3

    bom = {
        "assembly_name": asm_name,
        "parts": bom_parts,
        "total_parts": len(bom_parts),
        "total_weight_g": round(total_weight, 0),
        "total_volume_cm3": round(total_volume, 1),
    }

    if format == "csv":
        import csv
        import io as _io

        output = _io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "Material", "Volume (cm3)", "Weight (g)", "Dimensions (mm)", "Qty"])
        for p in bom_parts:
            writer.writerow([p["name"], p["material_name"], p["volume_cm3"],
                             p["weight_g"], p["dimensions_mm"], p["quantity"]])
        writer.writerow([])
        writer.writerow(["Total Parts", len(bom_parts)])
        writer.writerow(["Total Weight (g)", round(total_weight, 0)])
        writer.writerow(["Total Volume (cm3)", round(total_volume, 1)])

        csv_bytes = output.getvalue().encode("utf-8")
        safe_name = asm_name.replace("/", "_").replace("\\", "_")
        return Response(
            content=csv_bytes,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_bom.csv"'},
        )

    return bom


# ── DFM check ────────────────────────────────────────────────────────────

class DFMCheckRequest(BaseModel):
    method: str = "cnc"


@app.post("/assemblies/{assembly_id}/dfm-check")
async def dfm_check(assembly_id: int, request: DFMCheckRequest):
    asm_name, parts_with_wp = _load_assembly_parts(assembly_id)

    dfm_parts = []
    for entry in parts_with_wp:
        part = entry["part"]
        wp = entry["workplane"]
        bbox = extract_bounding_box(wp)
        vol = compute_volume(wp)

        bbox_dims = {}
        if bbox:
            bbox_dims = {
                "width": bbox["bbox_max_x"] - bbox["bbox_min_x"],
                "height": bbox["bbox_max_y"] - bbox["bbox_min_y"],
                "length": bbox["bbox_max_z"] - bbox["bbox_min_z"],
            }

        dfm_parts.append({
            "name": part["name"],
            "material": part["material"],
            "bbox": bbox_dims,
            "volume_mm3": vol,
        })

    warnings = check_dfm(dfm_parts, request.method)
    return {"warnings": [w.to_dict() for w in warnings]}


# ── AI-powered per-part DFM analysis ─────────────────────────────────────

@app.post("/parts/{part_id}/dfm-check")
async def dfm_check_part(part_id: int):
    """Run an AI-powered DFM analysis on a single part's CadQuery script.
    Returns structured JSON: geometry_summary, issues, passes, suggestions.
    Does NOT modify the part.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, cadquery_script, material FROM parts WHERE id = %s",
                (part_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Part not found")
        name, script, material = row
        if not script:
            raise HTTPException(status_code=400, detail="Part has no script to analyze")

        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": f"""You are a senior DFM (Design for Manufacturing) engineer.
Analyze the following CadQuery 3D part script and produce a structured DFM report.

Part name: {name}
Material: {material or 'unknown'}

CadQuery script:
```python
{script}
```

Return ONLY valid JSON — no markdown fences, no prose, no explanation.
Schema:
{{
  "geometry_summary": {{
    "<label>": "<value with units>"
  }},
  "issues": [
    {{ "severity": "error|warning", "message": "<concise issue description>" }}
  ],
  "passes": [
    {{ "severity": "pass|info", "message": "<what is fine or informational>" }}
  ],
  "suggestions": [
    {{
      "id": "s1",
      "title": "<short action title, max 6 words>",
      "description": "<why this matters and what to change, 1-2 sentences>",
      "fix_instruction": "<exact plain-English instruction that can be sent to edit-with-ai to apply this fix>"
    }}
  ]
}}

Rules:
- geometry_summary: 4-8 key dimensions/properties extracted directly from the script
- issues: only real problems (wall too thin, sharp re-entrant corners, undercuts, etc.)
- passes: things that are correctly done or acceptable
- suggestions: maximum 3, ordered by impact; each fix_instruction must be a self-contained CAD edit command
"""
            }]
        )

        raw = response.content[0].text.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        import json as _json
        try:
            report = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"AI returned invalid JSON: {exc}")

        return {
            "part_id": part_id,
            "part_name": name,
            **report,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        put_connection(conn)


# ═══════════════════════════════════════════════════════════════════════════
# PARAMETRIC ENGINE ENDPOINTS — Phase 7
# ═══════════════════════════════════════════════════════════════════════════

_engine = ParametricEngine()

OP_COLUMNS = ["id", "part_id", "sequence", "operation", "parameters", "parent_op_id"]


def _load_operations(cur, part_id: int) -> list:
    cur.execute(
        "SELECT id, part_id, sequence, operation, parameters, parent_op_id "
        "FROM operations WHERE part_id = %s ORDER BY sequence",
        (part_id,),
    )
    return [
        Operation(
            id=r[0], part_id=r[1], sequence=r[2], operation=r[3],
            parameters=r[4] if isinstance(r[4], dict) else _json.loads(r[4]),
            parent_op_id=r[5],
        )
        for r in cur.fetchall()
    ]


def _rebuild_part(cur, part_id: int, operations: list):
    """Rebuild geometry from operations, update parts table with new bbox + script."""
    wp = _engine.build(operations)
    bbox = extract_bounding_box(wp)
    mesh = shape_to_mesh(wp)

    cur.execute(
        """UPDATE parts SET
               bbox_min_x = %s, bbox_min_y = %s, bbox_min_z = %s,
               bbox_max_x = %s, bbox_max_y = %s, bbox_max_z = %s,
               updated_at = now()
           WHERE id = %s""",
        (
            bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
            bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
            part_id,
        ),
    )
    return wp, mesh


@app.get("/parts/{part_id}/operations", response_model=List[OperationResponse])
async def list_operations(part_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM parts WHERE id = %s", (part_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Part not found")
            ops = _load_operations(cur, part_id)
            return [
                OperationResponse(
                    id=op.id, part_id=op.part_id, sequence=op.sequence,
                    operation=op.operation, parameters=op.parameters,
                    parent_op_id=op.parent_op_id,
                )
                for op in ops
            ]
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/operations", response_model=OperationResponse)
async def add_operation(part_id: int, body: OperationCreate):
    if body.operation not in OPERATION_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown operation: {body.operation}")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM parts WHERE id = %s", (part_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Part not found")

            cur.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM operations WHERE part_id = %s",
                (part_id,),
            )
            max_seq = cur.fetchone()[0]

            cur.execute(
                """INSERT INTO operations (part_id, sequence, operation, parameters, parent_op_id)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, part_id, sequence, operation, parameters, parent_op_id""",
                (part_id, max_seq + 1, body.operation,
                 _json.dumps(body.parameters), body.parent_op_id),
            )
            row = cur.fetchone()
        conn.commit()
        params = row[4] if isinstance(row[4], dict) else _json.loads(row[4])
        return OperationResponse(
            id=row[0], part_id=row[1], sequence=row[2],
            operation=row[3], parameters=params, parent_op_id=row[5],
        )
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.put("/operations/{op_id}", response_model=OperationResponse)
async def update_operation(op_id: int, body: OperationUpdate):
    if body.parameters is None:
        raise HTTPException(status_code=400, detail="No parameters to update")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE operations SET parameters = %s
                   WHERE id = %s
                   RETURNING id, part_id, sequence, operation, parameters, parent_op_id""",
                (_json.dumps(body.parameters), op_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Operation not found")
        conn.commit()
        params = row[4] if isinstance(row[4], dict) else _json.loads(row[4])
        return OperationResponse(
            id=row[0], part_id=row[1], sequence=row[2],
            operation=row[3], parameters=params, parent_op_id=row[5],
        )
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.delete("/operations/{op_id}")
async def delete_operation(op_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT part_id FROM operations WHERE id = %s", (op_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Operation not found")
            part_id = row[0]

            cur.execute("DELETE FROM operations WHERE id = %s", (op_id,))

            cur.execute(
                "SELECT id FROM operations WHERE part_id = %s ORDER BY sequence",
                (part_id,),
            )
            for i, r in enumerate(cur.fetchall(), 1):
                cur.execute(
                    "UPDATE operations SET sequence = %s WHERE id = %s", (i, r[0])
                )
        conn.commit()
        return {"deleted": True, "part_id": part_id}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.post("/parts/{part_id}/rebuild")
async def rebuild_part(part_id: int):
    """Rebuild part geometry from its operations, update bbox."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM parts WHERE id = %s", (part_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Part not found")

            ops = _load_operations(cur, part_id)
            if not ops:
                raise HTTPException(status_code=400, detail="No operations for this part")

            # If the part has a cadquery_script but no raw_script base operation,
            # inject one so modify operations (extrude, fillet, etc.) have geometry to work on
            has_base = any(o.operation in ("raw_script", "box", "cylinder", "sphere", "round_tube", "rect_tube") for o in ops)
            if not has_base:
                cur.execute("SELECT cadquery_script FROM parts WHERE id = %s", (part_id,))
                script_row = cur.fetchone()
                if script_row and script_row[0]:
                    # Insert raw_script as sequence 0 so it runs first
                    cur.execute(
                        """INSERT INTO operations (part_id, sequence, operation, parameters)
                           VALUES (%s, 0, 'raw_script', %s)
                           RETURNING id""",
                        (part_id, _json.dumps({"script": script_row[0]})),
                    )
                    base_op_id = cur.fetchone()[0]
                    ops.insert(0, Operation(
                        id=base_op_id, part_id=part_id, sequence=0,
                        operation="raw_script",
                        parameters={"script": script_row[0]},
                    ))

            wp, mesh = _rebuild_part(cur, part_id, ops)
        conn.commit()
        return {"success": True, "part_id": part_id, **mesh}
    except (HTTPException, ValueError) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.get("/parametric-templates")
async def get_parametric_templates():
    """List available parametric templates and their parameter schemas."""
    return list_templates()


# ═══════════════════════════════════════════════════════════════════════════
# Template Library API (Canva-like template marketplace)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/template-categories")
async def get_template_categories():
    """List all template categories."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, slug, description, icon, sort_order FROM template_categories ORDER BY sort_order")
            rows = cur.fetchall()
            return [{"id": r[0], "name": r[1], "slug": r[2], "description": r[3], "icon": r[4], "sort_order": r[5]} for r in rows]
    finally:
        put_connection(conn)


@app.get("/api/templates")
async def get_templates(category: Optional[str] = None, search: Optional[str] = None, featured: Optional[bool] = None):
    """List templates with optional filtering by category, search term, or featured status."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT t.id, t.slug, t.name, t.description, t.tags, t.param_schema,
                       t.generator_key, t.is_featured, t.use_count, t.created_at,
                       c.name as category_name, c.slug as category_slug
                FROM templates t
                LEFT JOIN template_categories c ON t.category_id = c.id
                WHERE t.is_published = true
            """
            params = []
            if category:
                sql += " AND c.slug = %s"
                params.append(category)
            if featured is not None:
                sql += " AND t.is_featured = %s"
                params.append(featured)
            if search:
                sql += " AND (t.name ILIKE %s OR t.description ILIKE %s OR %s = ANY(t.tags))"
                params.extend([f"%{search}%", f"%{search}%", search.lower()])
            sql += " ORDER BY t.is_featured DESC, t.use_count DESC, t.name"
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [{
                "id": r[0], "slug": r[1], "name": r[2], "description": r[3],
                "tags": r[4] or [], "param_schema": r[5] if isinstance(r[5], dict) else json.loads(r[5]) if r[5] else {},
                "generator_key": r[6], "is_featured": r[7], "use_count": r[8],
                "created_at": r[9].isoformat() if r[9] else None,
                "category_name": r[10], "category_slug": r[11],
            } for r in rows]
    finally:
        put_connection(conn)


@app.get("/api/templates/{slug}")
async def get_template_detail(slug: str):
    """Get a single template by slug with full parameter schema."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.id, t.slug, t.name, t.description, t.tags, t.param_schema,
                       t.generator_key, t.is_featured, t.use_count,
                       c.name as category_name, c.slug as category_slug
                FROM templates t
                LEFT JOIN template_categories c ON t.category_id = c.id
                WHERE t.slug = %s AND t.is_published = true
            """, (slug,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="Template not found")
            return {
                "id": r[0], "slug": r[1], "name": r[2], "description": r[3],
                "tags": r[4] or [], "param_schema": r[5] if isinstance(r[5], dict) else json.loads(r[5]) if r[5] else {},
                "generator_key": r[6], "is_featured": r[7], "use_count": r[8],
                "category_name": r[9], "category_slug": r[10],
            }
    finally:
        put_connection(conn)


@app.post("/api/templates/{slug}/use")
async def use_template(slug: str, user=Depends(get_current_user)):
    """Create a new project from a template. Returns the new project ID."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Look up template
            cur.execute("SELECT generator_key, name, param_schema FROM templates WHERE slug = %s AND is_published = true", (slug,))
            tpl_row = cur.fetchone()
            if not tpl_row:
                raise HTTPException(status_code=404, detail="Template not found")
            generator_key, tpl_name, param_schema_raw = tpl_row

            # Generate script with default params
            script = generate_from_template(generator_key, {})

            # Create project
            cur.execute(
                "INSERT INTO projects (user_id, name, description) VALUES (%s, %s, %s) RETURNING id",
                (user.id, tpl_name, f"Created from template: {tpl_name}")
            )
            project_id = cur.fetchone()[0]

            # Create assembly
            cur.execute(
                "INSERT INTO assemblies (project_id, name) VALUES (%s, %s) RETURNING id",
                (project_id, "Main Assembly")
            )
            assembly_id = cur.fetchone()[0]

            # Execute script to get mesh + bbox
            try:
                wp = execute_script(script)
                mesh = shape_to_topo_mesh(wp)
                bbox = extract_bounding_box(wp)
            except Exception as e:
                mesh = None
                bbox = {}

            # Create part
            cur.execute("""
                INSERT INTO parts (assembly_id, name, cadquery_script, parametric_type, parametric_params,
                    bbox_min_x, bbox_min_y, bbox_min_z, bbox_max_x, bbox_max_y, bbox_max_z)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                assembly_id, tpl_name, script, generator_key,
                json.dumps({k: v.get("default") for k, v in (param_schema_raw if isinstance(param_schema_raw, dict) else json.loads(param_schema_raw) if param_schema_raw else {}).items()}),
                bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
            ))
            part_id = cur.fetchone()[0]

            # Increment use count
            cur.execute("UPDATE templates SET use_count = use_count + 1 WHERE slug = %s", (slug,))

            conn.commit()
            return {"project_id": project_id, "assembly_id": assembly_id, "part_id": part_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_connection(conn)


_template_mesh_cache: dict = {}

@app.get("/api/templates/{slug}/preview-mesh")
async def get_template_preview_mesh(slug: str):
    """Generate and return mesh data for a template with default parameters (for thumbnail rendering)."""
    # Serve from in-memory cache if available
    if slug in _template_mesh_cache:
        return Response(
            content=_template_mesh_cache[slug],
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT generator_key FROM templates WHERE slug = %s AND is_published = true", (slug,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Template not found")
            generator_key = row[0]
    finally:
        put_connection(conn)

    # Generate script with defaults and execute
    try:
        script = generate_from_template(generator_key, {})
        wp = execute_script(script)
        mesh = shape_to_topo_mesh(wp)
        import json as _json
        cached = _json.dumps(mesh)
        _template_mesh_cache[slug] = cached
        return Response(
            content=cached,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mesh generation failed: {e}")


@app.get("/projects/{project_id}/thumbnail-mesh")
async def get_project_thumbnail_mesh(project_id: int):
    """Return cached mesh of the first visible part in a project (for dashboard thumbnails)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.mesh_cache, p.cadquery_script
                   FROM parts p
                   JOIN assemblies a ON a.id = p.assembly_id
                   WHERE a.project_id = %s AND p.visible = true
                   ORDER BY p.id ASC LIMIT 1""",
                (project_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"vertices": [], "faces": []}
            mesh_cache, script = row
            if mesh_cache and isinstance(mesh_cache, dict) and mesh_cache.get("vertices"):
                return {"vertices": mesh_cache["vertices"], "faces": mesh_cache.get("faces", [])}
            # Fallback: execute script
            if script and script.strip():
                try:
                    wp = execute_script(script)
                    mesh = shape_to_topo_mesh(wp)
                    return {"vertices": mesh.get("vertices", []), "faces": mesh.get("faces", [])}
                except Exception:
                    pass
            return {"vertices": [], "faces": []}
    finally:
        put_connection(conn)


@app.put("/parts/{part_id}/parametric")
async def update_parametric(part_id: int, body: ParametricUpdateRequest):
    """Update parametric params, regenerate script, rebuild mesh."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT cadquery_script, parametric_type, parametric_params, part_type FROM parts WHERE id = %s", (part_id,))
            part_row = cur.fetchone()
            if not part_row:
                raise HTTPException(status_code=404, detail="Part not found")

            # part_row: cadquery_script, parametric_type, parametric_params, part_type
            existing = part_row[2] if isinstance(part_row[2], dict) else (_json.loads(part_row[2]) if part_row[2] else {})
            merged = {**existing, **body.parametric_params}

            # Imported STEP/IGES parts have no runnable script — just update stored params
            if part_row[3] == "imported":
                cur.execute(
                    "UPDATE parts SET parametric_params = %s, updated_at = now() WHERE id = %s",
                    (_json.dumps(merged), part_id),
                )
                conn.commit()
                return {"part_id": part_id, "mesh": None, "parametric_params": merged}

            if part_row[1]:  # parametric_type exists — use template
                new_script = generate_from_template(part_row[1], merged)
            else:
                # Non-template AI-generated part — replay with substituted values
                from parametric_templates import replay_script_with_params
                new_script = replay_script_with_params(part_row[0], merged)

            # Execute to validate and get bbox + mesh
            wp_result = execute_script(new_script)
            bbox = extract_bounding_box(wp_result)
            mesh_result = shape_to_topo_mesh(wp_result)
            mesh_result["id"] = part_id

            # Update part
            cur.execute(
                """UPDATE parts SET
                       cadquery_script = %s,
                       parametric_params = %s,
                       bbox_min_x = %s, bbox_min_y = %s, bbox_min_z = %s,
                       bbox_max_x = %s, bbox_max_y = %s, bbox_max_z = %s,
                       updated_at = now()
                   WHERE id = %s""",
                (
                    new_script, _json.dumps(merged),
                    bbox.get("bbox_min_x"), bbox.get("bbox_min_y"), bbox.get("bbox_min_z"),
                    bbox.get("bbox_max_x"), bbox.get("bbox_max_y"), bbox.get("bbox_max_z"),
                    part_id,
                ),
            )

            # Update raw_script operation if it exists
            cur.execute(
                "UPDATE operations SET parameters = %s WHERE part_id = %s AND operation = 'raw_script'",
                (_json.dumps({"script": new_script}), part_id),
            )

        conn.commit()
        return {"part_id": part_id, "mesh": mesh_result, "parametric_params": merged}
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        put_connection(conn)


@app.put("/operations/{op_id}/parameter")
async def update_single_parameter(op_id: int, body: ParameterUpdateRequest):
    """Update a single parameter, rebuild, return new mesh."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT part_id, parameters FROM operations WHERE id = %s", (op_id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Operation not found")

            part_id = row[0]
            params = row[1] if isinstance(row[1], dict) else _json.loads(row[1])
            params[body.param_name] = body.value

            cur.execute(
                "UPDATE operations SET parameters = %s WHERE id = %s",
                (_json.dumps(params), op_id),
            )

            ops = _load_operations(cur, part_id)
            wp, mesh = _rebuild_part(cur, part_id, ops)
        conn.commit()
        return {"success": True, "part_id": part_id, "op_id": op_id, **mesh}
    except (HTTPException, ValueError) as exc:
        conn.rollback()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        conn.rollback()
        raise
    finally:
        put_connection(conn)


@app.get("/operations/schema/{operation_name}")
async def get_operation_schema(operation_name: str):
    """Return the parameter schema for an operation type."""
    try:
        schema = get_param_schema(operation_name)
        return {"operation": operation_name, "schema": schema}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# Teams CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TeamCreate(BaseModel):
    name: str


class TeamMemberAdd(BaseModel):
    user_id: int


@app.post("/orgs/{org_id}/teams")
async def create_team(org_id: int, body: TeamCreate, user: UserInfo = Depends(get_current_user)):
    if not is_org_admin(user.id, org_id):
        raise HTTPException(status_code=403, detail="Must be org admin")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (org_id, name) VALUES (%s, %s) RETURNING id, org_id, name",
                (org_id, body.name),
            )
            row = cur.fetchone()
        conn.commit()
        return {"id": row[0], "org_id": row[1], "name": row[2]}
    finally:
        put_connection(conn)


@app.get("/orgs/{org_id}/teams")
async def list_teams(org_id: int, user: UserInfo = Depends(get_current_user)):
    if not is_org_member(user.id, org_id):
        raise HTTPException(status_code=403, detail="Not a member of this org")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.id, t.name, COUNT(tm.id) as member_count
                   FROM teams t LEFT JOIN team_members tm ON tm.team_id = t.id
                   WHERE t.org_id = %s GROUP BY t.id ORDER BY t.name""",
                (org_id,),
            )
            return [{"id": r[0], "name": r[1], "member_count": r[2]} for r in cur.fetchall()]
    finally:
        put_connection(conn)


@app.get("/teams/{team_id}")
async def get_team(team_id: int, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, org_id, name FROM teams WHERE id = %s", (team_id,))
            team = cur.fetchone()
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")
            if not is_org_member(user.id, team[1]):
                raise HTTPException(status_code=403, detail="Not a member of this org")
            cur.execute(
                """SELECT u.id, u.email, u.name FROM users u
                   JOIN team_members tm ON tm.user_id = u.id
                   WHERE tm.team_id = %s""",
                (team_id,),
            )
            members = [{"id": r[0], "email": r[1], "name": r[2]} for r in cur.fetchall()]
            return {"id": team[0], "org_id": team[1], "name": team[2], "members": members}
    finally:
        put_connection(conn)


@app.post("/teams/{team_id}/members")
async def add_team_member(team_id: int, body: TeamMemberAdd, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT org_id FROM teams WHERE id = %s", (team_id,))
            team = cur.fetchone()
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")
            if not is_org_admin(user.id, team[0]):
                raise HTTPException(status_code=403, detail="Must be org admin")
            cur.execute(
                "INSERT INTO team_members (team_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
                (team_id, body.user_id),
            )
        conn.commit()
        return {"ok": True}
    finally:
        put_connection(conn)


@app.delete("/teams/{team_id}/members/{user_id}")
async def remove_team_member(team_id: int, user_id: int, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT org_id FROM teams WHERE id = %s", (team_id,))
            team = cur.fetchone()
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")
            if not is_org_admin(user.id, team[0]):
                raise HTTPException(status_code=403, detail="Must be org admin")
            cur.execute("DELETE FROM team_members WHERE team_id = %s AND user_id = %s", (team_id, user_id))
        conn.commit()
        return {"ok": True}
    finally:
        put_connection(conn)


@app.delete("/teams/{team_id}")
async def delete_team(team_id: int, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT org_id FROM teams WHERE id = %s", (team_id,))
            team = cur.fetchone()
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")
            if not is_org_admin(user.id, team[0]):
                raise HTTPException(status_code=403, detail="Must be org admin")
            cur.execute("DELETE FROM teams WHERE id = %s", (team_id,))
        conn.commit()
        return {"ok": True}
    finally:
        put_connection(conn)


# ═══════════════════════════════════════════════════════════════════════════
# Invitations
# ═══════════════════════════════════════════════════════════════════════════


class InvitationCreate(BaseModel):
    email: str
    role: str = "member"


@app.get("/invitations/pending")
async def get_pending_invitations(user: UserInfo = Depends(get_current_user)):
    """Return all pending invitations for the current user's email."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT i.id, i.token, i.role, i.created_at, i.expires_at,
                          o.id as org_id, o.name as org_name,
                          u.name as invited_by_name
                   FROM invitations i
                   JOIN organizations o ON o.id = i.org_id
                   JOIN users u ON u.id = i.invited_by
                   WHERE lower(i.email) = lower(%s)
                     AND i.accepted_at IS NULL
                     AND i.expires_at > now()
                   ORDER BY i.created_at DESC""",
                (user.email,),
            )
            return [
                {
                    "id": r[0], "token": r[1], "role": r[2],
                    "created_at": str(r[3]), "expires_at": str(r[4]),
                    "org_id": r[5], "org_name": r[6], "invited_by": r[7],
                }
                for r in cur.fetchall()
            ]
    finally:
        put_connection(conn)


@app.post("/invitations/{token}/decline")
async def decline_invitation(token: str, user: UserInfo = Depends(get_current_user)):
    """Delete/decline a pending invitation."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM invitations WHERE token=%s AND lower(email)=lower(%s)",
                (token, user.email),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Invitation not found")
        conn.commit()
        return {"declined": True}
    except HTTPException:
        raise
    finally:
        put_connection(conn)


@app.post("/orgs/{org_id}/invitations")
async def create_invitation(org_id: int, body: InvitationCreate, user: UserInfo = Depends(get_current_user)):
    if not is_org_admin(user.id, org_id):
        raise HTTPException(status_code=403, detail="Must be org admin")
    import uuid
    from datetime import datetime, timedelta, timezone
    token = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO invitations (org_id, email, role, token, invited_by, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, token""",
                (org_id, body.email, body.role, token, user.id, expires),
            )
            row = cur.fetchone()
        conn.commit()
        return {"id": row[0], "token": row[1], "email": body.email, "role": body.role}
    finally:
        put_connection(conn)


@app.get("/orgs/{org_id}/invitations")
async def list_invitations(org_id: int, user: UserInfo = Depends(get_current_user)):
    if not is_org_admin(user.id, org_id):
        raise HTTPException(status_code=403, detail="Must be org admin")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, email, role, token, expires_at, accepted_at
                   FROM invitations WHERE org_id = %s AND accepted_at IS NULL
                   AND expires_at > now() ORDER BY created_at DESC""",
                (org_id,),
            )
            return [
                {"id": r[0], "email": r[1], "role": r[2], "token": r[3],
                 "expires_at": str(r[4]), "accepted": r[5] is not None}
                for r in cur.fetchall()
            ]
    finally:
        put_connection(conn)


@app.post("/invitations/{token}/accept")
async def accept_invitation(token: str, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, org_id, role FROM invitations
                   WHERE token = %s AND accepted_at IS NULL AND expires_at > now()""",
                (token,),
            )
            inv = cur.fetchone()
            if not inv:
                raise HTTPException(status_code=404, detail="Invitation not found or expired")
            cur.execute(
                """INSERT INTO org_members (org_id, user_id, role, invited_by)
                   VALUES (%s, %s, %s, NULL) ON CONFLICT DO NOTHING""",
                (inv[1], user.id, inv[2]),
            )
            cur.execute(
                "UPDATE invitations SET accepted_at = now() WHERE id = %s", (inv[0],)
            )
        conn.commit()
        return {"ok": True, "org_id": inv[1]}
    finally:
        put_connection(conn)


@app.delete("/invitations/{inv_id}")
async def cancel_invitation(inv_id: int, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT org_id FROM invitations WHERE id = %s", (inv_id,))
            inv = cur.fetchone()
            if not inv:
                raise HTTPException(status_code=404, detail="Invitation not found")
            if not is_org_admin(user.id, inv[0]):
                raise HTTPException(status_code=403, detail="Must be org admin")
            cur.execute("DELETE FROM invitations WHERE id = %s", (inv_id,))
        conn.commit()
        return {"ok": True}
    finally:
        put_connection(conn)


# ═══════════════════════════════════════════════════════════════════════════
# Project Sharing
# ═══════════════════════════════════════════════════════════════════════════


class ShareCreate(BaseModel):
    user_id: Optional[int] = None
    team_id: Optional[int] = None
    org_id: Optional[int] = None
    permission: str = "view"


class ShareUpdate(BaseModel):
    permission: str


@app.post("/projects/{project_id}/shares")
async def create_share(project_id: int, body: ShareCreate, user: UserInfo = Depends(get_current_user)):
    if not can_edit_project(user.id, project_id):
        raise HTTPException(status_code=403, detail="No edit access to this project")
    if not body.user_id and not body.team_id and not body.org_id:
        raise HTTPException(status_code=400, detail="Must specify user_id, team_id, or org_id")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO project_shares (project_id, user_id, team_id, org_id, permission)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (project_id, body.user_id, body.team_id, body.org_id, body.permission),
            )
            share_id = cur.fetchone()[0]
        conn.commit()
        return {"id": share_id}
    finally:
        put_connection(conn)


@app.get("/projects/{project_id}/shares")
async def list_shares(project_id: int, user: UserInfo = Depends(get_current_user)):
    if not can_view_project(user.id, project_id):
        raise HTTPException(status_code=403, detail="No access to this project")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ps.id, ps.user_id, ps.team_id, ps.org_id, ps.permission,
                          u.name as user_name, t.name as team_name, o.name as org_name
                   FROM project_shares ps
                   LEFT JOIN users u ON u.id = ps.user_id
                   LEFT JOIN teams t ON t.id = ps.team_id
                   LEFT JOIN organizations o ON o.id = ps.org_id
                   WHERE ps.project_id = %s""",
                (project_id,),
            )
            return [
                {"id": r[0], "user_id": r[1], "team_id": r[2], "org_id": r[3],
                 "permission": r[4], "user_name": r[5], "team_name": r[6], "org_name": r[7]}
                for r in cur.fetchall()
            ]
    finally:
        put_connection(conn)


@app.put("/shares/{share_id}")
async def update_share(share_id: int, body: ShareUpdate, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT project_id FROM project_shares WHERE id = %s", (share_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Share not found")
            if not can_edit_project(user.id, row[0]):
                raise HTTPException(status_code=403, detail="No edit access")
            cur.execute(
                "UPDATE project_shares SET permission = %s WHERE id = %s",
                (body.permission, share_id),
            )
        conn.commit()
        return {"ok": True}
    finally:
        put_connection(conn)


@app.delete("/shares/{share_id}")
async def delete_share(share_id: int, user: UserInfo = Depends(get_current_user)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT project_id FROM project_shares WHERE id = %s", (share_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Share not found")
            if not can_edit_project(user.id, row[0]):
                raise HTTPException(status_code=403, detail="No edit access")
            cur.execute("DELETE FROM project_shares WHERE id = %s", (share_id,))
        conn.commit()
        return {"ok": True}
    finally:
        put_connection(conn)


# ═══════════════════════════════════════════════════════════════════════════
# Org-scoped Projects
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/orgs/{org_id}/projects")
async def list_org_projects(org_id: int, user: UserInfo = Depends(get_current_user)):
    if not is_org_member(user.id, org_id):
        raise HTTPException(status_code=403, detail="Not a member of this org")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description FROM projects WHERE org_id = %s ORDER BY name",
                (org_id,),
            )
            return [{"id": r[0], "name": r[1], "description": r[2]} for r in cur.fetchall()]
    finally:
        put_connection(conn)


# COLLABORATION WebSocket — Phase E
# ═══════════════════════════════════════════════════════════════════════════
# Engineering References — REMOVED (table dropped, Claude's built-in knowledge used instead)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/references/search")
async def search_references(q: str = "", limit: int = 5):
    """engineering_references table removed."""
    return {"results": [], "count": 0}


@app.get("/references/{ref_id}")
async def get_reference(ref_id: int):
    raise HTTPException(status_code=404, detail="engineering_references table removed")


@app.post("/references")
async def create_reference():
    raise HTTPException(status_code=410, detail="engineering_references table removed")


# ═══════════════════════════════════════════════════════════════════════════


@app.websocket("/ws/collaborate/{project_id}")
async def collaborate_ws(websocket: WebSocket, project_id: int):
    await websocket.accept()

    params = websocket.query_params
    user_id = params.get("userId", "anon")
    display_name = params.get("name", "User")

    room = get_or_create_room(project_id)
    pid = await handle_connect(room, websocket, user_id, display_name)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = _json.loads(raw)
            except Exception:
                continue
            await handle_message(room, pid, data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await handle_disconnect(room, pid)
        remove_empty_room(project_id)


# ═══════════════════════════════════════════════════════════════════════════
# Shareable Assembly Previews (public share links)
# ═══════════════════════════════════════════════════════════════════════════

import uuid as _uuid

@app.post("/assemblies/{assembly_id}/share-link")
async def create_share_link(assembly_id: int, user=Depends(get_current_user)):
    """Generate a public share link for an assembly."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Verify assembly exists and user has access
            cur.execute("SELECT a.id FROM assemblies a JOIN projects p ON a.project_id = p.id WHERE a.id = %s AND p.user_id = %s", (assembly_id, user.id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Assembly not found or access denied")

            # Check if a share link already exists
            cur.execute("SELECT token FROM assembly_shares WHERE assembly_id = %s AND is_active = true ORDER BY created_at DESC LIMIT 1", (assembly_id,))
            existing = cur.fetchone()
            if existing:
                return {"token": existing[0], "url": f"/share/{existing[0]}"}

            # Create new share token
            token = _uuid.uuid4().hex[:12]
            cur.execute(
                "INSERT INTO assembly_shares (assembly_id, token, created_by) VALUES (%s, %s, %s) RETURNING token",
                (assembly_id, token, user.id)
            )
            conn.commit()
            return {"token": token, "url": f"/share/{token}"}
    finally:
        put_connection(conn)


@app.get("/public/share/{token}")
async def get_public_share(token: str):
    """Public endpoint — returns assembly data with mesh for all parts. No auth required."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Look up share
            cur.execute("""
                SELECT s.assembly_id, s.is_active, a.name as assembly_name, p.name as project_name
                FROM assembly_shares s
                JOIN assemblies a ON s.assembly_id = a.id
                JOIN projects p ON a.project_id = p.id
                WHERE s.token = %s
            """, (token,))
            share = cur.fetchone()
            if not share or not share[1]:
                raise HTTPException(status_code=404, detail="Share link not found or expired")

            assembly_id, _, assembly_name, project_name = share

            # Increment view count
            cur.execute("UPDATE assembly_shares SET view_count = view_count + 1 WHERE token = %s", (token,))
            conn.commit()

            # Fetch all parts
            cur.execute("""
                SELECT id, name, cadquery_script, position_x, position_y, position_z,
                       rotation_x, rotation_y, rotation_z, material, color, visible,
                       parent_part_id, part_type
                FROM parts WHERE assembly_id = %s AND visible = true
                ORDER BY sort_order, id
            """, (assembly_id,))
            part_rows = cur.fetchall()

            parts = []
            for row in part_rows:
                part_data = {
                    "id": row[0], "name": row[1], "script": row[2],
                    "position": [row[3], row[4], row[5]],
                    "rotation": [row[6], row[7], row[8]],
                    "material": row[9], "color": row[10],
                    "parent_part_id": row[11], "part_type": row[12],
                    "mesh": None,
                }

                # Generate mesh for each part
                if row[2] and row[2].strip():
                    try:
                        wp = execute_script(row[2])
                        mesh = shape_to_topo_mesh(wp)
                        part_data["mesh"] = mesh
                    except Exception:
                        pass

                parts.append(part_data)

            return {
                "assembly_name": assembly_name,
                "project_name": project_name,
                "parts": parts,
            }
    finally:
        put_connection(conn)
