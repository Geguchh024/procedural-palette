# SPDX-License-Identifier: GPL-3.0-or-later
"""Material assembly: builds a complete node tree for a style.

    Texture Coordinate -> object scale -> Mapping -> Surface (pattern)
        -> Detail (optional) -> Finish (weathering) -> Principled BSDF

Only standard Blender nodes are used, so materials render without the
add-on installed. The controls live as inputs on three named group nodes
("PP Surface", "PP Detail", "PP Finish"), which is what the UI draws.
"""

import colorsys
import random

import bpy

from . import finish, nodekit, patterns, presets

SURFACE_NODE = "PP Surface"
DETAIL_NODE = "PP Detail"
FINISH_NODE = "PP Finish"
MAPPING_NODE = "PP Mapping"
SCALE_NODE = "PP Object Scale"
CONTROL_NODES = (SURFACE_NODE, DETAIL_NODE, FINISH_NODE, MAPPING_NODE)
HIDDEN_INPUTS = {"Vector", "Normal"}

QUALITY = {
    # noise detail multiplier, AO / bevel samples
    "DRAFT": (0.5, 4),
    "BALANCED": (1.0, 8),
    "FINAL": (1.4, 16),
}


def is_pp(mat):
    return mat is not None and getattr(mat, "pp", None) is not None and mat.pp.is_pp


def control_node(mat, name):
    if mat is None or mat.node_tree is None:
        return None
    return mat.node_tree.nodes.get(name)


# ---------------------------------------------------------------------------
# value snapshot / restore
# ---------------------------------------------------------------------------

def _copy_value(v):
    try:
        return tuple(v)
    except TypeError:
        return v


def snapshot(mat):
    data = {}
    for name in CONTROL_NODES:
        node = control_node(mat, name)
        if node is None:
            continue
        vals = {}
        for s in node.inputs:
            if s.is_linked or not hasattr(s, "default_value") or s.name in HIDDEN_INPUTS:
                continue
            vals[s.name] = _copy_value(s.default_value)
        data[name] = vals
    sc = control_node(mat, SCALE_NODE)
    if sc is not None:
        data[SCALE_NODE] = {"scale": tuple(sc.inputs[1].default_value)}
    return data


def restore(mat, data):
    for name, vals in data.items():
        node = control_node(mat, name)
        if node is None:
            continue
        if name == SCALE_NODE:
            node.inputs[1].default_value = vals["scale"]
            continue
        for k, v in vals.items():
            s = node.inputs.get(k)
            if s is None or s.is_linked:
                continue
            try:
                s.default_value = nodekit.to_value(v)
            except (TypeError, ValueError):
                pass


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _ensure_nodes(mat):
    if mat.node_tree is None or not getattr(mat, "use_nodes", True):
        try:
            mat.use_nodes = True
        except AttributeError:
            pass
    return mat.node_tree


def _coords(nb, mode, obj):
    tc = nb.node("ShaderNodeTexCoord")
    geo = nb.node("ShaderNodeNewGeometry")
    if mode == "WORLD":
        vec, normal = geo.outputs["Position"], geo.outputs["Normal"]
    elif mode == "UV":
        vec, normal = tc.outputs["UV"], None
    elif mode == "GENERATED":
        vec, normal = tc.outputs["Generated"], tc.outputs["Normal"]
    else:  # OBJECT, real-world size
        vec, normal = tc.outputs["Object"], tc.outputs["Normal"]
        sc = nb.node("ShaderNodeVectorMath", operation="MULTIPLY")
        sc.name = sc.label = SCALE_NODE
        nb.link(vec, sc.inputs[0])
        sc.inputs[1].default_value = tuple(obj.scale) if obj else (1.0, 1.0, 1.0)
        vec = sc.outputs[0]
    mp = nb.node("ShaderNodeMapping", vector_type="POINT")
    mp.name = mp.label = MAPPING_NODE
    nb.link(vec, mp.inputs["Vector"])
    return mp.outputs["Vector"], normal


_quiet = 0


class quiet:
    """Suppress property update callbacks while settings are changed from code."""

    def __enter__(self):
        global _quiet
        _quiet += 1

    def __exit__(self, *exc):
        global _quiet
        _quiet -= 1


def is_quiet():
    return _quiet > 0


# ---------------------------------------------------------------------------
# upgrading files made with an older add-on version
# ---------------------------------------------------------------------------

_upgrading = False


def _outdated_groups():
    return [ng for ng in bpy.data.node_groups
            if "pp_version" in ng and ng["pp_version"] != nodekit.GROUP_VERSION]


def is_broken(mat):
    """True when a Palette material lost its wiring (e.g. damaged by an old upgrade bug)."""
    if mat.node_tree is None:
        return True
    surf = control_node(mat, SURFACE_NODE)
    fin = control_node(mat, FINISH_NODE)
    if surf is None or fin is None or surf.node_tree is None or fin.node_tree is None:
        return True
    color, vec = fin.inputs.get("Color"), surf.inputs.get("Vector")
    return not (color is not None and color.is_linked and vec is not None and vec.is_linked)


