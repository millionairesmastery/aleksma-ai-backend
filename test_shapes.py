"""Unit tests for shapes library and validators."""

import pytest
from shapes import (
    round_tube, rect_tube, flat_plate, plate_with_bolt_holes,
    l_bracket, gusset, mounting_boss,
)
from validators import validate_script, has_blocking_errors


# ── round_tube ───────────────────────────────────────────────────────────────

def test_round_tube_default():
    wp = round_tube(100)
    assert wp is not None
    bb = wp.val().BoundingBox()
    assert abs(bb.zmax - bb.zmin - 100) < 0.5

def test_round_tube_wall_too_thick():
    with pytest.raises(ValueError):
        round_tube(100, od=10, wall=6)

def test_round_tube_negative_length():
    with pytest.raises(ValueError):
        round_tube(-5)

def test_round_tube_axes():
    for axis in ["X", "Y", "Z"]:
        wp = round_tube(100, axis=axis)
        assert wp is not None
        bb = wp.val().BoundingBox()
        if axis == "Z":
            assert abs(bb.zmax - bb.zmin - 100) < 0.5
        elif axis == "X":
            assert abs(bb.xmax - bb.xmin - 100) < 0.5
        elif axis == "Y":
            assert abs(bb.ymax - bb.ymin - 100) < 0.5

def test_round_tube_bad_axis():
    with pytest.raises(ValueError):
        round_tube(100, axis="W")


# ── rect_tube ────────────────────────────────────────────────────────────────

def test_rect_tube_default():
    wp = rect_tube(80)
    assert wp is not None
    bb = wp.val().BoundingBox()
    assert abs(bb.zmax - bb.zmin - 80) < 0.5

def test_rect_tube_wall_too_thick():
    with pytest.raises(ValueError):
        rect_tube(100, width=10, height=10, wall=6)

def test_rect_tube_axes():
    for axis in ["X", "Y", "Z"]:
        wp = rect_tube(100, axis=axis)
        assert wp is not None


# ── flat_plate ───────────────────────────────────────────────────────────────

def test_flat_plate_default():
    wp = flat_plate(100, 60)
    assert wp is not None
    bb = wp.val().BoundingBox()
    assert abs(bb.xmax - bb.xmin - 100) < 0.5
    assert abs(bb.ymax - bb.ymin - 60) < 0.5

def test_flat_plate_negative():
    with pytest.raises(ValueError):
        flat_plate(-10, 20)


# ── plate_with_bolt_holes ────────────────────────────────────────────────────

def test_plate_with_bolt_holes_default():
    wp = plate_with_bolt_holes(100, 80)
    assert wp is not None

def test_plate_with_bolt_holes_margin_too_large():
    with pytest.raises(ValueError):
        plate_with_bolt_holes(40, 40, margin=25)


# ── l_bracket ────────────────────────────────────────────────────────────────

def test_l_bracket_default():
    wp = l_bracket(50, 30, 80)
    assert wp is not None
    bb = wp.val().BoundingBox()
    assert bb.xmax - bb.xmin > 0

def test_l_bracket_negative():
    with pytest.raises(ValueError):
        l_bracket(-10, 30, 80)


# ── gusset ───────────────────────────────────────────────────────────────────

def test_gusset_default():
    wp = gusset(40, 30)
    assert wp is not None
    bb = wp.val().BoundingBox()
    assert abs(bb.xmax - bb.xmin - 40) < 0.5

def test_gusset_negative():
    with pytest.raises(ValueError):
        gusset(-5, 10)


# ── mounting_boss ────────────────────────────────────────────────────────────

def test_mounting_boss_default():
    wp = mounting_boss(20)
    assert wp is not None
    bb = wp.val().BoundingBox()
    assert abs(bb.zmax - bb.zmin - 20) < 0.5

def test_mounting_boss_bore_too_large():
    with pytest.raises(ValueError):
        mounting_boss(20, od=10, bore_d=12)


# ── validators ───────────────────────────────────────────────────────────────

def test_validate_missing_result():
    warns = validate_script("x = 1")
    assert has_blocking_errors(warns)
    assert any("result" in w.message for w in warns)

def test_validate_forbidden_import():
    warns = validate_script("import os\nresult = 1")
    assert has_blocking_errors(warns)
    assert any("os" in w.message for w in warns)

def test_validate_large_dimension():
    warns = validate_script("result = cq.Workplane().box(50000, 10, 10)")
    assert any("Large dimension" in w.message for w in warns)

def test_validate_sweep_warning():
    warns = validate_script("result = wp.sweep(path)")
    assert any("sweep" in w.message for w in warns)

def test_validate_clean_script():
    warns = validate_script('import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 10)')
    assert not has_blocking_errors(warns)
