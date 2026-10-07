# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from . import finish, material, presets


def _simple_names(node):
    try:
        return set(node.node_tree.get("pp_simple", []))
    except (AttributeError, TypeError):
        return set()


def draw_node_inputs(layout, node, advanced, only=None, skip=()):
    if node is None:
        return
    simple = _simple_names(node)
    col = layout.column(align=False)
    for s in node.inputs:
        if s.is_linked or s.hide_value or s.name in material.HIDDEN_INPUTS or s.name in skip:
            continue
        if only is not None and s.name not in only:
            continue
        if only is None and not advanced and s.name not in simple:
            continue
        col.prop(s, "default_value", text=s.name)


def draw_style_picker(layout, context):
    sp = context.scene.pp
    layout.operator("pp.dial", text="Open Dial", icon="PROP_CON")
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop(sp, "family", text="")
    row.operator("pp.search_style", text="", icon="VIEWZOOM")
    row.operator("pp.random_style", text="", icon="FILE_REFRESH")
    col.template_icon_view(sp, "style", show_labels=True, scale=6.0, scale_popup=5.0)
    row = col.row(align=True)
    op = row.operator("pp.apply_style", text="Apply", icon="MATERIAL")
    op.style_id = sp.style
    op = row.operator("pp.apply_style", text="New", icon="ADD")
    op.style_id = sp.style
    op.new_material = True


def draw_material_controls(layout, context):
    sp = context.scene.pp
    obj = context.object
    mat = obj.active_material if obj else None
    if not material.is_pp(mat):
        if obj is not None:
            layout.label(text="Pick a style and press Apply", icon="INFO")
        return
    style = presets.STYLE_BY_ID.get(mat.pp.style)
    adv = sp.advanced

    box = layout.box()
    row = box.row(align=True)
    row.operator("pp.step_style", text="", icon="TRIA_LEFT").step = -1
    row.label(text=f"{presets.FAMILY_LABEL[style.family]}: {style.name}" if style else mat.pp.style)
    row.operator("pp.step_style", text="", icon="TRIA_RIGHT").step = 1
    row = box.row(align=True)
    row.operator("pp.randomize", text="Random", icon="MOD_NOISE")
    row.prop(sp, "random_strength", text="")
    row.operator("pp.reset", text="", icon="LOOP_BACK")
    row = box.row(align=True)
    row.prop(sp, "advanced", text="Advanced", toggle=True, icon="PREFERENCES")
    if mat.users > 1:
        row.operator("pp.make_unique", text="", icon="DUPLICATE")

    surf = material.control_node(mat, material.SURFACE_NODE)
    col = box.column()
    if surf is not None and "Size" in surf.inputs:
        col.prop(surf.inputs["Size"], "default_value", text="Size")
    draw_node_inputs(col, surf, adv, skip={"Size", "Seed", "Box Blend"})
    if adv and surf is not None:
        col.prop(surf.inputs["Seed"], "default_value", text="Seed")

    # mapping
    header, body = layout.panel_prop(sp, "show_mapping")
    header.label(text="Mapping", icon="ORIENTATION_GLOBAL")
    if body:
        body.prop(mat.pp, "mapping", text="")
        mp = material.control_node(mat, material.MAPPING_NODE)
        if mat.pp.mapping == "OBJECT":
            body.operator("pp.sync_scale", icon="OBJECT_ORIGIN")
        if surf is not None and "Box Blend" in surf.inputs:
            body.prop(surf.inputs["Box Blend"], "default_value", text="Box Blend")
        if mp is not None:
            body.prop(mp.inputs["Rotation"], "default_value", text="Rotation")
            if adv:
                body.prop(mp.inputs["Location"], "default_value", text="Offset")
                body.prop(mp.inputs["Scale"], "default_value", text="Stretch")

    # detail overlay
    header, body = layout.panel_prop(sp, "show_detail")
    header.label(text="Custom Detail", icon="SHADERFX")
    if body:
        body.prop(mat.pp, "detail", text="")
        det = material.control_node(mat, material.DETAIL_NODE)
        draw_node_inputs(body, det, adv, skip={"Seed"})

    # weathering
    header, body = layout.panel_prop(sp, "show_finish")
    header.label(text="Weathering & Depth", icon="MOD_DISPLACE")
    if body:
        fin = material.control_node(mat, material.FINISH_NODE)
        row = body.row(align=True)
        row.prop(mat.pp, "real_depth", toggle=True, icon="MOD_DISPLACE")
        if fin is not None:
            for label, names in finish.FINISH_SECTIONS:
                amount = fin.inputs[names[0]]
                c = body.column(align=True)
                c.prop(amount, "default_value", text=label)
                if adv and len(names) > 1 and amount.default_value > 0:
                    sub = c.box().column(align=True)
                    for n in names[1:]:
                        sub.prop(fin.inputs[n], "default_value", text=n)

    layout.prop(sp, "quality", expand=True)


