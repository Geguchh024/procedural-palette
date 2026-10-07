# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import (BoolProperty, EnumProperty, IntProperty, PointerProperty,
                       StringProperty)

from . import finish, material, presets, thumbs

MAPPING_ITEMS = [
    ("OBJECT", "Real Size", "Object space scaled by the object's scale, so patterns keep real-world size", "OBJECT_DATA", 0),
    ("GENERATED", "Stretch with Object", "Pattern stretches with the object's bounding box", "FULLSCREEN_ENTER", 1),
    ("WORLD", "World Space", "Pattern is fixed in the world; objects slide through it", "WORLD", 2),
    ("UV", "UV Map", "Use the object's active UV map (planar patterns are not box-projected)", "UV", 3),
]

QUALITY_ITEMS = [
    ("DRAFT", "Draft", "Fewer noise octaves and samples, fastest viewport"),
    ("BALANCED", "Balanced", "Good default"),
    ("FINAL", "Final", "Most detail, for final renders"),
]

RESOLUTION_ITEMS = [(str(r), f"{r // 1024}K" if r >= 1024 else str(r), "") for r in
                    (512, 1024, 2048, 4096, 8192)]

# Blender needs Python to keep dynamic enum strings alive
_style_items_cache = {}


def family_items(self, context):
    return [(k, n, "", icon, i) for i, (k, n, icon) in enumerate(presets.FAMILIES)]


def style_items(self, context):
    fam = getattr(self, "family", "BRICK")
    items = [(s.id, s.name, f"{presets.FAMILY_LABEL[s.family]}: {s.name}",
              thumbs.icon_id(thumbs.style_key(s.id)), i)
             for i, s in enumerate(presets.styles_in(fam))]
    _style_items_cache[fam] = items
    return items


_all_style_items = None


def all_style_items(self, context):
    global _all_style_items
    if _all_style_items is None:
        _all_style_items = [(s.id, f"{presets.FAMILY_LABEL[s.family]}: {s.name}", "")
                            for s in presets.STYLES]
    return _all_style_items


def _rebuild(self, context):
    if material.is_quiet():
        return
    mat = self.id_data
    if not self.is_pp:
        return
    obj = context.object if context else None
    material.build(mat, None, obj, keep_values=True)


def _real_depth_changed(self, context):
    if material.is_quiet() or not self.is_pp:
        return
    mat = self.id_data
    if hasattr(mat, "displacement_method"):
        mat.displacement_method = "BOTH" if self.real_depth else "BUMP"
    obj = context.object if context else None
    if obj is not None and obj.type == "MESH":
        set_depth_modifier(obj, self.real_depth)


DEPTH_MOD = "PP Real Depth"


def set_depth_modifier(obj, enable):
    mod = obj.modifiers.get(DEPTH_MOD)
    if enable and mod is None:
        mod = obj.modifiers.new(DEPTH_MOD, "SUBSURF")
        mod.subdivision_type = "SIMPLE"
        mod.levels = 3
        mod.render_levels = 5
        # Cycles adaptive subdivision where this Blender version exposes it
        for owner, attr in ((mod, "use_adaptive_subdivision"),
                            (getattr(obj, "cycles", None), "use_adaptive_subdivision")):
            if owner is not None and hasattr(owner, attr):
                try:
                    setattr(owner, attr, True)
                except (AttributeError, TypeError):
                    pass
    elif not enable and mod is not None:
        obj.modifiers.remove(mod)


def _quality_changed(self, context):
    material.apply_quality(context.scene)


class PP_MaterialSettings(bpy.types.PropertyGroup):
    is_pp: BoolProperty(default=False)
    style: StringProperty(default="")
    mapping: EnumProperty(name="Mapping", items=MAPPING_ITEMS, default="OBJECT", update=_rebuild)
    detail: EnumProperty(name="Detail", items=finish.DETAILS, default="NONE", update=_rebuild,
                         description="Extra detail layer added on top of the surface")
    real_depth: BoolProperty(name="Real Depth", default=False, update=_real_depth_changed,
                             description="Use true displacement instead of bump "
                                         "(adds a subdivision modifier to the active object)")


class PP_SceneSettings(bpy.types.PropertyGroup):
    family: EnumProperty(name="Family", items=family_items)
    style: EnumProperty(name="Style", items=style_items)
    search: EnumProperty(name="Style", items=all_style_items)
    advanced: BoolProperty(name="Advanced", default=False,
                           description="Show every parameter instead of the essentials")
    quality: EnumProperty(name="Quality", items=QUALITY_ITEMS, default="BALANCED",
                          update=_quality_changed)
    random_strength: bpy.props.FloatProperty(name="Strength", default=0.35, min=0.0, max=1.0,
                                             subtype="FACTOR")
    color_node: StringProperty()
    color_socket: StringProperty()
    show_mapping: BoolProperty(default=False)
    show_detail: BoolProperty(default=False)
    show_finish: BoolProperty(default=True)
    show_bake: BoolProperty(default=False)
    show_library: BoolProperty(default=False)
    # baking
    bake_resolution: EnumProperty(name="Resolution", items=RESOLUTION_ITEMS, default="2048")
    bake_color: BoolProperty(name="Color", default=True)
    bake_roughness: BoolProperty(name="Roughness", default=True)
    bake_metallic: BoolProperty(name="Metallic", default=True)
    bake_normal: BoolProperty(name="Normal", default=True)
    bake_height: BoolProperty(name="Height", default=False)
    bake_ao: BoolProperty(name="AO", default=False)
    bake_orm: BoolProperty(name="ORM Pack", default=False,
                           description="Pack AO, Roughness and Metallic into R, G and B")
    bake_dir: StringProperty(name="Folder", default="//textures/", subtype="DIR_PATH")
    bake_samples: IntProperty(name="AO Samples", default=32, min=1, max=1024)
    bake_margin: IntProperty(name="Margin", default=16, min=0, max=64)


classes = (PP_MaterialSettings, PP_SceneSettings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Material.pp = PointerProperty(type=PP_MaterialSettings)
    bpy.types.Scene.pp = PointerProperty(type=PP_SceneSettings)


def unregister():
    del bpy.types.Scene.pp
    del bpy.types.Material.pp
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
