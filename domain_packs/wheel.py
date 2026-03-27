from __future__ import annotations

WHEEL_PACK = {
    "object_type": "wheel",
    "aliases": ["wheel", "rim", "alloy wheel", "alloy rim"],
    "required_components": [],
    "forbidden_components": [],
    "default_dimensions": {
        "rim_diameter_in": 17,
        "rim_width_mm": 215,
        "spoke_count": 5,
        "pcd_mm": 114.3,
        "center_bore_mm": 66.1,
    },
    "generation_notes": [
        # ── Coordinate system ──
        "COORDINATE SYSTEM: Wheel axis = Z. Hub face in XY plane. Barrel extrudes along Z.",
        "ALL geometry (barrel, hub, spokes) must share the SAME coordinate system — Z-axis aligned.",
        "Barrel: build cross-section profile in XZ plane with (radius, z_pos) coordinates.",
        "REVOLVE AXIS: In XZ workplane, local Y = global Z. Use .revolve(360, (0,0,0), (0,1,0)) to revolve around global Z.",
        "Hub: cq.Workplane('XY').cylinder(hub_thickness, hub_radius) — centered at origin.",
        "Spokes: build in XY plane, extrude along Z. Rotate around Z for angular positioning.",
        # ── Structure ──
        "A wheel rim is a multi-part assembly: parts = {} with 'Hub', 'Barrel', 'Spoke_1'...'Spoke_N'.",
        "Each part is a separate selectable cq.Workplane entry in the parts dict.",
        "Hub includes center bore cut and bolt hole cuts — NO separate visualization cylinders.",
        "Barrel is ONE part built by revolve — do NOT add separate 'Drop Center', 'Lip', or 'Well' parts.",
        # ── Barrel profile ──
        "Use REVOLVE for the barrel cross-section profile (drop-center with bead seats and lips).",
        "Build the barrel profile with .moveTo().lineTo()...close().revolve(360, (0,0,0), (0,1,0)).",
        "Profile coordinates: (radius, z_position) — radius is distance from Z-axis, z is axial position.",
        # ── Spokes ──
        "Spokes: use LOFT for sculpted shape. Build 3 cross-sections (hub, mid, tip) in YZ plane at X offsets.",
        "Loft pattern: s1 = Workplane('YZ').workplane(offset=inner_r).rect(w1,h1); s2 = s1.workplane(offset=d).rect(w2,h2); s1.add(s2).loft()",
        "Spoke hub end: wider and thicker. Spoke tip: wider but thinner. This creates a sculpted taper.",
        "Then translate to hub Z and rotate around Z for angular position.",
        "Spoke outer end MUST stop at drop_center_r - barrel_wall (inner wall of drop center). NOT at rim_radius.",
        "Spokes must NOT cover bolt holes — offset spoke angles by half-bolt-spacing to sit BETWEEN bolts.",
        "Center bore must be fully clear — no spoke geometry inside center_bore_r.",
        # ── Forbidden patterns ──
        "NEVER add 'visualization' or 'highlight' parts (solid cylinders for bolt holes, bore viz, etc.).",
        "NEVER add duplicate geometry parts (drop center ring, well ring, lip parts).",
        "Only real physical parts: Hub, Barrel, Spoke_1...Spoke_N.",
        # ── Fillets ──
        "Add fillets at spoke-to-hub and spoke-to-barrel transitions where possible.",
        "Keep fillet radius < smallest adjacent dimension / 3 to avoid geometry failures.",
    ],
    "validation_notes": [
        "Accept multi-part with Hub + Barrel + Spokes.",
        "Reject tire-like rubber outer geometry for pure rim requests.",
        "Reject visualization-only parts (bolt hole cylinders, bore cylinders, well rings).",
        "Reject scripts that use different axis orientations for barrel vs hub vs spokes.",
    ],
}
