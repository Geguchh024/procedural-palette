# SPDX-License-Identifier: GPL-3.0-or-later
"""Weathering ("finish") and detail overlay node groups.

The finish group sits between a pattern and the Principled BSDF and adds
edge wear, rust, dirt, moss, dust, snow and wetness where they occur in
nature: edges, cavities, sky-facing surfaces and low areas.
"""

from . import nodekit
from .nodekit import auto_layout, new_group

F = "FACTOR"
D = "DISTANCE"

FINISH_GROUP = "PP Finish"

FINISH_INPUTS = [
    ("Vector", "vector", (0, 0, 0)),
    ("Seed", "float", 0.0, 0.0, 10000.0),
    ("Color", "color", (0.5, 0.5, 0.5, 1.0)),
    ("Roughness", "float", 0.5, 0.0, 1.0, F),
    ("Metallic", "float", 0.0, 0.0, 1.0, F),
    ("Height", "float", 1.0, 0.0, 1.0),
    ("Depth", "float", 0.004, 0.0, 1.0, D),
    ("Edge Wear", "float", 0.0, 0.0, 1.0, F),
    ("Edge Width", "float", 0.015, 0.0005, 0.2, D),
    ("Wear Color", "color", "#b9b4ad"),
    ("Wear Roughness", "float", 0.4, 0.0, 1.0, F),
    ("Wear Metallic", "float", 0.0, 0.0, 1.0, F),
    ("Rust", "float", 0.0, 0.0, 1.0, F),
    ("Rust Color", "color", "#6b2f14"),
    ("Dirt", "float", 0.0, 0.0, 1.0, F),
    ("Dirt Color", "color", "#2e261d"),
    ("Grime Streaks", "float", 0.0, 0.0, 1.0, F),
    ("Moss", "float", 0.0, 0.0, 1.0, F),
    ("Moss Color", "color", "#4b5e23"),
    ("Dust", "float", 0.0, 0.0, 1.0, F),
    ("Dust Color", "color", "#a59b8a"),
    ("Snow", "float", 0.0, 0.0, 1.0, F),
    ("Wetness", "float", 0.0, 0.0, 1.0, F),
    ("Puddles", "float", 0.0, 0.0, 1.0, F),
]
FINISH_OUTPUTS = [
    ("Color", "color"), ("Roughness", "float"), ("Metallic", "float"),
    ("Height", "float"), ("Displacement", "vector"),
]
FINISH_SIMPLE = ["Depth", "Edge Wear", "Rust", "Dirt", "Moss", "Dust", "Snow", "Wetness"]

# UI sections: (label, [socket names]); the first name is the section's amount
FINISH_SECTIONS = [
    ("Depth", ["Depth"]),
    ("Edge Wear", ["Edge Wear", "Edge Width", "Wear Color", "Wear Roughness", "Wear Metallic"]),
    ("Rust", ["Rust", "Rust Color"]),
    ("Dirt", ["Dirt", "Dirt Color", "Grime Streaks"]),
    ("Moss", ["Moss", "Moss Color"]),
    ("Dust", ["Dust", "Dust Color"]),
    ("Snow", ["Snow"]),
    ("Wetness", ["Wetness", "Puddles"]),
]


def _strength(nb, amount):
    """0 when amount is 0, quickly 1 otherwise (so masks fully switch off)."""
    return nb.clamp01(nb.mul(amount, 20.0))


