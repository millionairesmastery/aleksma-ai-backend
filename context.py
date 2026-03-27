"""Build assembly context strings for the AI system prompt."""

from __future__ import annotations

from db import get_connection, put_connection


def build_assembly_context(assembly_id: int, include_scripts: bool = True) -> str:
    """
    Query all parts in an assembly and format them as a context block
    for the AI system prompt.

    include_scripts=True (default): includes the full CadQuery script for each part
    so the AI can extract exact dimensions and fit new parts precisely.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name FROM assemblies WHERE id = %s", (assembly_id,)
            )
            row = cur.fetchone()
            if not row:
                return ""
            assembly_name = row[1]

            cur.execute(
                """SELECT name, material,
                          position_x, position_y, position_z,
                          bbox_min_x, bbox_min_y, bbox_min_z,
                          bbox_max_x, bbox_max_y, bbox_max_z,
                          cadquery_script, part_type
                   FROM parts WHERE assembly_id = %s ORDER BY id""",
                (assembly_id,),
            )
            parts = cur.fetchall()
    finally:
        put_connection(conn)

    if not parts:
        return (
            f"CURRENT ASSEMBLY: {assembly_name} (empty — no parts yet)\n"
            f"COORDINATE SYSTEM: All dimensions in millimeters."
        )

    lines = [
        f"CURRENT ASSEMBLY: {assembly_name}",
        f"EXISTING PARTS ({len(parts)}):",
        "",
        "IMPORTANT: Read each part's script below to extract EXACT dimensions before generating.",
        "When fitting a new part to an existing one, use the variable values from the existing script,",
        "NOT guesses. E.g. if the Rim script sets rim_r = 228.6, your tire MUST use that exact value.",
        "For IMPORTED parts, use the IMPORT_WIDTH_MM / IMPORT_DEPTH_MM / IMPORT_HEIGHT_MM values",
        "shown in their geometry summary to size and position mating parts correctly.",
        "",
    ]

    for i, p in enumerate(parts, 1):
        name, material = p[0], p[1]
        px, py, pz = p[2], p[3], p[4]
        bmin_x, bmin_y, bmin_z = p[5], p[6], p[7]
        bmax_x, bmax_y, bmax_z = p[8], p[9], p[10]
        script = p[11] or ""
        part_type = p[12] or ""

        is_imported = part_type == "imported"
        label = f'[IMPORTED] "{name}"' if is_imported else f'"{name}"'
        lines.append(f'{i}. {label}')

        if all(v is not None for v in (bmin_x, bmin_y, bmin_z, bmax_x, bmax_y, bmax_z)):
            dx = bmax_x - bmin_x
            dy = bmax_y - bmin_y
            dz = bmax_z - bmin_z
            lines.append(
                f"   Bbox: ({bmin_x:.1f}, {bmin_y:.1f}, {bmin_z:.1f}) "
                f"to ({bmax_x:.1f}, {bmax_y:.1f}, {bmax_z:.1f})  |  "
                f"Size: {dx:.1f} x {dy:.1f} x {dz:.1f} mm"
            )
        lines.append(f"   Position offset in assembly: ({px:.1f}, {py:.1f}, {pz:.1f})")
        if material:
            lines.append(f"   Material: {material}")

        if is_imported:
            # For imported parts show the geometry summary (not a runnable script)
            if script.strip():
                lines.append(f"   Geometry Summary (extracted at import):")
                lines.append(f"   ```")
                for sl in script.strip().splitlines():
                    lines.append(f"   {sl}")
                lines.append(f"   ```")
        elif include_scripts and script.strip():
            # Include the script so AI can extract exact dimensions
            # Truncate very long scripts to first 120 lines to avoid token overload
            script_lines = script.strip().splitlines()
            MAX_LINES = 120
            if len(script_lines) > MAX_LINES:
                shown = script_lines[:MAX_LINES]
                truncated = len(script_lines) - MAX_LINES
                shown.append(f"   # ... ({truncated} more lines truncated)")
                script_text = "\n".join(shown)
            else:
                script_text = "\n".join(script_lines)
            lines.append(f"   CadQuery Script:")
            lines.append(f"   ```python")
            for sl in script_text.splitlines():
                lines.append(f"   {sl}")
            lines.append(f"   ```")

        lines.append("")

    lines.append("COORDINATE SYSTEM: All dimensions in millimeters.")
    lines.append("FITTING RULE: New parts MUST use exact dimension values from existing scripts above.")
    return "\n".join(lines)
