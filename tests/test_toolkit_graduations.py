from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_toolkit():
    """Load pure toolkit helpers without requiring a Blender installation."""

    sys.modules.setdefault("bmesh", types.ModuleType("bmesh"))
    sys.modules.setdefault("bpy", types.ModuleType("bpy"))
    mathutils = sys.modules.setdefault("mathutils", types.ModuleType("mathutils"))
    if not hasattr(mathutils, "Vector"):
        mathutils.Vector = object

    path = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "toolkit"
        / "lab_blender_toolkit.py"
    )
    spec = importlib.util.spec_from_file_location("lab_blender_toolkit_for_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_graduation_levels_accept_integer_valued_floats():
    lab = _load_toolkit()
    profile = lab.profile_from_mm([(20, 0), (20, 300)])

    levels = lab.graduation_levels(
        profile,
        maximum_volume_ml=250.0,
        interval_ml=25.0,
    )

    assert [value for value, _ in levels] == list(range(25, 251, 25))


def test_graduation_levels_do_not_overshoot_non_multiple_maximum():
    lab = _load_toolkit()
    profile = lab.profile_from_mm([(20, 0), (20, 300)])

    levels = lab.graduation_levels(
        profile,
        maximum_volume_ml=260,
        interval_ml=25,
    )

    assert [value for value, _ in levels][-1] == 250


def test_graduation_levels_reject_fractional_intervals_clearly():
    lab = _load_toolkit()
    profile = lab.profile_from_mm([(20, 0), (20, 300)])

    with pytest.raises(ValueError, match="whole number"):
        lab.graduation_levels(
            profile,
            maximum_volume_ml=10,
            interval_ml=0.5,
        )