def build_finish_group():
    if nodekit.group_is_current(FINISH_GROUP):
        import bpy
        return bpy.data.node_groups[FINISH_GROUP]
    ng, nb, gin, gout = new_group(FINISH_GROUP, FINISH_INPUTS, FINISH_OUTPUTS, FINISH_SIMPLE)
    ng["pp_kind"] = "finish"
    I = {s.name: s for s in gin.outputs if s.name}
    vs = nb.vadd(I["Vector"], nb.vscale((5.31, 2.77, 8.13), I["Seed"]))

    geo = nb.node("ShaderNodeNewGeometry")
    normal = geo.outputs["Normal"]
    _, _, up = nb.sep(normal)
    top = nb.smooth(up, 0.25, 0.85)

    # convex edges: compare a bevelled normal with the true normal (Cycles)
    bev = nb.node("ShaderNodeBevel", samples=8)
    nb.set(bev, "Radius", I["Edge Width"])
    curv = nb.one_minus(nb.vmath("DOT_PRODUCT", bev.outputs["Normal"], normal))
    # convexity from occlusion traced *inside* the mesh: catches rounded and
    # bevelled edges where the shading normal is already smooth (EEVEE too)
    ao_in = nb.node("ShaderNodeAmbientOcclusion", samples=8, inside=True, only_local=True)
    nb.set(ao_in, "Distance", nb.mul(I["Edge Width"], 3.0))
    convex = nb.one_minus(ao_in.outputs["AO"])
    edge = nb.clamp01(nb.max(nb.mul(curv, 30.0), nb.mul(convex, 2.5)))

    # cavities from ambient occlusion and from the pattern's own height
    ao = nb.node("ShaderNodeAmbientOcclusion", samples=8, only_local=True)
    nb.set(ao, "Distance", 0.15)
    cav = nb.one_minus(ao.outputs["AO"])
    hcav = nb.one_minus(I["Height"])
    cavity = nb.clamp01(nb.max(nb.mul(cav, 1.5), nb.mul(hcav, 1.2)))

    # fBm noise clusters around 0.5; stretch it to use the full 0..1 range so
    # every mask threshold below can be driven linearly by its amount slider
    n_big = nb.smooth(nb.nfac(vs, 2.0, 6.0, 0.6), 0.28, 0.72)
    n_mid = nb.smooth(nb.nfac(vs, 9.0, 6.0, 0.6), 0.28, 0.72)
    n_fine = nb.smooth(nb.nfac(vs, 60.0, 4.0, 0.6), 0.28, 0.72)

    color, rough, metal, height = I["Color"], I["Roughness"], I["Metallic"], I["Height"]

    # rust: patches growing from edges and cavities
    r_score = nb.add(nb.mul(n_mid, 0.7), nb.add(nb.mul(edge, 0.3), nb.mul(cavity, 0.3)))
    rust = nb.mul(nb.mask_thr(r_score, nb.sub(1.0, I["Rust"]), 0.06), _strength(nb, I["Rust"]))
    rust_col = nb.mix(nb.mix(I["Rust Color"], (0, 0, 0), 0.55), I["Rust Color"], n_fine)
    color = nb.mix(color, rust_col, rust)
    rough = nb.lerp(rough, nb.add(0.75, nb.mul(n_fine, 0.25)), rust)
    metal = nb.lerp(metal, 0.0, rust)
    height = nb.sub(height, nb.mul(rust, nb.mul(n_fine, 0.15)))

    # edge wear: chipped edges reveal the wear colour
    e_score = nb.mul(edge, nb.add(0.55, nb.mul(n_fine, 0.9)))
    wear = nb.mul(nb.mask_thr(e_score, nb.sub(1.0, nb.mul(I["Edge Wear"], 0.85)), 0.04),
                  _strength(nb, I["Edge Wear"]))
    color = nb.mix(color, I["Wear Color"], wear)
    rough = nb.lerp(rough, I["Wear Roughness"], wear)
    metal = nb.lerp(metal, I["Wear Metallic"], wear)
    height = nb.sub(height, nb.mul(wear, 0.05))

    # dirt in cavities and grime streaks running down
    d_score = nb.add(nb.mul(cavity, 0.7), nb.mul(n_mid, 0.45))
    dirt = nb.mul(nb.mask_thr(d_score, nb.sub(1.0, nb.mul(I["Dirt"], 0.85)), 0.15), _strength(nb, I["Dirt"]))
    streak_n = nb.nfac(nb.vmul(vs, (25.0, 25.0, 1.5)), 1.0, 4.0, 0.6)
    streaks = nb.mul(nb.smooth(streak_n, 0.5, 0.75), nb.mul(I["Grime Streaks"], nb.one_minus(top)))
    dirt = nb.clamp01(nb.add(dirt, nb.mul(streaks, 0.7)))
    color = nb.mix(color, I["Dirt Color"], nb.mul(dirt, 0.85))
    rough = nb.lerp(rough, 0.95, dirt)

    # moss on top faces and in cavities, patchy
    m_score = nb.add(nb.mul(n_big, 0.5), nb.add(nb.mul(top, 0.3), nb.mul(cavity, 0.4)))
    moss = nb.mul(nb.mask_thr(m_score, nb.sub(1.0, nb.mul(I["Moss"], 0.9)), 0.05), _strength(nb, I["Moss"]))
    moss_col = nb.hsv(I["Moss Color"], nb.add(0.47, nb.mul(n_fine, 0.06)), 1.0, nb.add(0.6, nb.mul(n_fine, 0.8)))
    color = nb.mix(color, moss_col, moss)
    rough = nb.lerp(rough, 0.95, moss)
    metal = nb.lerp(metal, 0.0, moss)
    height = nb.add(height, nb.mul(moss, nb.mul(n_fine, 0.25)))

    # dust settles on upward faces
    dust = nb.mul(nb.mul(top, nb.add(0.4, nb.mul(n_mid, 0.6))), I["Dust"])
    color = nb.mix(color, I["Dust Color"], nb.clamp01(dust))
    rough = nb.lerp(rough, 0.9, nb.clamp01(dust))
    metal = nb.lerp(metal, 0.0, nb.clamp01(dust))

    # snow on sky-facing surfaces, fills cavities first
    s_score = nb.add(nb.mul(up, 0.7), nb.add(nb.mul(n_mid, 0.25), nb.mul(hcav, 0.2)))
    snow = nb.mul(nb.mask_thr(s_score, nb.sub(1.0, nb.mul(I["Snow"], 0.9)), 0.04), _strength(nb, I["Snow"]))
    color = nb.mix(color, "#eef2f6", snow)
    rough = nb.lerp(rough, 0.55, snow)
    metal = nb.lerp(metal, 0.0, snow)
    height = nb.lerp(height, nb.add(1.0, nb.mul(n_fine, 0.1)), snow)

    # wetness and puddles in low, flat areas
    p_score = nb.add(nb.mul(hcav, 0.5), nb.mul(n_big, 0.6))
    puddle = nb.mul(nb.mask_thr(p_score, nb.sub(1.0, nb.mul(I["Puddles"], 0.8)), 0.03),
                    nb.mul(top, _strength(nb, I["Puddles"])))
    wet = nb.clamp01(nb.max(nb.mul(I["Wetness"], nb.add(0.6, nb.mul(n_mid, 0.4))), puddle))
    wet = nb.mul(wet, nb.one_minus(snow))
    darker = nb.mix(color, color, 1.0, "MULTIPLY")
    color = nb.mix(color, nb.mix(color, darker, 0.7), wet)
    rough = nb.lerp(rough, 0.04, nb.max(nb.mul(wet, 0.8), puddle))
    height = nb.lerp(height, nb.max(height, 0.85), puddle)

    disp = nb.node("ShaderNodeDisplacement")
    nb.set(disp, "Height", height)
    nb.set(disp, "Midlevel", 1.0)
    nb.set(disp, "Scale", I["Depth"])

    nb.link(color, gout.inputs["Color"])
    nb.link(nb.clamp01(rough), gout.inputs["Roughness"])
    nb.link(nb.clamp01(metal), gout.inputs["Metallic"])
    nb.link(height, gout.inputs["Height"])
    nb.link(disp.outputs["Displacement"], gout.inputs["Displacement"])
    auto_layout(ng)
    return ng


