# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake procedural materials to image textures with Cycles.

Colour-like passes (base colour, roughness, metallic, height) are baked by
temporarily routing the value into an Emission shader. Normal and AO use
Cycles' native bake types. Works with several materials on one object: all
of them bake into the same image set.
"""

import math
import os

import bpy

from . import material

# key: (label, cycles bake type, principled input or special source, colour space, float)
PASSES = {
    "color": ("BaseColor", "EMIT", "Base Color", "sRGB", False),
    "roughness": ("Roughness", "EMIT", "Roughness", "Non-Color", False),
    "metallic": ("Metallic", "EMIT", "Metallic", "Non-Color", False),
    "height": ("Height", "EMIT", "@height", "Non-Color", True),
    "normal": ("Normal", "NORMAL", None, "Non-Color", False),
    "ao": ("AO", "AO", None, "Non-Color", False),
}
ORDER = ["color", "roughness", "metallic", "height", "normal", "ao"]


def _output_node(tree):
    outs = [n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
    for n in outs:
        if n.is_active_output:
            return n
    return outs[0] if outs else None


def _principled(tree, out):
    if out is None or not out.inputs["Surface"].is_linked:
        return None
    n = out.inputs["Surface"].links[0].from_node
    return n if n.bl_idname == "ShaderNodeBsdfPrincipled" else next(
        (x for x in tree.nodes if x.bl_idname == "ShaderNodeBsdfPrincipled"), None)


class _EmitRewire:
    """Temporarily replaces a material's surface with an emission of one value."""

    def __init__(self, mat, source):
        self.mat = mat
        self.tree = mat.node_tree
        self.out = _output_node(self.tree)
        self.prev = None
        self.added = []
        if self.out is None:
            return
        surf = self.out.inputs["Surface"]
        self.prev = surf.links[0].from_socket if surf.is_linked else None
        em = self.tree.nodes.new("ShaderNodeEmission")
        self.added.append(em)
        em.inputs["Strength"].default_value = 1.0
        src_socket = self._source_socket(source)
        if src_socket is not None:
            self.tree.links.new(src_socket, em.inputs["Color"])
        self.tree.links.new(em.outputs["Emission"], surf)

    def _source_socket(self, source):
        if source == "@height":
            fin = self.tree.nodes.get(material.FINISH_NODE)
            if fin is not None:
                return fin.outputs["Height"]
            val = self.tree.nodes.new("ShaderNodeValue")
            val.outputs[0].default_value = 1.0
            self.added.append(val)
            return val.outputs[0]
        bsdf = _principled(self.tree, self.out)
        if bsdf is None:
            return None
        inp = bsdf.inputs[source]
        if inp.is_linked:
            return inp.links[0].from_socket
        if inp.type == "RGBA":
            n = self.tree.nodes.new("ShaderNodeRGB")
            n.outputs[0].default_value = inp.default_value
        else:
            n = self.tree.nodes.new("ShaderNodeValue")
            n.outputs[0].default_value = inp.default_value
        self.added.append(n)
        return n.outputs[0]

    def restore(self):
        if self.out is None:
            return
        for n in self.added:
            self.tree.nodes.remove(n)
        if self.prev is not None:
            self.tree.links.new(self.prev, self.out.inputs["Surface"])


def _ensure_uv(obj):
    me = obj.data
    if me.uv_layers:
        return False
    me.uv_layers.new(name="PP Bake")
    ctx = bpy.context
    prev_mode = obj.mode
    with ctx.temp_override(active_object=obj, object=obj, selected_objects=[obj],
                           selected_editable_objects=[obj]):
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.003)
        bpy.ops.object.mode_set(mode=prev_mode if prev_mode != "EDIT" else "OBJECT")
    return True


def _out_dir(path):
    path = bpy.path.abspath(path)
    if path.startswith("//") or not os.path.isabs(path):
        path = os.path.join(os.path.expanduser("~"), "procedural_palette_bakes")
    os.makedirs(path, exist_ok=True)
    return path


