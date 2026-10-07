# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless test suite.

    blender -b --factory-startup --python tests/run_tests.py

Exits with a non-zero status when any test fails.
"""

import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bpy  # noqa: E402

import procedural_palette as pp  # noqa: E402

pp.register()
from procedural_palette import finish, material, nodekit, presets, props  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pp_tests_")
results = []


def test(fn):
    results.append(fn)
    return fn


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def cube(**kw):
    bpy.ops.mesh.primitive_cube_add(**kw)
    return bpy.context.object


# ---------------------------------------------------------------------------

@test
def all_styles_build():
    reset()
    obj = cube()
    for st in presets.STYLES:
        m = material.new_material(st, obj)
        assert not material.is_broken(m), st.id
        for node_name, values in ((material.SURFACE_NODE, st.surface), (material.FINISH_NODE, st.finish),
                                  (material.DETAIL_NODE, st.detail_values)):
            node = material.control_node(m, node_name)
            for key in values:
                assert node is not None and key in node.inputs, f"{st.id}: {node_name}.{key} missing"


@test
def family_icons_exist():
    icons = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys()
    bad = [icon for _, _, icon in presets.FAMILIES if icon not in icons]
    assert not bad, bad


@test
def rebuild_keeps_values():
    reset()
    obj = cube()
    m = material.new_material(presets.STYLE_BY_ID["BRICK/Old Red Brick"], obj)
    material.assign(obj, m)
    material.control_node(m, material.FINISH_NODE).inputs["Moss"].default_value = 0.77
    for mode, _, _, _, _ in props.MAPPING_ITEMS:
        m.pp.mapping = mode
    for kind, _, _ in finish.DETAILS:
        m.pp.detail = kind
    assert abs(material.control_node(m, material.FINISH_NODE).inputs["Moss"].default_value - 0.77) < 1e-6


@test
def style_switch_and_real_depth():
    reset()
    obj = cube()
    m = material.new_material(presets.STYLE_BY_ID["BRICK/Old Red Brick"], obj)
    material.assign(obj, m)
    material.build(m, "WOOD/Oak", obj)
    assert m.pp.style == "WOOD/Oak" and not material.is_broken(m)
    m.pp.real_depth = True
    assert m.displacement_method == "BOTH" and props.DEPTH_MOD in obj.modifiers
    m.pp.real_depth = False
    assert props.DEPTH_MOD not in obj.modifiers


@test
def look_roundtrip():
    reset()
    obj = cube()
    m = material.new_material(presets.STYLE_BY_ID["METAL/Painted Metal"], obj)
    material.randomize(m, 0.5, 42)
    look = material.look_data(m)
    m2 = bpy.data.materials.new("copy")
    material.apply_look(m2, look, obj)
    assert material.snapshot(m2)[material.FINISH_NODE] == look["values"][material.FINISH_NODE]


def _old_version_material():
    reset()
    obj = cube()
    obj.scale = (2, 2, 2)
    m = material.new_material(presets.STYLE_BY_ID["BRICK/Mossy Garden Wall"], obj)
    material.assign(obj, m)
    material.control_node(m, material.SURFACE_NODE).inputs["Brick Length"].default_value = 0.3
    for ng in bpy.data.node_groups:
        if "pp_version" in ng:
            ng["pp_version"] = nodekit.GROUP_VERSION - 1
    return obj, m


def _check_kept(m):
    assert not material.is_broken(m)
    assert abs(material.control_node(m, material.SURFACE_NODE).inputs["Brick Length"].default_value - 0.3) < 1e-6
    assert abs(material.control_node(m, material.FINISH_NODE).inputs["Moss"].default_value - 0.55) < 1e-6
    assert tuple(material.control_node(m, material.SCALE_NODE).inputs[1].default_value) == (2.0, 2.0, 2.0)


@test
def upgrade_keeps_existing_materials():
    _, old = _old_version_material()
    material.new_material(presets.STYLE_BY_ID["BRICK/London Stock"], cube(location=(4, 0, 0)))
    _check_kept(old)


@test
def upgrade_on_file_load():
    _old_version_material()
    path = os.path.join(TMP, "old_version.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    _check_kept(bpy.data.materials["PP Mossy Garden Wall"])
    assert not material._outdated_groups()


@test
def repair_broken_material():
    reset()
    obj = cube()
    m = material.new_material(presets.STYLE_BY_ID["BRICK/Mossy Garden Wall"], obj)
    material.control_node(m, material.FINISH_NODE).node_tree.interface.clear()
    assert material.is_broken(m)
    assert bpy.ops.pp.repair() == {"FINISHED"}
    assert not material.is_broken(m)


@test
def bake_two_materials_without_uvs():
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add()
    obj = bpy.context.object
    obj.data.uv_layers.remove(obj.data.uv_layers[0])
    obj.data.materials.append(material.new_material(presets.STYLE_BY_ID["BRICK/Old Red Brick"], obj))
    obj.data.materials.append(material.new_material(presets.STYLE_BY_ID["METAL/Rusted Steel"], obj))
    for i, poly in enumerate(obj.data.polygons):
        poly.material_index = i % 2
    sp = bpy.context.scene.pp
    sp.bake_resolution = "512"
    sp.bake_dir = os.path.join(TMP, "bake")
    sp.bake_samples = 4
    sp.bake_height = sp.bake_ao = sp.bake_orm = True
    assert bpy.ops.pp.bake() == {"FINISHED"}
    files = sorted(os.listdir(sp.bake_dir))
    assert len(files) == 7, files
    assert "PP Bake" in obj.data.uv_layers
    for m in obj.data.materials:
        assert not any(n.bl_idname == "ShaderNodeTexImage" for n in m.node_tree.nodes), "bake left image nodes"
        assert not material.is_broken(m)


# ---------------------------------------------------------------------------

def main():
    failed = 0
    for fn in results:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(results) - failed}/{len(results)} passed")
    sys.exit(1 if failed else 0)


main()
