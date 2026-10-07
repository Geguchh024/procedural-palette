# SPDX-License-Identifier: GPL-3.0-or-later
import os
import random

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import bake, library, material, presets, props


def _active_mat(context):
    obj = context.object
    return obj.active_material if obj else None


def _mesh_targets(context):
    objs = [o for o in context.selected_objects if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}]
    if context.object and context.object not in objs and context.object.type == "MESH":
        objs.append(context.object)
    return objs


class PP_OT_apply_style(bpy.types.Operator):
    """Apply a material style to the selected objects"""
    bl_idname = "pp.apply_style"
    bl_label = "Apply Style"
    bl_options = {"REGISTER", "UNDO"}

    style_id: StringProperty(options={"SKIP_SAVE"})
    new_material: BoolProperty(
        name="New Material", default=False, options={"SKIP_SAVE"},
        description="Always create a new material instead of changing the active Palette material")
    active_only: BoolProperty(
        name="Active Object Only", default=False, options={"SKIP_SAVE"},
        description="Only assign to the active object, not every selected object")

    def execute(self, context):
        sid = self.style_id or context.scene.pp.style
        style = presets.STYLE_BY_ID.get(sid)
        if style is None:
            self.report({"ERROR"}, f"Unknown style {sid}")
            return {"CANCELLED"}
        mat = _active_mat(context)
        obj = context.object
        if material.is_pp(mat) and not self.new_material:
            material.build(mat, style.id, obj)
            mat.name = f"PP {style.name}"
            return {"FINISHED"}
        targets = [obj] if self.active_only and obj is not None else _mesh_targets(context)
        if not targets:
            self.report({"WARNING"}, "Select an object to apply a material to")
            return {"CANCELLED"}
        mat = material.new_material(style, obj or targets[0], context.scene)
        for o in targets:
            material.assign(o, mat)
        return {"FINISHED"}