def _save(img, folder, is_float):
    ext = ".exr" if is_float else ".png"
    img.filepath_raw = os.path.join(folder, bpy.path.clean_name(img.name) + ext)
    img.file_format = "OPEN_EXR" if is_float else "PNG"
    img.save()


def bake_object(context, obj, settings, report=print):
    """Bake the selected passes for obj. Returns list of saved file paths."""
    scene = context.scene
    if obj is None or obj.type != "MESH":
        raise RuntimeError("Select a mesh object to bake")
    mats = [s.material for s in obj.material_slots if s.material and s.material.node_tree]
    if not mats:
        raise RuntimeError("Object has no node materials")

    wanted = [k for k in ORDER if getattr(settings, f"bake_{k}")]
    if settings.bake_orm:
        for k in ("ao", "roughness", "metallic"):
            if k not in wanted:
                wanted.append(k)
    if not wanted:
        raise RuntimeError("No bake passes enabled")

    res = int(settings.bake_resolution)
    folder = _out_dir(settings.bake_dir)

    prev = dict(engine=scene.render.engine, samples=scene.cycles.samples,
                selected=[o for o in context.view_layer.objects if o.select_get()],
                active=context.view_layer.objects.active)
    created_uv = False
    images = {}
    tex_nodes = []
    try:
        scene.render.engine = "CYCLES"
        for o in prev["selected"]:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        created_uv = _ensure_uv(obj)

        for key in wanted:
            label, btype, source, cspace, is_float = PASSES[key]
            img = bpy.data.images.new(f"{obj.name}_{label}", res, res, alpha=False,
                                      float_buffer=is_float)
            img.colorspace_settings.name = cspace
            images[key] = img
            for m in mats:
                n = m.node_tree.nodes.new("ShaderNodeTexImage")
                n.image = img
                n.location = (-600, 600)
                for other in m.node_tree.nodes:
                    other.select = False
                n.select = True
                m.node_tree.nodes.active = n
                tex_nodes.append((m, n))
            rewires = []
            if btype == "EMIT":
                rewires = [_EmitRewire(m, source) for m in mats]
                scene.cycles.samples = 1
            else:
                scene.cycles.samples = settings.bake_samples
            try:
                kw = dict(type=btype, margin=settings.bake_margin, use_clear=True)
                if btype == "NORMAL":
                    kw["normal_space"] = "TANGENT"
                with context.temp_override(active_object=obj, object=obj,
                                           selected_objects=[obj]):
                    bpy.ops.object.bake(**kw)
            finally:
                for r in rewires:
                    r.restore()
            for m, n in tex_nodes:
                m.node_tree.nodes.remove(n)
            tex_nodes.clear()
            _save(img, folder, is_float)
            report(f"Baked {label}")

        if settings.bake_orm:
            import numpy as np
            n = res * res * 4
            chans = []
            for k in ("ao", "roughness", "metallic"):
                buf = np.empty(n, dtype=np.float32)
                images[k].pixels.foreach_get(buf)
                chans.append(buf[0::4])
            orm = bpy.data.images.new(f"{obj.name}_ORM", res, res, alpha=False)
            orm.colorspace_settings.name = "Non-Color"
            out = np.ones(n, dtype=np.float32)
            out[0::4], out[1::4], out[2::4] = chans
            orm.pixels.foreach_set(out)
            _save(orm, folder, False)
            images["orm"] = orm
    finally:
        for m, n in tex_nodes:
            m.node_tree.nodes.remove(n)
        scene.render.engine = prev["engine"]
        scene.cycles.samples = prev["samples"]
        obj.select_set(False)
        for o in prev["selected"]:
            o.select_set(True)
        context.view_layer.objects.active = prev["active"]
    return [img.filepath_raw for img in images.values()], created_uv

