# SPDX-License-Identifier: GPL-3.0-or-later
"""Render preview thumbnails for every style and detail overlay.

    blender -b --factory-startup --python tools/render_thumbnails.py [-- STYLE_ID_SUBSTRING ...]

Writes PNGs with a transparent background into procedural_palette/thumbs.
Each preview is a sphere shown at a size that suits the pattern, so bricks,
threads and skin pores are all readable in a small ball.
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bpy  # noqa: E402

import procedural_palette as pp  # noqa: E402

pp.register()
from procedural_palette import finish, material, presets, thumbs  # noqa: E402

RES = 192
SAMPLES = 32

# sphere diameter in metres per pattern, so the real-size pattern reads well
DIAMETER = {
    "brick": 1.2, "tiles": 0.9, "planks": 1.2, "wood": 0.3, "bark": 0.8,
    "marble": 0.8, "speckle": 0.25, "strata": 1.0, "terrazzo": 0.5,
    "concrete": 0.6, "metal": 0.4, "plastic": 0.2, "fabric": 0.025,
    "leather": 0.12, "skin": 0.08, "ground": 0.6, "roof_tiles": 2.5,
    "sheet_roof": 1.2, "glass": 0.4, "plaster": 0.5,
}


def setup_scene():
    sc = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=0.5)
    sphere = bpy.context.object
    bpy.ops.object.shade_smooth()

    cam = bpy.data.cameras.new("thumb_cam")
    cam.type = "ORTHO"
    cam.ortho_scale = 1.0
    co = bpy.data.objects.new("thumb_cam", cam)
    sc.collection.objects.link(co)
    co.location = (0, -5, 0)
    co.rotation_euler = (math.radians(90), 0, 0)
    sc.camera = co

    key = bpy.data.lights.new("key", "AREA")
    key.energy = 120
    key.size = 2.0
    ko = bpy.data.objects.new("key", key)
    sc.collection.objects.link(ko)
    ko.location = (-2.0, -2.5, 2.5)
    ko.rotation_euler = (math.radians(50), 0, math.radians(-40))

    world = bpy.data.worlds.new("thumb_world")
    sc.world = world
    nt = world.node_tree
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    hdri = os.path.join(bpy.utils.system_resource("DATAFILES"), "studiolights", "world", "interior.exr")
    env.image = bpy.data.images.load(hdri)
    bg = nt.nodes["Background"]
    bg.inputs["Strength"].default_value = 0.9
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])

    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = SAMPLES
    sc.cycles.use_denoising = True
    sc.render.film_transparent = True
    sc.render.resolution_x = sc.render.resolution_y = RES
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "Standard"
    return sphere


def render(sphere, mat, diameter, key):
    sphere.data.materials.clear()
    sphere.data.materials.append(mat)
    # Real Size mapping: pretend the 1 m sphere has the target diameter
    node = material.control_node(mat, material.SCALE_NODE)
    if node is not None:
        node.inputs[1].default_value = (diameter, diameter, diameter)
    fin = material.control_node(mat, material.FINISH_NODE)
    if fin is not None:
        # displacement depth is in metres; scale it to the preview size
        fin.inputs["Depth"].default_value /= max(diameter, 0.05)
    bpy.context.scene.render.filepath = thumbs.thumb_path(key)
    bpy.ops.render.render(write_still=True)
    print("THUMB", key, flush=True)


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    os.makedirs(thumbs.THUMB_DIR, exist_ok=True)
    sphere = setup_scene()
    for style in presets.STYLES:
        if args and not any(a in style.id for a in args):
            continue
        mat = material.new_material(style, sphere)
        render(sphere, mat, DIAMETER.get(style.pattern, 0.6), thumbs.style_key(style.id))
        bpy.data.materials.remove(mat)

    base = presets.STYLE_BY_ID["PAINT/Smooth Plaster"]
    for kind, _, _ in finish.DETAILS:
        if args and not any(a in f"DETAIL/{kind}" for a in args):
            continue
        mat = material.new_material(base, sphere)
        with material.quiet():
            mat.pp.detail = kind
        material.build(mat, None, sphere, keep_values=True)
        det = material.control_node(mat, material.DETAIL_NODE)
        if det is not None:
            det.inputs["Detail Amount"].default_value = 0.7
            det.inputs["Detail Size"].default_value = 0.12
            det.inputs["Detail Color"].default_value = (0.08, 0.06, 0.05, 1.0)
        render(sphere, mat, 0.6, thumbs.detail_key(kind))
        bpy.data.materials.remove(mat)


main()
