# SPDX-License-Identifier: GPL-3.0-or-later
"""Small DSL for building shader node trees from Python.

Every helper accepts either plain Python values or node sockets for its
inputs and returns an output socket, so node graphs can be written like
ordinary expressions:

    nb = NB(tree)
    v = nb.div(vec, size)
    mask = nb.smooth(nb.noise(v, 4).outputs["Fac"], 0.4, 0.6)
"""

import bpy

GROUP_VERSION = 6


def srgb_to_linear(c):
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_color(h):
    """'#RRGGBB' -> linear RGBA tuple."""
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1.0)


def to_value(v):
    if isinstance(v, str):
        return hex_color(v)
    return v


def _is_socket(v):
    return isinstance(v, bpy.types.NodeSocket)


def _available(sockets):
    for s in sockets:
        if getattr(s, "is_unavailable", not getattr(s, "enabled", True)):
            continue
        yield s


def find_input(node, key):
    if isinstance(key, int):
        return list(_available(node.inputs))[key]
    for s in _available(node.inputs):
        if s.name == key or s.identifier == key:
            return s
    raise KeyError(f"{node.bl_idname} has no input {key!r}")


def find_output(node, key=0):
    if isinstance(key, int):
        return list(_available(node.outputs))[key]
    for s in _available(node.outputs):
        if s.name == key or s.identifier == key:
            return s
    raise KeyError(f"{node.bl_idname} has no output {key!r}")