# ---------------------------------------------------------------------------
# detail overlays
# ---------------------------------------------------------------------------

DETAILS = [
    ("NONE", "None", "No detail overlay"),
    ("CRACKS", "Cracks", "Fine branching cracks"),
    ("VEINS", "Veins", "Thin mineral veins"),
    ("SPOTS", "Spots", "Round spots or dots"),
    ("STRIPES", "Stripes", "Parallel stripes"),
    ("PORES", "Pores", "Small pits"),
    ("SPECKLE", "Speckle", "Fine coloured grains"),
    ("STRATA", "Strata", "Horizontal layering"),
    ("CLOUDS", "Clouds", "Soft cloudy blotches"),
    ("CRYSTALS", "Crystals", "Angular crystal facets"),
]

DETAIL_INPUTS = [
    ("Vector", "vector", (0, 0, 0)),
    ("Seed", "float", 0.0, 0.0, 10000.0),
    ("Color", "color", (0.5, 0.5, 0.5, 1.0)),
    ("Roughness", "float", 0.5, 0.0, 1.0, F),
    ("Height", "float", 1.0, 0.0, 1.0),
    ("Detail Amount", "float", 0.5, 0.0, 1.0, F),
    ("Detail Size", "float", 0.1, 0.001, 10.0, D),
    ("Detail Color", "color", "#3a3330"),
    ("Detail Roughness", "float", 0.5, 0.0, 1.0, F),
    ("Detail Depth", "float", 0.3, -1.0, 1.0),
    ("Detail Sharpness", "float", 0.5, 0.0, 1.0, F),
]
DETAIL_OUTPUTS = [("Color", "color"), ("Roughness", "float"), ("Height", "float")]
DETAIL_SIMPLE = ["Detail Amount", "Detail Size", "Detail Color"]