def upgrade_file(force=False):
    """Rebuild outdated shared node groups without breaking existing materials.

    Node groups are shared by every Palette material, so rebuilding one in
    place drops the links of all materials using it. Instead: remember every
    material's values first, rebuild the groups, then rebuild each material
    and put its values back. Materials that are already broken are rebuilt
    from their style preset. Returns (rebuilt, repaired) counts.
    """
    global _upgrading
    if _upgrading:
        return 0, 0
    mats = [m for m in bpy.data.materials if is_pp(m) and m.pp.style in presets.STYLE_BY_ID]
    broken = {m.name for m in mats if is_broken(m)}
    if not force and not _outdated_groups() and not broken:
        return 0, 0
    saved = {}
    for m in mats:
        data = snapshot(m)
        if m.name in broken:
            # group values are gone; keep only what lives in the material itself
            data = {k: v for k, v in data.items() if k in (SCALE_NODE, MAPPING_NODE)}
        saved[m.name] = data
    _upgrading = True
    try:
        for ng in _outdated_groups() if not force else [g for g in bpy.data.node_groups if "pp_version" in g]:
            ng["pp_version"] = -1
        for m in mats:
            build(m, None, None, keep_values=False)
            restore(m, saved.get(m.name, {}))
    finally:
        _upgrading = False
    return len(mats) - len(broken), len(broken)


def build(mat, style_id=None, obj=None, keep_values=True, scene=None):
    """(Re)build the material's node tree from its pp settings.

    style_id: when given and different, switch to that style and load its
              full preset (surface, detail and weathering values).
    keep_values: keep current control values (used for mapping/detail/depth changes).
    """
    pp = mat.pp
    if style_id is not None and style_id != pp.style:
        new_style = presets.STYLE_BY_ID[style_id]
        with quiet():
            pp.style = style_id
            pp.detail = new_style.detail
        keep_values = False
    style = presets.STYLE_BY_ID.get(pp.style)
    if style is None:
        raise ValueError(f"Unknown style {pp.style!r}")
    if not _upgrading and _outdated_groups():
        upgrade_file()
    saved = snapshot(mat) if keep_values else {}

    tree = _ensure_nodes(mat)
    tree.nodes.clear()
    nb = nodekit.NB(tree)
    pat = patterns.PATTERNS[style.pattern]

    vec, normal = _coords(nb, pp.mapping, obj)

    # surface
    if pat.dims == 2 and pp.mapping != "UV":
        group = patterns.build_box_group(style.pattern)
    else:
        group = patterns.build_pattern_group(style.pattern)
    surf = nb.node("ShaderNodeGroup")
    surf.node_tree = group
    surf.name = surf.label = SURFACE_NODE
    nb.link(vec, surf.inputs["Vector"])
    if normal is not None and "Normal" in surf.inputs:
        nb.link(normal, surf.inputs["Normal"])

    color, rough, height = surf.outputs["Color"], surf.outputs["Roughness"], surf.outputs["Height"]

    # detail overlay
    if pp.detail != "NONE":
        det = nb.node("ShaderNodeGroup")
        det.node_tree = finish.build_detail_group(pp.detail)
        det.name = det.label = DETAIL_NODE
        nb.link(vec, det.inputs["Vector"])
        nb.link(color, det.inputs["Color"])
        nb.link(rough, det.inputs["Roughness"])
        nb.link(height, det.inputs["Height"])
        color, rough, height = det.outputs["Color"], det.outputs["Roughness"], det.outputs["Height"]

    # finish
    fin = nb.node("ShaderNodeGroup")
    fin.node_tree = finish.build_finish_group()
    fin.name = fin.label = FINISH_NODE
    nb.link(vec, fin.inputs["Vector"])
    nb.link(color, fin.inputs["Color"])
    nb.link(rough, fin.inputs["Roughness"])
    nb.link(surf.outputs["Metallic"], fin.inputs["Metallic"])
    nb.link(height, fin.inputs["Height"])

    bsdf = nb.node("ShaderNodeBsdfPrincipled")
    nb.link(fin.outputs["Color"], bsdf.inputs["Base Color"])
    nb.link(fin.outputs["Roughness"], bsdf.inputs["Roughness"])
    nb.link(fin.outputs["Metallic"], bsdf.inputs["Metallic"])
    for src, dst in (("Transmission", "Transmission Weight"), ("Coat", "Coat Weight"),
                     ("Sheen", "Sheen Weight"), ("Subsurface", "Subsurface Weight"),
                     ("IOR", "IOR")):
        if dst in bsdf.inputs:
            nb.link(surf.outputs[src], bsdf.inputs[dst])
    if "Sheen Tint" in bsdf.inputs:
        nb.link(fin.outputs["Color"], bsdf.inputs["Sheen Tint"])
    out = nb.node("ShaderNodeOutputMaterial")
    nb.link(bsdf.outputs["BSDF"], out.inputs["Surface"])
    nb.link(fin.outputs["Displacement"], out.inputs["Displacement"])
    if hasattr(mat, "displacement_method"):
        mat.displacement_method = "BOTH" if pp.real_depth else "BUMP"

    # default values: style presets, then restore what the user had
    if not keep_values:
        apply_style_values(mat, style)
    restore(mat, saved)

    _layout_material(tree, surf, fin, bsdf, out)
    apply_quality(scene or bpy.context.scene)
    with quiet():
        pp.is_pp = True
    if obj is not None:
        update_object_scale(mat, obj)
    return mat