class NB:
    """Node builder bound to a node tree."""

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links

    # -- basics -----------------------------------------------------------
    def node(self, idname, inputs=None, **props):
        n = self.nodes.new(idname)
        for k, v in props.items():
            setattr(n, k, v)
        if inputs:
            for k, v in inputs.items():
                self.set(n, k, v)
        return n

    def set(self, node, key, value):
        sock = find_input(node, key)
        if value is None:
            return
        if _is_socket(value):
            self.links.new(value, sock)
            return
        value = to_value(value)
        if sock.type in {"RGBA"} and isinstance(value, (int, float)):
            value = (value, value, value, 1.0)
        elif sock.type == "RGBA" and len(value) == 3:
            value = (*value, 1.0)
        elif sock.type == "VECTOR" and isinstance(value, (int, float)):
            value = (value, value, value)
        elif sock.type == "VALUE" and isinstance(value, tuple):
            value = value[0]
        sock.default_value = value

    def link(self, out, inp):
        self.links.new(out, inp)

    def val(self, v, label=None):
        n = self.node("ShaderNodeValue")
        n.outputs[0].default_value = v
        if label:
            n.label = label
        return n.outputs[0]

    def rgb(self, color):
        n = self.node("ShaderNodeRGB")
        n.outputs[0].default_value = to_value(color)
        return n.outputs[0]

    # -- scalar math --------------------------------------------------------
    def math(self, op, a, b=None, c=None, clamp=False):
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.set(n, i, v)
        return n.outputs[0]

    def add(self, a, b, clamp=False):
        return self.math("ADD", a, b, clamp=clamp)

    def sub(self, a, b, clamp=False):
        return self.math("SUBTRACT", a, b, clamp=clamp)

    def mul(self, a, b, clamp=False):
        return self.math("MULTIPLY", a, b, clamp=clamp)

    def div(self, a, b, clamp=False):
        return self.math("DIVIDE", a, b, clamp=clamp)

    def madd(self, a, b, c, clamp=False):
        return self.math("MULTIPLY_ADD", a, b, c, clamp=clamp)

    def pow(self, a, b):
        return self.math("POWER", a, b)

    def min(self, a, b):
        return self.math("MINIMUM", a, b)

    def max(self, a, b):
        return self.math("MAXIMUM", a, b)

    def abs(self, a):
        return self.math("ABSOLUTE", a)

    def floor(self, a):
        return self.math("FLOOR", a)

    def fract(self, a):
        return self.math("FRACT", a)

    def fmod(self, a, b):
        return self.math("FLOORED_MODULO", a, b)

    def sin(self, a):
        return self.math("SINE", a)

    def clamp01(self, a):
        return self.math("ADD", a, 0.0, clamp=True)

    def one_minus(self, a):
        return self.math("SUBTRACT", 1.0, a, clamp=True)

    def lerp(self, a, b, t):
        n = self.node("ShaderNodeMix", data_type="FLOAT", clamp_factor=True)
        self.set(n, "Factor", t)
        self.set(n, "A", a)
        self.set(n, "B", b)
        return find_output(n, "Result")

    def remap(self, x, a, b, c=0.0, d=1.0, clamp=True, interp="LINEAR"):
        n = self.node("ShaderNodeMapRange", interpolation_type=interp, clamp=clamp)
        for k, v in (("Value", x), ("From Min", a), ("From Max", b),
                     ("To Min", c), ("To Max", d)):
            self.set(n, k, v)
        return find_output(n, "Result")

    def smooth(self, x, lo, hi):
        """Smoothstep from lo..hi -> 0..1."""
        return self.remap(x, lo, hi, 0.0, 1.0, True, "SMOOTHSTEP")

    def mask_thr(self, x, threshold, softness=0.05):
        """Soft threshold around a (socket or float) threshold."""
        lo = self.sub(threshold, softness)
        hi = self.add(threshold, softness)
        return self.smooth(x, lo, hi)

    # -- vector math --------------------------------------------------------
    def vmath(self, op, a, b=None, scale=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        ins = list(_available(n.inputs))
        if a is not None:
            self.set(n, 0, a)
        if b is not None and len(ins) > 1 and ins[1].type == "VECTOR":
            self.set(n, 1, b)
        if scale is not None:
            self.set(n, "Scale", scale)
        out = "Value" if op in {"DOT_PRODUCT", "LENGTH", "DISTANCE"} else "Vector"
        return find_output(n, out)

    def vadd(self, a, b):
        return self.vmath("ADD", a, b)

    def vsub(self, a, b):
        return self.vmath("SUBTRACT", a, b)

    def vmul(self, a, b):
        return self.vmath("MULTIPLY", a, b)

    def vscale(self, a, s):
        return self.vmath("SCALE", a, scale=s)

    def sep(self, v):
        n = self.node("ShaderNodeSeparateXYZ")
        self.set(n, 0, v)
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def comb(self, x=0.0, y=0.0, z=0.0):
        n = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.set(n, i, v)
        return n.outputs[0]

    # -- colour -------------------------------------------------------------
    def mix(self, a, b, fac, blend="MIX", clamp=False):
        n = self.node("ShaderNodeMix", data_type="RGBA", blend_type=blend,
                      clamp_factor=True, clamp_result=clamp)
        self.set(n, "Factor", fac)
        self.set(n, "A", a)
        self.set(n, "B", b)
        return find_output(n, "Result")

    def hsv(self, color, h=0.5, s=1.0, v=1.0, fac=1.0):
        n = self.node("ShaderNodeHueSaturation")
        for k, x in (("Hue", h), ("Saturation", s), ("Value", v),
                     ("Fac", fac), ("Color", color)):
            self.set(n, k, x)
        return n.outputs[0]

    def ramp(self, fac, stops, interp="LINEAR"):
        n = self.node("ShaderNodeValToRGB")
        cr = n.color_ramp
        cr.interpolation = interp
        while len(cr.elements) > len(stops):
            cr.elements.remove(cr.elements[-1])
        while len(cr.elements) < len(stops):
            cr.elements.new(0.5)
        for el, (pos, col) in zip(cr.elements, stops):
            el.position = pos
            el.color = to_value(col) if not isinstance(col, (int, float)) else (col, col, col, 1)
        self.set(n, "Fac", fac)
        return n.outputs["Color"]

    # -- textures -------------------------------------------------------------
    def _detail(self, n, detail):
        if isinstance(detail, (int, float)):
            n["pp_detail"] = float(detail)

    def noise(self, vec, scale=5.0, detail=2.0, rough=0.5, dist=0.0,
              dims="3D", ntype="FBM", lac=2.0, w=None):
        n = self.node("ShaderNodeTexNoise", noise_dimensions=dims)
        if hasattr(n, "noise_type"):
            n.noise_type = ntype
        for k, v in (("Vector", vec), ("Scale", scale), ("Detail", detail),
                     ("Roughness", rough), ("Distortion", dist), ("Lacunarity", lac)):
            self.set(n, k, v)
        if w is not None:
            self.set(n, "W", w)
        self._detail(n, detail)
        return n

    def nfac(self, *args, **kw):
        return self.noise(*args, **kw).outputs["Fac"]

    def voronoi(self, vec, scale=5.0, feature="F1", rand=1.0, dims="3D",
                metric="EUCLIDEAN", smooth=None):
        n = self.node("ShaderNodeTexVoronoi", feature=feature,
                      voronoi_dimensions=dims, distance=metric)
        if hasattr(n, "normalize"):
            n.normalize = False
        for k, v in (("Vector", vec), ("Scale", scale), ("Randomness", rand)):
            self.set(n, k, v)
        if smooth is not None:
            self.set(n, "Smoothness", smooth)
        return n

    def white(self, vec, dims="3D"):
        n = self.node("ShaderNodeTexWhiteNoise", noise_dimensions=dims)
        self.set(n, "Vector", vec)
        return n

    def wave(self, vec, scale=1.0, dist=0.0, detail=2.0, dscale=1.0,
             wtype="BANDS", direction="X", profile="SIN", phase=0.0):
        n = self.node("ShaderNodeTexWave", wave_type=wtype, wave_profile=profile)
        if wtype == "BANDS":
            n.bands_direction = direction
        else:
            n.rings_direction = direction if direction in {"X", "Y", "Z"} else "SPHERICAL"
        for k, v in (("Vector", vec), ("Scale", scale), ("Distortion", dist),
                     ("Detail", detail), ("Detail Scale", dscale), ("Phase Offset", phase)):
            self.set(n, k, v)
        self._detail(n, detail)
        return n

    # -- composite helpers -------------------------------------------------------
    def grid(self, x, y, w, h, gap, offset=0.5, shift_rand=0.0, bevel=0.005, seed=0.0):
        """Running-bond cell grid on the XY plane, sizes in metres.

        Returns dict with: rand (colour, 3 random channels per cell),
        rand1 (float), u, v (0..1 inside cell), mortar (1 in the joint),
        profile (0 at joint rising to 1 after `bevel`), row, col.
        """
        row = self.floor(self.div(y, h))
        row_rand = self.white(self.comb(row, seed, 7.31), dims="3D").outputs["Value"]
        shift = self.add(self.mul(row, offset), self.mul(row_rand, shift_rand))
        xo = self.div(x, w)
        xo = self.add(xo, shift)
        col = self.floor(xo)
        u = self.fract(xo)
        v = self.fract(self.div(y, h))
        cell = self.comb(col, row, seed)
        wn = self.white(cell, dims="3D")
        du = self.mul(self.min(u, self.one_minus(u)), w)
        dv = self.mul(self.min(v, self.one_minus(v)), h)
        d = self.min(du, dv)
        half = self.mul(gap, 0.5)
        mortar = self.one_minus(self.smooth(d, self.mul(half, 0.85), self.add(half, 0.0005)))
        profile = self.smooth(d, half, self.add(half, bevel))
        return dict(rand=wn.outputs["Color"], rand1=wn.outputs["Value"], u=u, v=v,
                    mortar=mortar, profile=profile, row=row, col=col, cell=cell, dist=d)


# -- node groups -------------------------------------------------------------------

SOCKET_TYPES = {
    "float": "NodeSocketFloat",
    "color": "NodeSocketColor",
    "vector": "NodeSocketVector",
    "int": "NodeSocketInt",
}


def new_group(name, inputs, outputs, simple=None):
    """Create (or recreate) a shader node group.

    inputs: list of (name, kind, default, min, max[, subtype])
    outputs: list of (name, kind)
    Returns (group, NB, group_input_node, group_output_node).
    """
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    else:
        ng.nodes.clear()
        ng.interface.clear()
    for spec in inputs:
        sname, kind, default = spec[0], spec[1], spec[2]
        s = ng.interface.new_socket(sname, in_out="INPUT", socket_type=SOCKET_TYPES[kind])
        if kind == "float":
            lo, hi = spec[3], spec[4]
            s.min_value, s.max_value = lo, hi
            if len(spec) > 5 and spec[5]:
                s.subtype = spec[5]
        if kind == "vector" and sname in {"Vector", "Normal"}:
            s.hide_value = True
        s.default_value = to_value(default)
    for oname, kind in outputs:
        ng.interface.new_socket(oname, in_out="OUTPUT", socket_type=SOCKET_TYPES[kind])
    nb = NB(ng)
    gin = nb.node("NodeGroupInput")
    gout = nb.node("NodeGroupOutput")
    ng["pp_version"] = GROUP_VERSION
    ng["pp_simple"] = list(simple or [])
    return ng, nb, gin, gout


def group_is_current(name):
    ng = bpy.data.node_groups.get(name)
    return ng is not None and ng.get("pp_version") == GROUP_VERSION and len(ng.nodes) > 2


def auto_layout(tree, dx=220, dy=190):
    """Column layout by longest-path depth so generated groups stay readable."""
    nodes = list(tree.nodes)
    incoming = {n: [] for n in nodes}
    for l in tree.links:
        incoming[l.to_node].append(l.from_node)
    depth = {}

    def d(n, stack=()):
        if n in depth:
            return depth[n]
        if n in stack:
            return 0
        val = 0 if not incoming[n] else 1 + max(d(p, stack + (n,)) for p in incoming[n])
        depth[n] = val
        return val

    cols = {}
    for n in nodes:
        cols.setdefault(d(n), []).append(n)
    for c, ns in cols.items():
        for i, n in enumerate(ns):
            n.location = (c * dx, -i * dy)