class PP_OT_search_style(bpy.types.Operator):
    """Search all styles by name"""
    bl_idname = "pp.search_style"
    bl_label = "Search Styles"
    bl_property = "style"
    bl_options = {"REGISTER", "UNDO"}

    style: EnumProperty(items=props.all_style_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        style = presets.STYLE_BY_ID[self.style]
        sp = context.scene.pp
        sp.family = style.family
        sp.style = style.id
        return bpy.ops.pp.apply_style(style_id=style.id)


class PP_OT_step_style(bpy.types.Operator):
    """Switch to the previous or next style in the same family"""
    bl_idname = "pp.step_style"
    bl_label = "Next Style"
    bl_options = {"REGISTER", "UNDO"}

    step: IntProperty(default=1)

    @classmethod
    def poll(cls, context):
        return material.is_pp(_active_mat(context))

    def execute(self, context):
        mat = _active_mat(context)
        cur = presets.STYLE_BY_ID.get(mat.pp.style)
        fam = presets.styles_in(cur.family)
        i = (fam.index(cur) + self.step) % len(fam)
        style = fam[i]
        material.build(mat, style.id, context.object)
        mat.name = f"PP {style.name}"
        context.scene.pp.family = style.family
        context.scene.pp.style = style.id
        return {"FINISHED"}


class PP_OT_randomize(bpy.types.Operator):
    """Randomize seed and nudge colours and parameters for a fresh variation"""
    bl_idname = "pp.randomize"
    bl_label = "Random Variation"
    bl_options = {"REGISTER", "UNDO"}

    seed: IntProperty(name="Seed", default=0, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return material.is_pp(_active_mat(context))

    def execute(self, context):
        mat = _active_mat(context)
        seed = self.seed or random.randrange(1, 1 << 30)
        material.randomize(mat, context.scene.pp.random_strength, seed)
        return {"FINISHED"}


class PP_OT_random_style(bpy.types.Operator):
    """Apply a random style from the current family"""
    bl_idname = "pp.random_style"
    bl_label = "Random Style"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sp = context.scene.pp
        style = random.choice(presets.styles_in(sp.family))
        sp.style = style.id
        return bpy.ops.pp.apply_style(style_id=style.id)


class PP_OT_reset(bpy.types.Operator):
    """Reset all parameters to the style's preset values"""
    bl_idname = "pp.reset"
    bl_label = "Reset to Preset"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return material.is_pp(_active_mat(context))

    def execute(self, context):
        mat = _active_mat(context)
        style = presets.STYLE_BY_ID[mat.pp.style]
        with material.quiet():
            mat.pp.detail = style.detail
        material.build(mat, None, context.object, keep_values=False)
        return {"FINISHED"}


class PP_OT_rebuild(bpy.types.Operator):
    """Regenerate node groups and the material tree (keeps your values)"""
    bl_idname = "pp.rebuild"
    bl_label = "Rebuild Nodes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return material.is_pp(_active_mat(context))

    def execute(self, context):
        for ng in list(bpy.data.node_groups):
            if "pp_version" in ng:
                ng["pp_version"] = -1
        material.build(_active_mat(context), None, context.object, keep_values=True)
        return {"FINISHED"}


class PP_OT_repair(bpy.types.Operator):
    """Upgrade and repair every Palette material in this file (rebuilds shared node groups safely)"""
    bl_idname = "pp.repair"
    bl_label = "Repair Palette Materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rebuilt, repaired = material.upgrade_file(force=True)
        msg = f"Rebuilt {rebuilt} material(s)"
        if repaired:
            msg += f", repaired {repaired} broken one(s) from their style preset"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class PP_OT_sync_scale(bpy.types.Operator):
    """Match the material's real-size mapping to the active object's scale"""
    bl_idname = "pp.sync_scale"
    bl_label = "Sync Object Scale"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return material.is_pp(_active_mat(context))

    def execute(self, context):
        material.update_object_scale(_active_mat(context), context.object)
        return {"FINISHED"}


class PP_OT_copy_to_selected(bpy.types.Operator):
    """Assign the active object's material to all selected objects"""
    bl_idname = "pp.copy_to_selected"
    bl_label = "Copy to Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_mat(context) is not None

    def execute(self, context):
        mat = _active_mat(context)
        n = 0
        for o in context.selected_objects:
            if o is not context.object and hasattr(o.data, "materials"):
                material.assign(o, mat)
                n += 1
        self.report({"INFO"}, f"Assigned {mat.name} to {n} object(s)")
        return {"FINISHED"}


class PP_OT_make_unique(bpy.types.Operator):
    """Make a single-user copy of the active material so edits don't affect other objects"""
    bl_idname = "pp.make_unique"
    bl_label = "Make Unique"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        m = _active_mat(context)
        return m is not None and m.users > 1

    def execute(self, context):
        obj = context.object
        material.assign(obj, _active_mat(context).copy())
        return {"FINISHED"}


class PP_OT_library_save(bpy.types.Operator):
    """Save the current look to your personal library"""
    bl_idname = "pp.library_save"
    bl_label = "Save Look"

    name: StringProperty(name="Name")

    @classmethod
    def poll(cls, context):
        return material.is_pp(_active_mat(context))

    def invoke(self, context, event):
        self.name = _active_mat(context).name.removeprefix("PP ")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not self.name.strip():
            self.report({"ERROR"}, "Name required")
            return {"CANCELLED"}
        library.add(self.name.strip(), material.look_data(_active_mat(context)))
        self.report({"INFO"}, f"Saved '{self.name}' to library")
        return {"FINISHED"}


class PP_OT_library_apply(bpy.types.Operator):
    """Apply a saved look from your library"""
    bl_idname = "pp.library_apply"
    bl_label = "Apply Saved Look"
    bl_property = "name"
    bl_options = {"REGISTER", "UNDO"}

    name: EnumProperty(items=library.enum_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        look = library.load().get(self.name)
        if look is None or look.get("style") not in presets.STYLE_BY_ID:
            self.report({"ERROR"}, "Look not found")
            return {"CANCELLED"}
        obj = context.object
        if obj is None:
            return {"CANCELLED"}
        mat = bpy.data.materials.new(f"PP {self.name}")
        material.apply_look(mat, look, obj)
        for o in _mesh_targets(context):
            material.assign(o, mat)
        return {"FINISHED"}


class PP_OT_library_remove(bpy.types.Operator):
    """Remove a saved look from your library"""
    bl_idname = "pp.library_remove"
    bl_label = "Remove Saved Look"
    bl_property = "name"

    name: EnumProperty(items=library.enum_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        library.remove(self.name)
        return {"FINISHED"}


class PP_OT_mark_asset(bpy.types.Operator):
    """Mark the material as an asset so it shows in the Asset Browser and can be shared"""
    bl_idname = "pp.mark_asset"
    bl_label = "Mark as Asset"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_mat(context) is not None

    def execute(self, context):
        mat = _active_mat(context)
        mat.asset_mark()
        try:
            mat.asset_generate_preview()
        except (AttributeError, RuntimeError):
            pass
        self.report({"INFO"}, f"{mat.name} marked as asset")
        return {"FINISHED"}


class PP_OT_bake(bpy.types.Operator):
    """Bake the active object's materials to image textures (uses Cycles)"""
    bl_idname = "pp.bake"
    bl_label = "Bake Textures"

    @classmethod
    def poll(cls, context):
        o = context.object
        return o is not None and o.type == "MESH" and context.mode == "OBJECT"

    def execute(self, context):
        try:
            paths, created_uv = bake.bake_object(context, context.object, context.scene.pp,
                                                 report=lambda m: None)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        msg = f"Baked {len(paths)} map(s) to {os.path.dirname(paths[0]) if paths else ''}"
        if created_uv:
            msg += " (added UV map 'PP Bake')"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


classes = (
    PP_OT_apply_style, PP_OT_search_style, PP_OT_step_style, PP_OT_randomize,
    PP_OT_random_style, PP_OT_reset, PP_OT_rebuild, PP_OT_repair, PP_OT_sync_scale,
    PP_OT_copy_to_selected, PP_OT_make_unique, PP_OT_library_save,
    PP_OT_library_apply, PP_OT_library_remove, PP_OT_mark_asset, PP_OT_bake,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
