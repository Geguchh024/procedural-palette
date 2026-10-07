# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural surface patterns.

Each pattern is a shader node group with a common interface:

  inputs : Vector, Seed, Size, <pattern parameters...>
  outputs: Color, Roughness, Metallic, Height, Transmission, Coat, Sheen,
           Subsurface, IOR

Height is 0..1 where 1 is the outer surface and 0 the deepest groove, so
displacement can use a mid-level of 1 and only ever push inward.

All sizes are real-world metres before the global Size multiplier.
"""

import math

from . import nodekit
from .nodekit import auto_layout, new_group

PI = math.pi

OUTPUTS = [
    ("Color", "color"), ("Roughness", "float"), ("Metallic", "float"),
    ("Height", "float"), ("Transmission", "float"), ("Coat", "float"),
    ("Sheen", "float"), ("Subsurface", "float"), ("IOR", "float"),
]
OUTPUT_DEFAULTS = {"Metallic": 0.0, "Transmission": 0.0, "Coat": 0.0,
                   "Sheen": 0.0, "Subsurface": 0.0, "IOR": 1.5, "Height": 1.0,
                   "Roughness": 0.5}

COMMON = [
    ("Vector", "vector", (0, 0, 0)),
    ("Seed", "float", 0.0, 0.0, 10000.0),
    ("Size", "float", 1.0, 0.01, 100.0),
]

D = "DISTANCE"
F = "FACTOR"


class Pattern:
    def __init__(self, key, label, dims, inputs, simple, build, description=""):
        self.key = key
        self.label = label
        self.dims = dims          # 2 => planar pattern, gets box projection
        self.inputs = inputs
        self.simple = simple
        self.build = build
        self.description = description

    @property
    def group_name(self):
        return f"PP {self.label}"


PATTERNS = {}


def pattern(key, label, dims, inputs, simple, description=""):
    def deco(fn):
        PATTERNS[key] = Pattern(key, label, dims, inputs, simple, fn, description)
        return fn
    return deco


class Ctx:
    """Shared prelude values for a builder."""

    def __init__(self, nb, I):
        self.nb = nb
        self.I = I
        inv = nb.div(1.0, I["Size"])
        self.v = nb.vscale(I["Vector"], inv)
        self.seed = I["Seed"]
        self.vs = nb.vadd(self.v, nb.vscale((17.13, 31.71, 9.37), self.seed))
        self.x, self.y, self.z = nb.sep(self.v)

    def scaled(self, sx, sy, sz, vec=None):
        return self.nb.vmul(vec or self.vs, (sx, sy, sz))


# ---------------------------------------------------------------------------
# shared building blocks
# ---------------------------------------------------------------------------

def wood_core(nb, p, light, dark, ring_scale, grain, distortion, figure=0.5):
    """Wood with growth rings around the X axis and pores streaking along X.

    p is a position in metres. Returns (color, height, ring_mask).
    """
    # low frequency warping of the log so rings wobble and form cathedrals
    warp = nb.noise(nb.vmul(p, (0.8, 4.0, 4.0)), 1.0, 3.0, 0.5).outputs["Color"]
    warp = nb.vscale(nb.vsub(warp, (0.5, 0.5, 0.5)), nb.mul(distortion, 0.03))
    pw = nb.vadd(p, warp)
    _, py, pz = nb.sep(pw)
    r = nb.vmath("LENGTH", nb.comb(0.0, py, pz))
    freq = nb.div(1.0, nb.mul(ring_scale, 0.0045))
    rings = nb.mul(r, freq)
    ring_noise = nb.nfac(nb.vmul(p, (2.0, 30.0, 30.0)), 1.0, 2.0, 0.5)
    rings = nb.add(rings, nb.mul(ring_noise, 0.6))
    ring = nb.fract(rings)
    late = nb.smooth(ring, 0.55, 0.92)                 # latewood: dark band
    # pores and fine grain streaking along the length
    pores = nb.nfac(nb.vmul(pw, (6.0, 700.0, 700.0)), 1.0, 2.0, 0.6)
    pores = nb.smooth(pores, 0.52, 0.72)
    fine = nb.nfac(nb.vmul(pw, (3.0, 160.0, 160.0)), 1.0, 3.0, 0.6)
    # broad figure / colour streaks
    streak = nb.nfac(nb.vmul(p, (0.6, 12.0, 12.0)), 1.0, 2.0, 0.5)
    t = nb.add(nb.mul(late, 0.75), nb.mul(nb.sub(streak, 0.5), figure))
    t = nb.add(t, nb.mul(nb.sub(fine, 0.5), 0.35))
    col = nb.mix(light, dark, nb.clamp01(t))
    col = nb.mix(col, nb.mix(dark, (0.0, 0.0, 0.0), 0.35), nb.mul(pores, nb.mul(grain, 0.6)))
    h = nb.sub(1.0, nb.add(nb.mul(pores, nb.mul(grain, 0.45)), nb.mul(late, 0.08)))
    return col, h, late


def vary_color(nb, color, rnd3, amount, hue=0.04):
    """Per-cell hue/value variation from a random colour (3 channels)."""
    r, g, _ = nb.sep(rnd3)
    h = nb.add(0.5, nb.mul(nb.sub(r, 0.5), nb.mul(amount, hue)))
    v = nb.add(1.0, nb.mul(nb.sub(g, 0.5), amount))
    return nb.hsv(color, h, 1.0, v)


def mottle(nb, color, vec, scale, amount, dark=0.75):
    n = nb.nfac(vec, scale, 4.0, 0.55)
    m = nb.mul(nb.smooth(n, 0.3, 0.7), amount)
    return nb.mix(color, nb.mix(color, (0, 0, 0), 1.0 - dark), m)


def lt(nb, a, b):
    return nb.math("LESS_THAN", a, b)


# ---------------------------------------------------------------------------
# masonry
# ---------------------------------------------------------------------------

@pattern("brick", "Brick", 2, [
    ("Brick Length", "float", 0.215, 0.01, 2.0, D),
    ("Brick Height", "float", 0.065, 0.01, 1.0, D),
    ("Mortar Width", "float", 0.010, 0.0, 0.1, D),
    ("Bond Offset", "float", 0.5, 0.0, 1.0, F),
    ("Bevel", "float", 0.004, 0.0, 0.05, D),
    ("Color A", "color", "#8a3b2a"),
    ("Color B", "color", "#a8553a"),
    ("Color Variation", "float", 0.35, 0.0, 1.0, F),
    ("Burnt Bricks", "float", 0.12, 0.0, 1.0, F),
    ("Grit", "float", 0.5, 0.0, 1.0, F),
    ("Mortar Color", "color", "#9b958a"),
    ("Mortar Depth", "float", 0.55, 0.0, 1.0, F),
    ("Roughness", "float", 0.85, 0.0, 1.0, F),
], ["Color A", "Color B", "Mortar Color", "Brick Length", "Brick Height",
    "Mortar Width", "Color Variation"])
def build_brick(nb, I, c):
    g = nb.grid(c.x, c.y, I["Brick Length"], I["Brick Height"], I["Mortar Width"],
                I["Bond Offset"], 0.0, I["Bevel"], c.seed)
    _, _, rz = nb.sep(g["rand"])
    col = nb.mix(I["Color A"], I["Color B"], g["rand1"])
    col = vary_color(nb, col, g["rand"], I["Color Variation"])
    burnt = lt(nb, rz, I["Burnt Bricks"])
    burn_tone = nb.ramp(nb.nfac(c.vs, 12.0, 3.0), [(0.3, "#1d1210"), (0.8, "#4a2a20")])
    col = nb.mix(col, burn_tone, nb.mul(burnt, nb.smooth(g["u"], -0.3, 0.9)))
    # surface grit and pits
    vor = nb.voronoi(c.vs, 220.0)
    pits = nb.one_minus(nb.smooth(vor.outputs["Distance"], 0.0, 0.22))
    pits = nb.mul(pits, I["Grit"])
    col = mottle(nb, col, c.vs, 18.0, nb.mul(I["Grit"], 0.6))
    col = nb.mix(col, nb.mix(col, (0, 0, 0), 0.45), pits)
    # mortar
    mort_n = nb.nfac(c.vs, 140.0, 3.0, 0.7)
    mort = nb.mix(I["Mortar Color"], nb.mix(I["Mortar Color"], (0, 0, 0), 0.35),
                  nb.smooth(mort_n, 0.45, 0.7))
    out_col = nb.mix(col, mort, g["mortar"])
    face = nb.sub(1.0, nb.mul(pits, 0.25))
    deep = nb.sub(1.0, I["Mortar Depth"])
    deep = nb.add(deep, nb.mul(mort_n, 0.12))
    h = nb.lerp(deep, face, g["profile"])
    rough = nb.lerp(I["Roughness"], 0.95, g["mortar"])
    return {"Color": out_col, "Roughness": rough, "Height": h}


@pattern("tiles", "Tiles", 2, [
    ("Tile Width", "float", 0.30, 0.005, 5.0, D),
    ("Tile Height", "float", 0.30, 0.005, 5.0, D),
    ("Grout Width", "float", 0.004, 0.0, 0.05, D),
    ("Row Offset", "float", 0.0, 0.0, 1.0, F),
    ("Bevel", "float", 0.003, 0.0, 0.05, D),
    ("Color A", "color", "#e9e6df"),
    ("Color B", "color", "#2c2c2e"),
    ("Color B Amount", "float", 0.0, 0.0, 1.0, F),
    ("Checker", "float", 0.0, 0.0, 1.0, F),
    ("Color Variation", "float", 0.08, 0.0, 1.0, F),
    ("Mottling", "float", 0.1, 0.0, 1.0, F),
    ("Grout Color", "color", "#a9a59c"),
    ("Grout Depth", "float", 0.4, 0.0, 1.0, F),
    ("Waviness", "float", 0.2, 0.0, 1.0, F),
    ("Roughness", "float", 0.12, 0.0, 1.0, F),
], ["Color A", "Color B", "Grout Color", "Tile Width", "Tile Height", "Checker", "Roughness"])
def build_tiles(nb, I, c):
    g = nb.grid(c.x, c.y, I["Tile Width"], I["Tile Height"], I["Grout Width"],
                I["Row Offset"], 0.0, I["Bevel"], c.seed)
    parity = nb.fmod(nb.add(g["row"], g["col"]), 2.0)
    pick_rand = lt(nb, g["rand1"], I["Color B Amount"])
    pick = nb.lerp(pick_rand, parity, I["Checker"])
    col = nb.mix(I["Color A"], I["Color B"], pick)
    col = vary_color(nb, col, g["rand"], I["Color Variation"], hue=0.02)
    tile_vec = nb.vadd(c.vs, nb.vscale(g["rand"], 30.0))
    col = mottle(nb, col, tile_vec, 6.0, I["Mottling"], 0.7)
    grout = nb.mix(I["Grout Color"], nb.mix(I["Grout Color"], (0, 0, 0), 0.25),
                   nb.smooth(nb.nfac(c.vs, 200.0, 2.0), 0.4, 0.7))
    out_col = nb.mix(col, grout, g["mortar"])
    wav = nb.mul(nb.nfac(tile_vec, 3.0, 2.0), nb.mul(I["Waviness"], 0.15))
    face = nb.sub(1.0, wav)
    h = nb.lerp(nb.sub(1.0, I["Grout Depth"]), face, g["profile"])
    rough = nb.lerp(I["Roughness"], 0.9, g["mortar"])
    return {"Color": out_col, "Roughness": rough, "Height": h}


# ---------------------------------------------------------------------------
# wood
# ---------------------------------------------------------------------------

WOOD_COLORS = [
    ("Light Wood", "color", "#c79a64"),
    ("Dark Wood", "color", "#7a4a26"),
]


@pattern("planks", "Wood Planks", 2, [
    ("Plank Length", "float", 1.8, 0.05, 20.0, D),
    ("Plank Width", "float", 0.14, 0.01, 2.0, D),
    ("Gap", "float", 0.0015, 0.0, 0.05, D),
    ("Stagger", "float", 1.0, 0.0, 1.0, F),
    ("Bevel", "float", 0.002, 0.0, 0.05, D),
    *WOOD_COLORS,
    ("Ring Scale", "float", 1.0, 0.05, 20.0),
    ("Grain", "float", 0.6, 0.0, 1.0, F),
    ("Figure", "float", 0.5, 0.0, 2.0),
    ("Plank Variation", "float", 0.35, 0.0, 1.0, F),
    ("Overlap", "float", 0.0, 0.0, 1.0, F),
    ("Gap Color", "color", "#1e140c"),
    ("Roughness", "float", 0.45, 0.0, 1.0, F),
    ("Coat", "float", 0.0, 0.0, 1.0, F),
], ["Light Wood", "Dark Wood", "Plank Length", "Plank Width", "Plank Variation", "Roughness"])
def build_planks(nb, I, c):
    g = nb.grid(c.x, c.y, I["Plank Length"], I["Plank Width"], I["Gap"],
                0.0, I["Stagger"], I["Bevel"], c.seed)
    rx, ry, rz = nb.sep(g["rand"])
    # local plank space: grain along X, log centre offset per plank
    py = nb.mul(nb.sub(g["v"], 0.5), I["Plank Width"])
    tilt = nb.mul(nb.sub(ry, 0.5), 0.03)
    pz = nb.add(nb.mul(nb.sub(rz, 0.5), 0.18), nb.mul(c.x, tilt))
    p = nb.comb(nb.add(c.x, nb.mul(rx, 37.0)), nb.add(py, nb.mul(nb.sub(ry, 0.5), 0.12)), pz)
    col, h, _ = wood_core(nb, p, I["Light Wood"], I["Dark Wood"], I["Ring Scale"],
                          I["Grain"], 1.0, I["Figure"])
    col = vary_color(nb, col, g["rand"], I["Plank Variation"], hue=0.06)
    out_col = nb.mix(col, I["Gap Color"], g["mortar"])
    lap = nb.lerp(1.0, nb.one_minus(g["v"]), I["Overlap"])
    face = nb.mul(h, nb.lerp(1.0, nb.add(0.6, nb.mul(lap, 0.4)), I["Overlap"]))
    hh = nb.lerp(0.35, face, g["profile"])
    rough = nb.lerp(I["Roughness"], 0.9, g["mortar"])
    return {"Color": out_col, "Roughness": rough, "Height": hh, "Coat": I["Coat"]}


@pattern("wood", "Solid Wood", 3, [
    *WOOD_COLORS,
    ("Ring Scale", "float", 1.0, 0.05, 20.0),
    ("Grain", "float", 0.6, 0.0, 1.0, F),
    ("Figure", "float", 0.5, 0.0, 2.0),
    ("Distortion", "float", 1.0, 0.0, 5.0),
    ("Roughness", "float", 0.45, 0.0, 1.0, F),
    ("Coat", "float", 0.0, 0.0, 1.0, F),
], ["Light Wood", "Dark Wood", "Ring Scale", "Grain", "Roughness"],
    "Solid timber, grain runs along the object's X axis")
def build_wood(nb, I, c):
    p = nb.vadd(c.v, nb.vscale((0.0, 0.13, 0.07), nb.fract(nb.mul(c.seed, 0.618))))
    col, h, late = wood_core(nb, p, I["Light Wood"], I["Dark Wood"], I["Ring Scale"],
                             I["Grain"], I["Distortion"], I["Figure"])
    rough = nb.add(I["Roughness"], nb.mul(late, 0.08))
    return {"Color": col, "Roughness": rough, "Height": h, "Coat": I["Coat"]}


@pattern("bark", "Bark", 3, [
    ("Color", "color", "#5a4a3c"),
    ("Crevice Color", "color", "#1f1914"),
    ("Furrow Scale", "float", 0.06, 0.005, 1.0, D),
    ("Furrow Depth", "float", 0.8, 0.0, 1.0, F),
    ("Stretch", "float", 4.0, 1.0, 20.0),
    ("Flakiness", "float", 0.5, 0.0, 1.0, F),
    ("Roughness", "float", 0.9, 0.0, 1.0, F),
], ["Color", "Crevice Color", "Furrow Scale", "Furrow Depth"],
    "Tree bark, furrows run along the object's Z axis")
def build_bark(nb, I, c):
    inv = nb.div(1.0, I["Furrow Scale"])
    sq = nb.div(1.0, I["Stretch"])
    warp = nb.noise(c.vs, 6.0, 3.0).outputs["Color"]
    pv = nb.vadd(nb.vmul(c.vs, nb.comb(1.0, 1.0, sq)), nb.vscale(warp, nb.mul(I["Furrow Scale"], 0.6)))
    vor = nb.voronoi(pv, inv, "DISTANCE_TO_EDGE")
    ridge = nb.smooth(vor.outputs["Distance"], 0.0, 0.35)
    flakes = nb.nfac(nb.vmul(c.vs, (40.0, 40.0, 12.0)), 1.0, 4.0, 0.6)
    flakes = nb.smooth(flakes, 0.45, 0.75)
    h = nb.sub(1.0, nb.mul(nb.one_minus(ridge), I["Furrow Depth"]))
    h = nb.sub(h, nb.mul(flakes, nb.mul(I["Flakiness"], 0.15)))
    col = nb.mix(I["Crevice Color"], I["Color"], nb.smooth(ridge, 0.0, 0.8))
    col = mottle(nb, col, c.vs, 30.0, 0.5)
    col = nb.mix(col, nb.mix(I["Color"], (1, 1, 1), 0.25), nb.mul(flakes, nb.mul(I["Flakiness"], 0.5)))
    return {"Color": col, "Roughness": I["Roughness"], "Height": h}


# ---------------------------------------------------------------------------
# stone
# ---------------------------------------------------------------------------

@pattern("marble", "Marble", 3, [
    ("Base Color", "color", "#e8e6e1"),
    ("Cloud Color", "color", "#cfcac2"),
    ("Vein Color", "color", "#6f6a66"),
    ("Vein Scale", "float", 1.5, 0.05, 50.0),
    ("Vein Width", "float", 0.35, 0.0, 1.0, F),
    ("Vein Distortion", "float", 7.0, 0.0, 30.0),
    ("Secondary Veins", "float", 0.5, 0.0, 1.0, F),
    ("Cloudiness", "float", 0.5, 0.0, 1.0, F),
    ("Roughness", "float", 0.08, 0.0, 1.0, F),
], ["Base Color", "Vein Color", "Vein Scale", "Vein Width", "Roughness"])
def build_marble(nb, I, c):
    w1 = nb.wave(c.vs, I["Vein Scale"], I["Vein Distortion"], 6.0, 1.2,
                 direction="DIAGONAL")
    k = nb.lerp(80.0, 4.0, I["Vein Width"])
    v1 = nb.pow(w1.outputs["Fac"], k)
    w2 = nb.wave(nb.vmul(c.vs, (1.0, -1.3, 0.7)), nb.mul(I["Vein Scale"], 3.1),
                 nb.mul(I["Vein Distortion"], 1.4), 8.0, 2.0, direction="DIAGONAL")
    v2 = nb.mul(nb.pow(w2.outputs["Fac"], nb.mul(k, 2.5)), I["Secondary Veins"])
    veins = nb.clamp01(nb.add(v1, nb.mul(v2, 0.7)))
    cloud = nb.nfac(c.vs, 1.2, 6.0, 0.6, 0.3)
    col = nb.mix(I["Base Color"], I["Cloud Color"], nb.mul(nb.smooth(cloud, 0.35, 0.7), I["Cloudiness"]))
    col = nb.mix(col, I["Vein Color"], veins)
    h = nb.sub(1.0, nb.mul(veins, 0.04))
    return {"Color": col, "Roughness": nb.add(I["Roughness"], nb.mul(veins, 0.05)),
            "Height": h, "Subsurface": 0.05}


@pattern("speckle", "Speckled Stone", 3, [
    ("Color A", "color", "#8f8a86"),
    ("Color B", "color", "#2d2b2a"),
    ("Accent Color", "color", "#c9b9ab"),
    ("Grain Size", "float", 0.004, 0.0005, 0.1, D),
    ("Dark Amount", "float", 0.35, 0.0, 1.0, F),
    ("Accent Amount", "float", 0.15, 0.0, 1.0, F),
    ("Polish", "float", 0.7, 0.0, 1.0, F),
], ["Color A", "Color B", "Accent Color", "Grain Size", "Polish"],
    "Granite, quartz, speckled surfaces")
def build_speckle(nb, I, c):
    inv = nb.div(1.0, I["Grain Size"])
    warp = nb.noise(c.vs, 30.0, 2.0).outputs["Color"]
    pv = nb.vadd(c.vs, nb.vscale(warp, nb.mul(I["Grain Size"], 1.5)))
    vor = nb.voronoi(pv, inv)
    r, g, b = nb.sep(vor.outputs["Color"])
    col = nb.mix(I["Color A"], I["Color B"], lt(nb, r, I["Dark Amount"]))
    col = nb.mix(col, I["Accent Color"], lt(nb, g, I["Accent Amount"]))
    col = nb.hsv(col, 0.5, 1.0, nb.add(0.85, nb.mul(b, 0.3)))
    col = mottle(nb, col, c.vs, 2.0, 0.3, 0.85)
    edges = nb.one_minus(nb.smooth(nb.voronoi(pv, inv, "DISTANCE_TO_EDGE").outputs["Distance"], 0.0, 0.06))
    rough = nb.lerp(0.75, 0.06, I["Polish"])
    h = nb.sub(1.0, nb.mul(edges, nb.lerp(0.25, 0.02, I["Polish"])))
    return {"Color": col, "Roughness": nb.add(rough, nb.mul(edges, 0.1)), "Height": h}


@pattern("strata", "Layered Stone", 3, [
    ("Color A", "color", "#c9a97a"),
    ("Color B", "color", "#a5835a"),
    ("Layer Scale", "float", 12.0, 0.1, 200.0),
    ("Layer Distortion", "float", 9.0, 0.0, 30.0),
    ("Grit", "float", 0.6, 0.0, 1.0, F),
    ("Pits", "float", 0.3, 0.0, 1.0, F),
    ("Roughness", "float", 0.9, 0.0, 1.0, F),
], ["Color A", "Color B", "Layer Scale", "Grit"],
    "Sandstone, slate, limestone, layers stack along Z")
def build_strata(nb, I, c):
    w = nb.wave(c.vs, I["Layer Scale"], I["Layer Distortion"], 6.0, 0.6, direction="Z")
    bands = nb.ramp(w.outputs["Fac"], [(0.0, 0.0), (0.45, 0.3), (0.55, 0.85), (1.0, 1.0)])
    col = nb.mix(I["Color A"], I["Color B"], bands)
    grit = nb.nfac(c.vs, 300.0, 2.0, 0.7)
    col = nb.mix(col, nb.mix(col, (0, 0, 0), 0.3), nb.mul(nb.smooth(grit, 0.5, 0.8), I["Grit"]))
    col = mottle(nb, col, c.vs, 4.0, 0.4)
    pv = nb.voronoi(c.vs, 90.0)
    pits = nb.mul(nb.one_minus(nb.smooth(pv.outputs["Distance"], 0.0, 0.18)), I["Pits"])
    h = nb.sub(1.0, nb.add(nb.mul(pits, 0.5), nb.mul(nb.sub(1.0, w.outputs["Fac"]), 0.12)))
    h = nb.sub(h, nb.mul(grit, nb.mul(I["Grit"], 0.08)))
    return {"Color": col, "Roughness": I["Roughness"], "Height": h}


@pattern("terrazzo", "Terrazzo", 3, [
    ("Matrix Color", "color", "#e3ddd2"),
    ("Chip Color 1", "color", "#3b3b3b"),
    ("Chip Color 2", "color", "#b55a3c"),
    ("Chip Color 3", "color", "#7d8a86"),
    ("Chip Size", "float", 0.035, 0.002, 0.5, D),
    ("Chip Density", "float", 0.55, 0.0, 1.0, F),
    ("Small Chips", "float", 0.6, 0.0, 1.0, F),
    ("Roughness", "float", 0.15, 0.0, 1.0, F),
], ["Matrix Color", "Chip Color 1", "Chip Color 2", "Chip Color 3", "Chip Size", "Chip Density"])
def build_terrazzo(nb, I, c):
    def chips(scale_mul, density, seed_off):
        inv = nb.div(scale_mul, I["Chip Size"])
        warp = nb.noise(c.vs, nb.mul(inv, 0.7), 2.0).outputs["Color"]
        pv = nb.vadd(nb.vadd(c.vs, (seed_off, seed_off, seed_off)),
                     nb.vscale(warp, nb.div(0.35, inv)))
        vor = nb.voronoi(pv, inv, metric="CHEBYCHEV")
        r, g, _ = nb.sep(vor.outputs["Color"])
        rad = nb.mul(density, 0.5)
        m = nb.one_minus(nb.smooth(vor.outputs["Distance"], nb.mul(rad, 0.9), rad))
        m = nb.mul(m, lt(nb, g, nb.add(density, 0.2)))
        cc = nb.mix(I["Chip Color 1"], I["Chip Color 2"], lt(nb, r, 0.6))
        cc = nb.mix(cc, I["Chip Color 3"], lt(nb, r, 0.3))
        cc = nb.hsv(cc, 0.5, 1.0, nb.add(0.8, nb.mul(g, 0.4)))
        return m, cc
    m1, c1 = chips(1.0, I["Chip Density"], 0.0)
    m2, c2 = chips(3.5, nb.mul(I["Small Chips"], I["Chip Density"]), 11.0)
    col = mottle(nb, I["Matrix Color"], c.vs, 20.0, 0.25, 0.9)
    col = nb.mix(col, c2, m2)
    col = nb.mix(col, c1, m1)
    pores = nb.one_minus(nb.smooth(nb.voronoi(c.vs, 400.0).outputs["Distance"], 0.0, 0.1))
    h = nb.sub(1.0, nb.mul(pores, 0.3))
    return {"Color": col, "Roughness": nb.add(I["Roughness"], nb.mul(nb.sub(1.0, nb.max(m1, m2)), 0.05)),
            "Height": h}


@pattern("concrete", "Concrete", 3, [
    ("Color A", "color", "#9a9893"),
    ("Color B", "color", "#7f7d78"),
    ("Mottling", "float", 0.5, 0.0, 1.0, F),
    ("Pores", "float", 0.35, 0.0, 1.0, F),
    ("Pore Size", "float", 0.003, 0.0005, 0.05, D),
    ("Aggregate", "float", 0.0, 0.0, 1.0, F),
    ("Aggregate Size", "float", 0.008, 0.001, 0.1, D),
    ("Aggregate Color", "color", "#c7c1b5"),
    ("Stains", "float", 0.2, 0.0, 1.0, F),
    ("Roughness", "float", 0.85, 0.0, 1.0, F),
], ["Color A", "Color B", "Mottling", "Pores", "Aggregate", "Roughness"],
    "Concrete, cement, asphalt")
def build_concrete(nb, I, c):
    n1 = nb.nfac(c.vs, 2.5, 6.0, 0.6)
    n2 = nb.nfac(c.vs, 25.0, 4.0, 0.6)
    t = nb.add(nb.mul(nb.smooth(n1, 0.3, 0.7), 0.7), nb.mul(nb.sub(n2, 0.5), 0.6))
    col = nb.mix(I["Color A"], I["Color B"], nb.mul(nb.clamp01(t), I["Mottling"]))
    # exposed aggregate
    inv_a = nb.div(1.0, I["Aggregate Size"])
    warp = nb.noise(c.vs, nb.mul(inv_a, 0.5), 2.0).outputs["Color"]
    av = nb.voronoi(nb.vadd(c.vs, nb.vscale(warp, nb.mul(I["Aggregate Size"], 0.6))), inv_a)
    ar, ag, _ = nb.sep(av.outputs["Color"])
    agg = nb.one_minus(nb.smooth(av.outputs["Distance"], 0.25, 0.42))
    agg = nb.mul(agg, lt(nb, ag, I["Aggregate"]))
    acol = nb.hsv(I["Aggregate Color"], 0.5, 1.0, nb.add(0.45, nb.mul(ar, 0.9)))
    col = nb.mix(col, acol, agg)
    # pores
    pv = nb.voronoi(c.vs, nb.div(0.25, I["Pore Size"]))
    pr, _, _ = nb.sep(pv.outputs["Color"])
    pores = nb.one_minus(nb.smooth(pv.outputs["Distance"], 0.05, 0.16))
    pores = nb.mul(pores, lt(nb, pr, I["Pores"]))
    col = nb.mix(col, nb.mix(col, (0, 0, 0), 0.6), pores)
    # water stains
    st = nb.nfac(nb.vmul(c.vs, (1.0, 1.0, 0.25)), 3.0, 4.0, 0.6)
    col = nb.mix(col, nb.mix(col, (0.05, 0.045, 0.04), 0.5), nb.mul(nb.smooth(st, 0.55, 0.75), I["Stains"]))
    h = nb.sub(1.0, nb.mul(pores, 0.7))
    h = nb.add(nb.sub(h, nb.mul(n2, 0.1)), nb.mul(agg, 0.08))
    rough = nb.sub(I["Roughness"], nb.mul(agg, 0.2))
    return {"Color": col, "Roughness": rough, "Height": nb.clamp01(h)}


# ---------------------------------------------------------------------------
# metal / plastic
# ---------------------------------------------------------------------------

@pattern("metal", "Metal", 3, [
    ("Color", "color", "#c8c9cb"),
    ("Metallic", "float", 1.0, 0.0, 1.0, F),
    ("Roughness", "float", 0.3, 0.0, 1.0, F),
    ("Brushing", "float", 0.0, 0.0, 1.0, F),
    ("Spangle", "float", 0.0, 0.0, 1.0, F),
    ("Spangle Size", "float", 0.015, 0.001, 0.2, D),
    ("Hammered", "float", 0.0, 0.0, 1.0, F),
    ("Hammer Size", "float", 0.012, 0.001, 0.2, D),
    ("Flakes", "float", 0.0, 0.0, 1.0, F),
    ("Variation", "float", 0.2, 0.0, 1.0, F),
    ("Coat", "float", 0.0, 0.0, 1.0, F),
], ["Color", "Roughness", "Brushing", "Variation"],
    "Brushed, polished, galvanised, hammered metals and car paint")
def build_metal(nb, I, c):
    # brushed streaks along X
    br = nb.nfac(nb.vmul(c.vs, (3.0, 900.0, 900.0)), 1.0, 3.0, 0.7)
    br2 = nb.nfac(nb.vmul(c.vs, (1.0, 120.0, 120.0)), 1.0, 2.0, 0.6)
    streak = nb.mul(nb.add(nb.sub(br, 0.5), nb.mul(nb.sub(br2, 0.5), 0.5)), I["Brushing"])
    # galvanised spangle
    sv = nb.voronoi(c.vs, nb.div(1.0, I["Spangle Size"]))
    sr, sg, _ = nb.sep(sv.outputs["Color"])
    spang = nb.mul(nb.sub(sr, 0.5), I["Spangle"])
    # hammered dents
    hv = nb.voronoi(c.vs, nb.div(1.0, I["Hammer Size"]), "SMOOTH_F1", smooth=0.6)
    dent = nb.mul(nb.smooth(hv.outputs["Distance"], 0.0, 0.7), I["Hammered"])
    # broad variation
    var = nb.nfac(c.vs, 1.5, 4.0, 0.6)
    col = nb.hsv(I["Color"], 0.5, 1.0, nb.add(1.0, nb.add(nb.mul(nb.sub(var, 0.5), nb.mul(I["Variation"], 0.5)),
                                                          nb.mul(spang, 0.5))))
    # car paint flakes
    fv = nb.voronoi(c.vs, 2500.0)
    fr, _, _ = nb.sep(fv.outputs["Color"])
    flake = nb.mul(nb.smooth(fr, 0.6, 1.0), I["Flakes"])
    col = nb.mix(col, nb.mix(col, (1, 1, 1), 0.5), nb.mul(flake, 0.6))
    rough = nb.add(I["Roughness"], nb.mul(streak, 0.35))
    rough = nb.add(rough, nb.mul(spang, 0.4))
    rough = nb.add(rough, nb.mul(nb.sub(var, 0.5), nb.mul(I["Variation"], 0.25)))
    metallic = nb.clamp01(nb.add(I["Metallic"], flake))
    h = nb.sub(1.0, nb.add(nb.mul(nb.add(streak, nb.mul(I["Brushing"], 0.5)), 0.08), nb.mul(dent, 0.6)))
    return {"Color": col, "Roughness": nb.clamp01(rough), "Metallic": metallic,
            "Height": nb.clamp01(h), "Coat": I["Coat"]}


@pattern("plastic", "Plastic & Rubber", 3, [
    ("Color", "color", "#2f6fb3"),
    ("Roughness", "float", 0.35, 0.0, 1.0, F),
    ("Texture", "float", 0.0, 0.0, 1.0, F),
    ("Texture Size", "float", 0.0015, 0.0002, 0.05, D),
    ("Color Variation", "float", 0.1, 0.0, 1.0, F),
    ("Subsurface", "float", 0.0, 0.0, 1.0, F),
    ("Coat", "float", 0.0, 0.0, 1.0, F),
], ["Color", "Roughness", "Texture"])
def build_plastic(nb, I, c):
    tv = nb.voronoi(c.vs, nb.div(1.0, I["Texture Size"]), "SMOOTH_F1", smooth=0.5)
    stipple = nb.smooth(tv.outputs["Distance"], 0.1, 0.6)
    var = nb.nfac(c.vs, 2.0, 3.0)
    col = nb.hsv(I["Color"], 0.5, 1.0, nb.add(1.0, nb.mul(nb.sub(var, 0.5), nb.mul(I["Color Variation"], 0.4))))
    rough = nb.add(I["Roughness"], nb.mul(nb.mul(stipple, I["Texture"]), 0.25))
    h = nb.sub(1.0, nb.mul(stipple, nb.mul(I["Texture"], 0.5)))
    return {"Color": col, "Roughness": nb.clamp01(rough), "Height": h,
            "Subsurface": I["Subsurface"], "Coat": I["Coat"]}


# ---------------------------------------------------------------------------
# fabric / leather / skin
# ---------------------------------------------------------------------------

@pattern("fabric", "Fabric", 2, [
    ("Warp Color", "color", "#3b4f73"),
    ("Weft Color", "color", "#d9d4c7"),
    ("Thread Count", "float", 18.0, 1.0, 200.0),
    ("Twill", "float", 0.0, 0.0, 1.0, F),
    ("Thread Variation", "float", 0.3, 0.0, 1.0, F),
    ("Fuzz", "float", 0.4, 0.0, 1.0, F),
    ("Sheen", "float", 0.5, 0.0, 1.0, F),
    ("Roughness", "float", 0.85, 0.0, 1.0, F),
], ["Warp Color", "Weft Color", "Thread Count", "Twill", "Sheen"],
    "Woven cloth. Thread Count is threads per centimetre")
def build_fabric(nb, I, c):
    f = nb.mul(I["Thread Count"], 100.0)
    xf, yf = nb.mul(c.x, f), nb.mul(c.y, f)
    tx, ty = nb.floor(xf), nb.floor(yf)
    u, v = nb.fract(xf), nb.fract(yf)
    plain = nb.fmod(nb.add(tx, ty), 2.0)
    twill = lt(nb, nb.fmod(nb.add(tx, ty), 3.0), 1.5)
    warp_top = nb.lerp(plain, twill, I["Twill"])
    warp_h = nb.sin(nb.mul(u, PI))
    weft_h = nb.sin(nb.mul(v, PI))
    h = nb.lerp(nb.mul(weft_h, 0.85), warp_h, warp_top)
    wr = nb.white(nb.comb(tx, c.seed, 1.0)).outputs["Value"]
    fr = nb.white(nb.comb(c.seed, ty, 2.0)).outputs["Value"]
    warp_c = nb.hsv(I["Warp Color"], 0.5, 1.0, nb.add(1.0, nb.mul(nb.sub(wr, 0.5), nb.mul(I["Thread Variation"], 0.5))))
    weft_c = nb.hsv(I["Weft Color"], 0.5, 1.0, nb.add(1.0, nb.mul(nb.sub(fr, 0.5), nb.mul(I["Thread Variation"], 0.5))))
    col = nb.mix(weft_c, warp_c, warp_top)
    shade = nb.add(0.55, nb.mul(h, 0.45))
    col = nb.mix(col, (0, 0, 0), nb.one_minus(shade))
    fuzz = nb.nfac(c.vs, nb.mul(f, 3.0), 3.0, 0.7)
    col = nb.mix(col, nb.mix(col, (1, 1, 1), 0.3), nb.mul(nb.smooth(fuzz, 0.6, 0.85), I["Fuzz"]))
    h = nb.add(nb.add(0.55, nb.mul(h, 0.4)), nb.mul(fuzz, nb.mul(I["Fuzz"], 0.05)))
    return {"Color": col, "Roughness": I["Roughness"], "Height": h, "Sheen": I["Sheen"]}


@pattern("leather", "Leather", 3, [
    ("Color", "color", "#5b3520"),
    ("Crease Color", "color", "#2a170d"),
    ("Grain Size", "float", 0.003, 0.0003, 0.05, D),
    ("Grain Depth", "float", 0.6, 0.0, 1.0, F),
    ("Wrinkles", "float", 0.3, 0.0, 1.0, F),
    ("Roughness", "float", 0.5, 0.0, 1.0, F),
    ("Sheen", "float", 0.15, 0.0, 1.0, F),
], ["Color", "Crease Color", "Grain Size", "Grain Depth"])
def build_leather(nb, I, c):
    inv = nb.div(1.0, I["Grain Size"])
    warp = nb.noise(c.vs, nb.mul(inv, 0.3), 2.0).outputs["Color"]
    pv = nb.vadd(c.vs, nb.vscale(warp, nb.mul(I["Grain Size"], 0.8)))
    e = nb.voronoi(pv, inv, "DISTANCE_TO_EDGE").outputs["Distance"]
    crease = nb.one_minus(nb.smooth(e, 0.0, 0.12))
    wr = nb.wave(c.vs, 6.0, 6.0, 3.0, 2.0, direction="DIAGONAL")
    wr = nb.mul(nb.pow(wr.outputs["Fac"], 8.0), I["Wrinkles"])
    col = nb.mix(I["Color"], I["Crease Color"], nb.clamp01(nb.add(nb.mul(crease, 0.6), wr)))
    col = mottle(nb, col, c.vs, 8.0, 0.4, 0.8)
    h = nb.sub(1.0, nb.add(nb.mul(crease, I["Grain Depth"]), nb.mul(wr, 0.6)))
    rough = nb.add(I["Roughness"], nb.mul(crease, 0.15))
    return {"Color": col, "Roughness": rough, "Height": nb.clamp01(h), "Sheen": I["Sheen"]}


@pattern("skin", "Skin", 3, [
    ("Color", "color", "#d9a58a"),
    ("Blotch Color", "color", "#c4765f"),
    ("Pores", "float", 0.5, 0.0, 1.0, F),
    ("Pore Size", "float", 0.0012, 0.0002, 0.01, D),
    ("Blotches", "float", 0.3, 0.0, 1.0, F),
    ("Wrinkles", "float", 0.15, 0.0, 1.0, F),
    ("Roughness", "float", 0.45, 0.0, 1.0, F),
    ("Subsurface", "float", 0.8, 0.0, 1.0, F),
], ["Color", "Blotch Color", "Pores", "Blotches"])
def build_skin(nb, I, c):
    pv = nb.voronoi(c.vs, nb.div(1.0, I["Pore Size"]))
    pores = nb.one_minus(nb.smooth(pv.outputs["Distance"], 0.05, 0.22))
    pores = nb.mul(pores, I["Pores"])
    bl = nb.nfac(c.vs, 18.0, 4.0, 0.6)
    col = nb.mix(I["Color"], I["Blotch Color"], nb.mul(nb.smooth(bl, 0.5, 0.75), I["Blotches"]))
    fr = nb.voronoi(c.vs, 220.0)
    freckle = nb.one_minus(nb.smooth(fr.outputs["Distance"], 0.0, 0.2))
    col = nb.mix(col, nb.mix(col, (0.2, 0.08, 0.04), 0.4), nb.mul(freckle, nb.mul(I["Blotches"], 0.3)))
    wr = nb.voronoi(nb.vmul(c.vs, (1.0, 6.0, 1.0)), 60.0, "DISTANCE_TO_EDGE").outputs["Distance"]
    wr = nb.mul(nb.one_minus(nb.smooth(wr, 0.0, 0.05)), I["Wrinkles"])
    h = nb.sub(1.0, nb.add(nb.mul(pores, 0.5), nb.mul(wr, 0.4)))
    rough = nb.add(I["Roughness"], nb.mul(pores, 0.15))
    return {"Color": col, "Roughness": rough, "Height": h, "Subsurface": I["Subsurface"],
            "IOR": 1.4}


# ---------------------------------------------------------------------------
# ground
# ---------------------------------------------------------------------------

@pattern("ground", "Ground", 3, [
    ("Soil Color", "color", "#5a4434"),
    ("Soil Color 2", "color", "#3b2c22"),
    ("Mottling", "float", 0.6, 0.0, 1.0, F),
    ("Pebbles", "float", 0.35, 0.0, 1.0, F),
    ("Pebble Size", "float", 0.02, 0.002, 0.5, D),
    ("Pebble Color", "color", "#8c8378"),
    ("Pebble Color 2", "color", "#5d5650"),
    ("Ripples", "float", 0.0, 0.0, 1.0, F),
    ("Ripple Spacing", "float", 0.08, 0.005, 2.0, D),
    ("Clumps", "float", 0.5, 0.0, 1.0, F),
    ("Grass", "float", 0.0, 0.0, 1.0, F),
    ("Grass Color", "color", "#4c6b2a"),
    ("Roughness", "float", 0.92, 0.0, 1.0, F),
], ["Soil Color", "Soil Color 2", "Pebbles", "Pebble Size", "Grass"],
    "Soil, gravel, sand, mud and grass")
def build_ground(nb, I, c):
    n1 = nb.nfac(c.vs, 3.0, 6.0, 0.6)
    n2 = nb.nfac(c.vs, 40.0, 4.0, 0.6)
    col = nb.mix(I["Soil Color"], I["Soil Color 2"],
                 nb.mul(nb.clamp01(nb.add(nb.smooth(n1, 0.35, 0.7), nb.mul(nb.sub(n2, 0.5), 0.8))), I["Mottling"]))
    # pebbles
    inv = nb.div(1.0, I["Pebble Size"])
    warp = nb.noise(c.vs, nb.mul(inv, 0.6), 2.0).outputs["Color"]
    pv = nb.vadd(c.vs, nb.vscale(warp, nb.mul(I["Pebble Size"], 0.5)))
    vor = nb.voronoi(pv, inv)
    pr, pg, pb = nb.sep(vor.outputs["Color"])
    rad = 0.45
    present = lt(nb, pg, I["Pebbles"])
    dn = nb.div(vor.outputs["Distance"], rad)
    dome = nb.clamp01(nb.one_minus(nb.mul(dn, dn)))
    peb = nb.mul(nb.one_minus(nb.smooth(dn, 0.85, 1.0)), present)
    pcol = nb.mix(I["Pebble Color"], I["Pebble Color 2"], pr)
    pcol = nb.hsv(pcol, nb.add(0.48, nb.mul(pb, 0.04)), 1.0, nb.add(0.75, nb.mul(pb, 0.5)))
    pcol = mottle(nb, pcol, c.vs, nb.mul(inv, 3.0), 0.4, 0.8)
    col = nb.mix(col, pcol, peb)
    # ripples (sand)
    rp = nb.wave(nb.vmul(c.vs, (1.0, 1.0, 0.0)), nb.div(1.0, I["Ripple Spacing"]), 3.0, 2.0, 1.0,
                 direction="Y", profile="SAW")
    ripple = nb.mul(rp.outputs["Fac"], I["Ripples"])
    col = nb.mix(col, nb.mix(col, (0, 0, 0), 0.25), nb.mul(nb.smooth(rp.outputs["Fac"], 0.6, 1.0), I["Ripples"]))
    # grass
    gn = nb.nfac(c.vs, 2.5, 4.0, 0.6)
    gmask = nb.smooth(nb.add(gn, nb.mul(I["Grass"], 1.2)), 1.05, 1.25)
    blades = nb.smooth(nb.nfac(nb.vmul(c.vs, (1.0, 1.0, 0.3)), 120.0, 3.0, 0.7), 0.25, 0.75)
    gcol = nb.hsv(I["Grass Color"], nb.add(0.47, nb.mul(blades, 0.06)), 1.0, nb.add(0.5, blades))
    col = nb.mix(col, gcol, nb.mul(gmask, nb.smooth(blades, 0.15, 0.4)))
    h = nb.add(0.6, nb.add(nb.mul(nb.mul(n1, I["Clumps"]), 0.25), nb.mul(n2, 0.1)))
    h = nb.add(h, nb.mul(ripple, 0.2))
    h = nb.max(h, nb.mul(peb, nb.add(0.7, nb.mul(dome, 0.3))))
    h = nb.add(h, nb.mul(gmask, nb.mul(blades, 0.1)))
    rough = nb.sub(I["Roughness"], nb.mul(peb, 0.2))
    return {"Color": col, "Roughness": rough, "Height": nb.clamp01(h)}


# ---------------------------------------------------------------------------
# roofing
# ---------------------------------------------------------------------------

@pattern("roof_tiles", "Roof Tiles", 2, [
    ("Tile Width", "float", 0.25, 0.02, 3.0, D),
    ("Exposure", "float", 0.30, 0.02, 3.0, D),
    ("Gap", "float", 0.004, 0.0, 0.05, D),
    ("Row Offset", "float", 0.5, 0.0, 1.0, F),
    ("Barrel", "float", 0.0, 0.0, 1.0, F),
    ("Overlap", "float", 0.8, 0.0, 1.0, F),
    ("Rounded Ends", "float", 0.0, 0.0, 1.0, F),
    ("Color A", "color", "#9c4a2f"),
    ("Color B", "color", "#7a3522"),
    ("Color Variation", "float", 0.35, 0.0, 1.0, F),
    ("Roughness", "float", 0.7, 0.0, 1.0, F),
], ["Color A", "Color B", "Tile Width", "Exposure", "Barrel", "Overlap"],
    "Clay tiles, slate, shingles and shakes. Courses run along X, roof slopes along Y")
def build_roof_tiles(nb, I, c):
    w, ex = I["Tile Width"], I["Exposure"]
    row = nb.floor(nb.div(c.y, ex))
    xo = nb.add(nb.div(c.x, w), nb.mul(row, I["Row Offset"]))
    col_i = nb.floor(xo)
    u = nb.fract(xo)
    v = nb.fract(nb.div(c.y, ex))
    # rounded / beaver tail ends: push the visible butt edge down in the tile corners
    arc = nb.one_minus(nb.sin(nb.mul(u, PI)))
    v_edge = nb.mul(nb.pow(arc, 2.0), nb.mul(I["Rounded Ends"], 0.35))
    cut = lt(nb, v, v_edge)                      # inside cut-out: show the row below
    row = nb.sub(row, cut)
    xo = nb.add(nb.div(c.x, w), nb.mul(row, I["Row Offset"]))
    col_i = nb.floor(xo)
    u = nb.fract(xo)
    v = nb.lerp(v, nb.add(v, 1.0), cut)
    cell = nb.comb(col_i, row, c.seed)
    wn = nb.white(cell)
    du = nb.mul(nb.min(u, nb.one_minus(u)), w)
    joint = nb.one_minus(nb.smooth(du, nb.mul(I["Gap"], 0.5), nb.add(nb.mul(I["Gap"], 0.5), 0.002)))
    joint = nb.mul(joint, nb.one_minus(I["Barrel"]))
    barrel = nb.pow(nb.sin(nb.mul(u, PI)), 0.6)
    vv = nb.clamp01(v)
    lap = nb.one_minus(nb.mul(vv, 0.5))
    shape = nb.lerp(1.0, barrel, I["Barrel"])
    h = nb.mul(nb.lerp(1.0, lap, I["Overlap"]), nb.add(0.4, nb.mul(shape, 0.6)))
    butt = nb.smooth(v, 0.0, 0.03)
    h = nb.mul(h, nb.add(0.7, nb.mul(butt, 0.3)))
    h = nb.lerp(h, 0.2, joint)
    col = nb.mix(I["Color A"], I["Color B"], wn.outputs["Value"])
    col = vary_color(nb, col, wn.outputs["Color"], I["Color Variation"])
    col = mottle(nb, col, nb.vadd(c.vs, nb.vscale(wn.outputs["Color"], 20.0)), 15.0, 0.4)
    shadow = nb.mul(nb.one_minus(nb.smooth(v, 0.0, 0.12)), nb.mul(I["Overlap"], 0.5))
    col = nb.mix(col, (0, 0, 0), nb.max(shadow, nb.mul(joint, 0.8)))
    return {"Color": col, "Roughness": I["Roughness"], "Height": h}


@pattern("sheet_roof", "Sheet Metal", 2, [
    ("Pitch", "float", 0.076, 0.005, 2.0, D),
    ("Standing Seam", "float", 0.0, 0.0, 1.0, F),
    ("Seam Width", "float", 0.025, 0.001, 0.2, D),
    ("Color", "color", "#bfc3c4"),
    ("Metallic", "float", 1.0, 0.0, 1.0, F),
    ("Roughness", "float", 0.35, 0.0, 1.0, F),
    ("Spangle", "float", 0.3, 0.0, 1.0, F),
    ("Minor Ribs", "float", 0.0, 0.0, 1.0, F),
], ["Color", "Pitch", "Standing Seam", "Metallic", "Roughness"],
    "Corrugated, box-rib and standing-seam sheeting, ribs run along Y")
def build_sheet_roof(nb, I, c):
    s = nb.div(c.x, I["Pitch"])
    corr = nb.add(0.5, nb.mul(nb.sin(nb.mul(s, 2 * PI)), 0.5))
    f = nb.fract(s)
    d = nb.mul(nb.min(f, nb.one_minus(f)), I["Pitch"])
    seam = nb.one_minus(nb.smooth(d, nb.mul(I["Seam Width"], 0.2), nb.mul(I["Seam Width"], 0.5)))
    ribs = nb.mul(nb.add(0.5, nb.mul(nb.sin(nb.mul(s, 8 * PI)), 0.5)), nb.mul(I["Minor Ribs"], 0.08))
    h = nb.lerp(corr, nb.add(0.75, nb.mul(seam, 0.25)), I["Standing Seam"])
    h = nb.sub(h, ribs)
    sv = nb.voronoi(c.vs, 70.0)
    sr, _, _ = nb.sep(sv.outputs["Color"])
    sp = nb.mul(nb.sub(sr, 0.5), I["Spangle"])
    col = nb.hsv(I["Color"], 0.5, 1.0, nb.add(1.0, nb.mul(sp, 0.4)))
    rough = nb.clamp01(nb.add(I["Roughness"], nb.mul(sp, 0.3)))
    return {"Color": col, "Roughness": rough, "Metallic": I["Metallic"], "Height": h}


# ---------------------------------------------------------------------------
# glass, liquids, finishes
# ---------------------------------------------------------------------------

@pattern("glass", "Glass & Liquid", 3, [
    ("Color", "color", "#f2f7f7"),
    ("Roughness", "float", 0.0, 0.0, 1.0, F),
    ("IOR", "float", 1.5, 1.0, 3.0),
    ("Frost", "float", 0.0, 0.0, 1.0, F),
    ("Ripples", "float", 0.0, 0.0, 1.0, F),
    ("Ripple Scale", "float", 0.25, 0.005, 10.0, D),
    ("Cracks", "float", 0.0, 0.0, 1.0, F),
    ("Bubbles", "float", 0.0, 0.0, 1.0, F),
], ["Color", "Roughness", "IOR", "Frost", "Ripples"],
    "Glass, gems, water and ice")
def build_glass(nb, I, c):
    rp = nb.noise(c.vs, nb.div(1.0, I["Ripple Scale"]), 3.0, 0.5)
    ripple = nb.mul(rp.outputs["Fac"], I["Ripples"])
    fr = nb.nfac(c.vs, 600.0, 2.0, 0.6)
    frost = nb.mul(nb.add(0.6, nb.mul(fr, 0.4)), I["Frost"])
    cv = nb.voronoi(nb.vadd(c.vs, nb.vscale(nb.noise(c.vs, 4.0).outputs["Color"], 0.05)), 6.0,
                    "DISTANCE_TO_EDGE").outputs["Distance"]
    crack = nb.mul(nb.one_minus(nb.smooth(cv, 0.0, 0.015)), I["Cracks"])
    bv = nb.voronoi(c.vs, 80.0)
    bub = nb.mul(nb.one_minus(nb.smooth(bv.outputs["Distance"], 0.08, 0.12)), I["Bubbles"])
    col = nb.mix(I["Color"], (1, 1, 1), nb.max(crack, nb.mul(bub, 0.5)))
    rough = nb.clamp01(nb.add(nb.add(I["Roughness"], frost), nb.mul(crack, 0.3)))
    h = nb.sub(1.0, nb.add(nb.mul(ripple, 0.6), nb.add(nb.mul(crack, 0.4), nb.mul(fr, nb.mul(I["Frost"], 0.05)))))
    return {"Color": col, "Roughness": rough, "Height": nb.clamp01(h), "Transmission": 1.0,
            "IOR": I["IOR"]}


@pattern("plaster", "Plaster & Paint", 3, [
    ("Color", "color", "#e8e2d6"),
    ("Color 2", "color", "#d6cdbd"),
    ("Trowel", "float", 0.4, 0.0, 1.0, F),
    ("Stucco", "float", 0.0, 0.0, 1.0, F),
    ("Stucco Size", "float", 0.01, 0.001, 0.2, D),
    ("Orange Peel", "float", 0.2, 0.0, 1.0, F),
    ("Fibers", "float", 0.0, 0.0, 1.0, F),
    ("Gloss Variation", "float", 0.2, 0.0, 1.0, F),
    ("Roughness", "float", 0.85, 0.0, 1.0, F),
], ["Color", "Color 2", "Trowel", "Stucco", "Roughness"],
    "Plaster, stucco, painted walls and paper")
def build_plaster(nb, I, c):
    tw = nb.noise(c.vs, 3.0, 3.0, 0.5, 2.5)
    trowel = nb.mul(nb.smooth(tw.outputs["Fac"], 0.35, 0.65), I["Trowel"])
    col = nb.mix(I["Color"], I["Color 2"], trowel)
    st = nb.nfac(c.vs, nb.div(1.0, I["Stucco Size"]), 3.0, 0.6)
    stucco = nb.mul(nb.smooth(st, 0.5, 0.7), I["Stucco"])
    peel = nb.nfac(c.vs, 600.0, 1.0, 0.5)
    fib = nb.nfac(nb.vmul(c.vs, (1.0, 25.0, 1.0)), 40.0, 3.0, 0.6, 1.0)
    fib2 = nb.nfac(nb.vmul(c.vs, (25.0, 1.0, 1.0)), 40.0, 3.0, 0.6, 1.0)
    fibers = nb.mul(nb.max(nb.smooth(fib, 0.6, 0.8), nb.smooth(fib2, 0.6, 0.8)), I["Fibers"])
    col = nb.mix(col, nb.mix(col, (0, 0, 0), 0.12), fibers)
    col = nb.mix(col, nb.mix(col, (1, 1, 1), 0.15), nb.mul(stucco, 0.6))
    rough = nb.add(I["Roughness"], nb.mul(nb.sub(tw.outputs["Fac"], 0.5), I["Gloss Variation"]))
    h = nb.add(nb.mul(stucco, 0.25), nb.mul(peel, nb.mul(I["Orange Peel"], 0.06)))
    h = nb.add(h, nb.mul(trowel, 0.04))
    h = nb.add(nb.add(h, nb.mul(fibers, 0.04)), 0.7)
    return {"Color": col, "Roughness": nb.clamp01(rough), "Height": nb.clamp01(h)}


# ---------------------------------------------------------------------------
# group construction
# ---------------------------------------------------------------------------

def build_pattern_group(key):
    p = PATTERNS[key]
    name = p.group_name
    if nodekit.group_is_current(name):
        return bpy_group(name)
    ng, nb, gin, gout = new_group(name, COMMON + p.inputs, OUTPUTS, p.simple)
    ng["pp_kind"] = "pattern"
    ng["pp_key"] = key
    I = {s.name: s for s in gin.outputs if s.name}
    ctx = Ctx(nb, I)
    out = p.build(nb, I, ctx)
    for oname, _ in OUTPUTS:
        v = out.get(oname, OUTPUT_DEFAULTS.get(oname))
        if v is None:
            continue
        if isinstance(v, (int, float)):
            gout.inputs[oname].default_value = v
        else:
            nb.link(v, gout.inputs[oname])
    auto_layout(ng)
    return ng


def build_box_group(key):
    """Wrap a planar pattern in a 3-axis box projection."""
    p = PATTERNS[key]
    inner = build_pattern_group(key)
    name = f"{p.group_name} (Box)"
    if nodekit.group_is_current(name):
        return bpy_group(name)
    inputs = COMMON[:1] + [("Normal", "vector", (0, 0, 1)),
                           ("Box Blend", "float", 0.15, 0.0, 1.0, F)] + COMMON[1:] + p.inputs
    ng, nb, gin, gout = new_group(name, inputs, OUTPUTS, p.simple)
    ng["pp_kind"] = "pattern"
    ng["pp_key"] = key
    I = {s.name: s for s in gin.outputs if s.name}
    x, y, z = nb.sep(I["Vector"])
    projections = [nb.comb(y, z, x), nb.comb(x, z, y), nb.comb(x, y, z)]
    an = nb.vmath("ABSOLUTE", I["Normal"])
    nx, ny, nz = nb.sep(an)
    sharp = nb.lerp(40.0, 1.0, I["Box Blend"])
    # divide by the largest component first so the dominant weight is exactly
    # 1: raw pow(n, 34) underflows to ~1e-6 on diagonals and any epsilon
    # would then swamp the sum (weights summing to ~0.5 halves IOR/roughness)
    nmax = nb.max(nb.max(nx, ny), nb.max(nz, 1e-6))
    ws = [nb.pow(nb.div(n, nmax), sharp) for n in (nx, ny, nz)]
    total = nb.add(nb.add(ws[0], ws[1]), ws[2])
    ws = [nb.div(w, total) for w in ws]
    # Height switches hard to the dominant axis: averaging unrelated heights
    # across the blend band creates steep false slopes that bump-map into
    # bright mirror-like streaks.
    hx = nb.math("GREATER_THAN", nx, nb.max(ny, nz))
    hy = nb.mul(nb.one_minus(hx), nb.math("GREATER_THAN", ny, nz))
    hz = nb.one_minus(nb.max(hx, hy))
    hard = [hx, hy, hz]
    nodes = []
    for i, vec in enumerate(projections):
        g = nb.node("ShaderNodeGroup")
        g.node_tree = inner
        nb.link(vec, g.inputs["Vector"])
        for s in g.inputs:
            if s.name in I and s.name != "Vector":
                nb.link(I[s.name], s)
        nodes.append(g)
    for oname, kind in OUTPUTS:
        acc = None
        for g, w, wh in zip(nodes, ws, hard):
            o = g.outputs[oname]
            if oname == "Height":
                w = wh
            term = nb.vscale(o, w) if kind == "color" else nb.mul(o, w)
            acc = term if acc is None else (nb.vadd(acc, term) if kind == "color" else nb.add(acc, term))
        nb.link(acc, gout.inputs[oname])
    auto_layout(ng)
    return ng


def bpy_group(name):
    import bpy
    return bpy.data.node_groups[name]