def build_detail_group(kind):
    name = f"PP Detail {kind.title()}"
    if nodekit.group_is_current(name):
        import bpy
        return bpy.data.node_groups[name]
    ng, nb, gin, gout = new_group(name, DETAIL_INPUTS, DETAIL_OUTPUTS, DETAIL_SIMPLE)
    ng["pp_kind"] = "detail"
    I = {s.name: s for s in gin.outputs if s.name}
    inv = nb.div(1.0, I["Detail Size"])
    vs = nb.vadd(I["Vector"], nb.vscale((3.7, 8.1, 1.9), I["Seed"]))
    sharp = I["Detail Sharpness"]
    amount = I["Detail Amount"]

    if kind == "CRACKS":
        warp = nb.noise(vs, nb.mul(inv, 1.5), 3.0).outputs["Color"]
        pv = nb.vadd(vs, nb.vscale(warp, nb.mul(I["Detail Size"], 0.4)))
        e = nb.voronoi(pv, inv, "DISTANCE_TO_EDGE").outputs["Distance"]
        width = nb.lerp(0.08, 0.01, sharp)
        m = nb.one_minus(nb.smooth(e, 0.0, width))
        keep = nb.smooth(nb.nfac(vs, nb.mul(inv, 0.4), 2.0), nb.sub(0.75, nb.mul(amount, 0.5)), 0.8)
        m = nb.mul(m, keep)
    elif kind == "VEINS":
        w = nb.wave(vs, inv, 8.0, 5.0, 1.5, direction="DIAGONAL")
        m = nb.pow(w.outputs["Fac"], nb.lerp(6.0, 60.0, sharp))
    elif kind == "SPOTS":
        v = nb.voronoi(vs, inv)
        r, _, _ = nb.sep(v.outputs["Color"])
        rad = nb.mul(amount, 0.45)
        m = nb.one_minus(nb.smooth(v.outputs["Distance"], nb.mul(rad, nb.lerp(0.4, 0.95, sharp)), rad))
        m = nb.mul(m, nb.math("LESS_THAN", r, nb.add(amount, 0.3)))
    elif kind == "STRIPES":
        w = nb.wave(vs, inv, 0.0, 0.0, 1.0, direction="X")
        m = nb.smooth(w.outputs["Fac"], nb.sub(1.0, amount), nb.add(nb.sub(1.0, amount), nb.lerp(0.3, 0.01, sharp)))
    elif kind == "PORES":
        v = nb.voronoi(vs, inv)
        r, _, _ = nb.sep(v.outputs["Color"])
        m = nb.one_minus(nb.smooth(v.outputs["Distance"], 0.0, nb.lerp(0.35, 0.12, sharp)))
        m = nb.mul(m, nb.math("LESS_THAN", r, amount))
    elif kind == "SPECKLE":
        v = nb.voronoi(vs, inv)
        r, _, _ = nb.sep(v.outputs["Color"])
        m = nb.math("LESS_THAN", r, amount)
    elif kind == "STRATA":
        w = nb.wave(vs, inv, 3.0, 3.0, 1.0, direction="Z")
        m = nb.pow(w.outputs["Fac"], nb.lerp(2.0, 20.0, sharp))
    elif kind == "CLOUDS":
        n = nb.nfac(vs, inv, 6.0, 0.6)
        m = nb.smooth(n, nb.sub(0.75, nb.mul(amount, 0.4)), nb.sub(0.95, nb.mul(sharp, 0.15)))
    else:  # CRYSTALS
        v = nb.voronoi(vs, inv, metric="CHEBYCHEV")
        r, _, _ = nb.sep(v.outputs["Color"])
        m = nb.mul(nb.smooth(r, nb.sub(1.0, amount), 1.0), nb.one_minus(nb.smooth(v.outputs["Distance"], 0.3, 0.5)))
        m = nb.clamp01(nb.mul(m, nb.lerp(1.5, 6.0, sharp)))

    m = nb.clamp01(m)
    strength = nb.clamp01(nb.mul(amount, 4.0))
    m = nb.mul(m, strength)
    nb.link(nb.mix(I["Color"], I["Detail Color"], m), gout.inputs["Color"])
    nb.link(nb.lerp(I["Roughness"], I["Detail Roughness"], m), gout.inputs["Roughness"])
    nb.link(nb.sub(I["Height"], nb.mul(m, nb.mul(I["Detail Depth"], 0.5))), gout.inputs["Height"])
    auto_layout(ng)
    return ng