def draw_library(layout, context):
    sp = context.scene.pp
    header, body = layout.panel_prop(sp, "show_library")
    header.label(text="Library", icon="ASSET_MANAGER")
    if body:
        col = body.column(align=True)
        col.operator("pp.library_save", icon="FILE_TICK")
        col.operator("pp.library_apply", icon="IMPORT")
        col.operator("pp.library_remove", icon="TRASH")
        col.separator()
        col.operator("pp.mark_asset", icon="ASSET_MANAGER")
        col.operator("pp.copy_to_selected", icon="PASTEDOWN")
        col.separator()
        col.operator("pp.repair", icon="TOOL_SETTINGS")


def draw_bake(layout, context):
    sp = context.scene.pp
    header, body = layout.panel_prop(sp, "show_bake")
    header.label(text="Bake Textures", icon="RENDER_STILL")
    if body:
        draw_bake_body(body, sp)


def draw_bake_body(body, sp):
    if True:
        body.prop(sp, "bake_resolution")
        grid = body.grid_flow(columns=2, align=True)
        for k in ("color", "roughness", "metallic", "normal", "height", "ao", "orm"):
            grid.prop(sp, f"bake_{k}", toggle=True)
        body.prop(sp, "bake_dir", text="")
        row = body.row(align=True)
        row.prop(sp, "bake_samples")
        row.prop(sp, "bake_margin")
        body.operator("pp.bake", icon="RENDER_STILL")


def draw_all(layout, context):
    draw_style_picker(layout, context)
    layout.separator()
    draw_material_controls(layout, context)
    draw_library(layout, context)
    draw_bake(layout, context)


class PP_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Palette"
    bl_label = "Procedural Palette"

    def draw(self, context):
        self.layout.use_property_split = False
        draw_all(self.layout, context)


class PP_PT_quick(bpy.types.Panel):
    """Floating quick panel, opened with Shift+Alt+M in the 3D Viewport"""
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_label = "Procedural Palette"
    bl_ui_units_x = 16

    def draw(self, context):
        self.layout.label(text="Procedural Palette", icon="MATERIAL")
        draw_all(self.layout, context)


class PP_PT_material(bpy.types.Panel):
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"
    bl_label = "Procedural Palette"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        draw_all(self.layout, context)


class PP_PT_bake_pop(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_label = "Bake Textures"
    bl_ui_units_x = 12

    def draw(self, context):
        self.layout.label(text="Bake Textures", icon="RENDER_STILL")
        draw_bake_body(self.layout, context.scene.pp)


class PP_PT_color_pop(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_label = "Colour"
    bl_ui_units_x = 10

    def draw(self, context):
        sp = context.scene.pp
        obj = context.object
        node = material.control_node(obj.active_material if obj else None, sp.color_node)
        if node is None or sp.color_socket not in node.inputs:
            self.layout.label(text="Nothing to edit")
            return
        sock = node.inputs[sp.color_socket]
        self.layout.label(text=sp.color_socket)
        self.layout.template_color_picker(sock, "default_value", value_slider=True)
        self.layout.prop(sock, "default_value", text="")


classes = (PP_PT_main, PP_PT_quick, PP_PT_material, PP_PT_bake_pop, PP_PT_color_pop)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