def _set_values(node, values):
    if node is None:
        return
    for k, v in values.items():
        s = node.inputs.get(k)
        if s is not None and not s.is_linked:
            s.default_value = nodekit.to_value(v)


def _reset_defaults(node):
    if node is None:
        return
    iface = {i.name: i for i in node.node_tree.interface.items_tree
             if getattr(i, "in_out", None) == "INPUT"}
    for s in node.inputs:
        it = iface.get(s.name)
        if it is not None and hasattr(it, "default_value") and not s.is_linked:
            s.default_value = it.default_value


def apply_style_values(mat, style):
    for name, values in ((SURFACE_NODE, style.surface), (DETAIL_NODE, style.detail_values),
                         (FINISH_NODE, style.finish)):
        node = control_node(mat, name)
        _reset_defaults(node)
        _set_values(node, values)


def _layout_material(tree, surf, fin, bsdf, out):
    nodekit.auto_layout(tree, dx=260, dy=260)
    surf.width = fin.width = 220


def update_object_scale(mat, obj):
    node = control_node(mat, SCALE_NODE)
    if node is not None and obj is not None:
        node.inputs[1].default_value = tuple(abs(s) for s in obj.scale)


def apply_quality(scene):
    q = getattr(getattr(scene, "pp", None), "quality", "BALANCED")
    mult, samples = QUALITY.get(q, QUALITY["BALANCED"])
    for ng in bpy.data.node_groups:
        if "pp_version" not in ng:
            continue
        for n in ng.nodes:
            if "pp_detail" in n:
                s = n.inputs.get("Detail")
                if s is not None and not s.is_linked:
                    s.default_value = min(15.0, n["pp_detail"] * mult)
            if n.bl_idname in {"ShaderNodeBevel", "ShaderNodeAmbientOcclusion"}:
                n.samples = samples


# ---------------------------------------------------------------------------
# convenience operations
# ---------------------------------------------------------------------------

def new_material(style, obj=None, scene=None):
    mat = bpy.data.materials.new(f"PP {style.name}")
    with quiet():
        mat.pp.mapping = "OBJECT"
        mat.pp.detail = style.detail
        mat.pp.style = style.id
    build(mat, None, obj, keep_values=False, scene=scene)
    return mat


def assign(obj, mat):
    if obj.data is None or not hasattr(obj.data, "materials"):
        return
    if len(obj.material_slots) == 0:
        obj.data.materials.append(mat)
    else:
        obj.material_slots[obj.active_material_index].material = mat


def randomize(mat, strength=0.35, seed=None):
    rng = random.Random(seed)
    for name in (SURFACE_NODE, DETAIL_NODE, FINISH_NODE):
        node = control_node(mat, name)
        if node is None:
            continue
        iface = {i.name: i for i in node.node_tree.interface.items_tree
                 if getattr(i, "in_out", None) == "INPUT"}
        for s in node.inputs:
            if s.is_linked or s.name in HIDDEN_INPUTS:
                continue
            if s.name == "Seed":
                s.default_value = rng.uniform(0.0, 1000.0)
                continue
            if name == FINISH_NODE and s.type == "VALUE":
                continue  # keep weathering amounts; only jitter the look
            if s.type == "RGBA":
                r, g, b, a = s.default_value
                h, l, sat = colorsys.rgb_to_hls(max(r, 0), max(g, 0), max(b, 0))
                h = (h + rng.uniform(-0.03, 0.03) * strength) % 1.0
                l = max(0.0, l * (1.0 + rng.uniform(-0.25, 0.25) * strength))
                sat = min(1.0, max(0.0, sat * (1.0 + rng.uniform(-0.3, 0.3) * strength)))
                s.default_value = (*colorsys.hls_to_rgb(h, l, sat), a)
            elif s.type == "VALUE" and s.name not in {"Size"}:
                it = iface.get(s.name)
                lo = getattr(it, "min_value", 0.0)
                hi = getattr(it, "max_value", 1.0)
                v = s.default_value
                span = max(abs(v), 0.05) * strength * 0.5
                s.default_value = min(hi, max(lo, v + rng.uniform(-span, span)))


def look_data(mat):
    pp = mat.pp
    return {
        "style": pp.style,
        "mapping": pp.mapping,
        "detail": pp.detail,
        "real_depth": pp.real_depth,
        "values": snapshot(mat),
    }


def apply_look(mat, look, obj=None):
    with quiet():
        mat.pp.style = look["style"]
        mat.pp.mapping = look.get("mapping", "OBJECT")
        mat.pp.detail = look.get("detail", "NONE")
        mat.pp.real_depth = look.get("real_depth", False)
    build(mat, None, obj, keep_values=False)
    restore(mat, look.get("values", {}))
