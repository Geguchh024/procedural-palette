# SPDX-License-Identifier: GPL-3.0-or-later
"""Anti-aliased 2D primitives for the viewport dial.

A single signed-distance-field shader draws everything: rounded rectangles,
rings and ring sectors (arcs), textured discs (thumbnails) and shaded balls.
All coordinates are region pixels.
"""

import math

import blf
import gpu
from gpu_extras.batch import batch_for_shader

_shader = None
_dummy = None

VERT = """
void main()
{
    v_local = loc;
    gl_Position = vec4(pos / viewport * 2.0 - 1.0, 0.0, 1.0);
}
"""

FRAG = """
void main()
{
    vec2 p = v_local;
    float mode = rect.w;
    vec4 col = color;
    float a;
    if (mode < 0.5) {
        /* rounded rectangle; shape.x > 0 draws only an outline of that width */
        vec2 q = abs(p) - rect.xy + vec2(rect.z);
        float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - rect.z;
        a = clamp(0.5 - d, 0.0, 1.0);
        if (shape.x > 0.0) {
            a *= clamp(0.5 + d + shape.x, 0.0, 1.0);
        }
    }
    else {
        /* ring between radius shape.x and shape.y, sector shape.z .. shape.w */
        float r = length(p);
        a = clamp(r - shape.x + 0.5, 0.0, 1.0) * clamp(shape.y - r + 0.5, 0.0, 1.0);
        float span = shape.w - shape.z;
        if (span < 6.28) {
            vec2 d0 = vec2(cos(shape.z), sin(shape.z));
            vec2 d1 = vec2(cos(shape.w), sin(shape.w));
            float s0 = d0.x * p.y - d0.y * p.x;
            float s1 = d1.y * p.x - d1.x * p.y;
            float s = (span <= 3.14159) ? min(s0, s1) : max(s0, s1);
            a *= clamp(0.5 + s, 0.0, 1.0);
        }
        if (mode > 2.5) {
            /* shaded ball */
            vec2 n2 = p / max(shape.y, 1.0);
            float z = sqrt(max(0.0, 1.0 - dot(n2, n2)));
            vec3 n = vec3(n2, z);
            vec3 l = normalize(vec3(-0.45, 0.55, 0.75));
            float diff = max(dot(n, l), 0.0) * 0.8 + 0.25;
            vec3 h = normalize(l + vec3(0.0, 0.0, 1.0));
            float spec = pow(max(dot(n, h), 0.0), 40.0) * 0.45;
            float rim = pow(1.0 - z, 3.0) * 0.25;
            col = vec4(col.rgb * diff + vec3(spec + rim), col.a);
        }
        else if (mode > 1.5) {
            /* textured disc */
            vec4 t = texture(image, p / (2.0 * shape.y) + 0.5);
            col = vec4(t.rgb, t.a * col.a);
        }
    }
    /* the overlay buffer is scene-linear: decode our sRGB colours/thumbnails */
    FragColor = vec4(pow(max(col.rgb, vec3(0.0)), vec3(2.2)), col.a * a);
}
"""


def shader():
    global _shader, _dummy
    if _shader is None:
        iface = gpu.types.GPUStageInterfaceInfo("pp_dial_iface")
        iface.smooth("VEC2", "v_local")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("VEC4", "color")
        info.push_constant("VEC4", "shape")
        info.push_constant("VEC4", "rect")
        info.push_constant("VEC2", "viewport")
        info.sampler(0, "FLOAT_2D", "image")
        info.vertex_in(0, "VEC2", "pos")
        info.vertex_in(1, "VEC2", "loc")
        info.vertex_out(iface)
        info.fragment_out(0, "VEC4", "FragColor")
        info.vertex_source(VERT)
        info.fragment_source(FRAG)
        _shader = gpu.shader.create_from_info(info)
        buf = gpu.types.Buffer("FLOAT", 4, [1.0, 1.0, 1.0, 1.0])
        _dummy = gpu.types.GPUTexture((1, 1), format="RGBA16F", data=buf)
    return _shader


def free():
    global _shader, _dummy
    _shader = None
    _dummy = None


def _draw(cx, cy, hw, hh, color, shape, rect, tex=None):
    sh = shader()
    pad = 1.5
    hw += pad
    hh += pad
    pos = ((cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh))
    loc = ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
    batch = batch_for_shader(sh, "TRIS", {"pos": pos, "loc": loc}, indices=((0, 1, 2), (0, 2, 3)))
    vp = gpu.state.viewport_get()
    # blf text drawing resets the blend mode, so set it for every shape
    gpu.state.blend_set("ALPHA")
    sh.bind()
    sh.uniform_float("viewport", (float(vp[2]), float(vp[3])))
    sh.uniform_float("color", color)
    sh.uniform_float("shape", shape)
    sh.uniform_float("rect", rect)
    sh.uniform_sampler("image", tex or _dummy)
    batch.draw(sh)


def rrect(cx, cy, w, h, radius, color, outline=0.0):
    """Rounded rectangle centred at (cx, cy)."""
    _draw(cx, cy, w / 2, h / 2, color, (outline, 0, 0, 0), (w / 2, h / 2, min(radius, w / 2, h / 2), 0.0))


def ring(cx, cy, r0, r1, color, a0=0.0, a1=math.tau + 1.0):
    """Ring (or disc when r0 == 0) optionally limited to angles a0..a1 (radians, CCW)."""
    _draw(cx, cy, r1, r1, color, (r0, r1, a0, a1), (0, 0, 0, 1.0))


def circle(cx, cy, r, color):
    ring(cx, cy, 0.0, r, color)


def ball(cx, cy, r, color):
    _draw(cx, cy, r, r, color, (0.0, r, 0.0, math.tau + 1.0), (0, 0, 0, 3.0))


def thumb(cx, cy, r, tex, alpha=1.0):
    _draw(cx, cy, r, r, (1, 1, 1, alpha), (0.0, r, 0.0, math.tau + 1.0), (0, 0, 0, 2.0), tex)


# -- text --------------------------------------------------------------------------

FONT = 0


def text(x, y, s, size, color, align="CENTER", shadow=True):
    blf.size(FONT, size)
    w, _ = blf.dimensions(FONT, s)
    if align == "CENTER":
        x -= w / 2
    elif align == "RIGHT":
        x -= w
    y -= size * 0.36
    if shadow:
        blf.enable(FONT, blf.SHADOW)
        blf.shadow(FONT, 3, 0.0, 0.0, 0.0, 0.7)
        blf.shadow_offset(FONT, 1, -1)
    blf.color(FONT, *color)
    blf.position(FONT, x, y, 0)
    blf.draw(FONT, s)
    if shadow:
        blf.disable(FONT, blf.SHADOW)
    return w


def text_width(s, size):
    blf.size(FONT, size)
    return blf.dimensions(FONT, s)[0]


def fit(s, size, max_w):
    if text_width(s, size) <= max_w:
        return s
    while len(s) > 2 and text_width(s + "…", size) > max_w:
        s = s[:-1]
    return s.rstrip() + "…"
