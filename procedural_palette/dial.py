# SPDX-License-Identifier: GPL-3.0-or-later
"""The viewport dial: a radial, GPU-drawn material picker.

Layout (centre outwards):
  * knob      - drag (or scroll) to change the page's active value
  * page ring - Material, Style, Pattern, Colour, Detail, Weather, ...
  * item ring - thumbnails / swatches / parameters for the current page
  * header    - title, drag to move, "-" to minimise
  * buttons   - Random, Save, Panel, Bake, Close

Mouse events outside the dial pass straight through to the viewport, so you
can keep orbiting and selecting while it is open. Typing while hovering the
dial searches all styles.
"""

import colorsys
import math

import bpy

from . import dial_draw as dd
from . import finish, library, material, presets, thumbs

ACCENT = (0.96, 0.54, 0.18, 1.0)
ACCENT_SOFT = (0.96, 0.54, 0.18, 0.35)
BG = (0.075, 0.075, 0.08, 0.93)
PANEL = (0.135, 0.135, 0.145, 0.97)
PANEL_HOVER = (0.2, 0.2, 0.215, 0.97)
TEXT = (0.88, 0.88, 0.9, 1.0)
TEXT_DIM = (0.74, 0.74, 0.77, 1.0)
WHITE = (1, 1, 1, 1)

PAGES = ["Material", "Style", "Pattern", "Colour", "Detail", "Weather", "Relief", "Mapping", "Saved"]
NEEDS_MATERIAL = {"Pattern", "Colour", "Detail", "Weather", "Relief", "Mapping"}
BUTTONS = ["Random", "Save", "Panel", "Bake", "Close"]

WEATHER_ITEMS = [
    ("Edge Wear", (0.78, 0.76, 0.72)), ("Rust", (0.62, 0.28, 0.1)),
    ("Dirt", (0.28, 0.22, 0.16)), ("Grime Streaks", (0.2, 0.18, 0.15)),
    ("Moss", (0.33, 0.45, 0.14)), ("Dust", (0.7, 0.66, 0.58)),
    ("Snow", (0.95, 0.97, 1.0)), ("Wetness", (0.25, 0.4, 0.6)),
    ("Puddles", (0.35, 0.55, 0.8)),
]
MAPPING_LABELS = {"OBJECT": "Real Size", "GENERATED": "Stretch", "WORLD": "World", "UV": "UV Map"}

# remembered between openings
_state = {"page": 0, "scale": 1.0, "offset": None, "minimized": False}
_running = None


def to_display(c):
    return tuple(max(0.0, x) ** (1 / 2.2) for x in c[:3]) + (1.0,)


# ---------------------------------------------------------------------------
# knob parameters
# ---------------------------------------------------------------------------

def _fmt_mult(v):
    return f"×{v:.2f}"


def _fmt_dist(v):
    if v < 0.01:
        return f"{v * 1000:.1f} mm"
    if v < 1.0:
        return f"{v * 100:.1f} cm"
    return f"{v:.2f} m"


def _fmt_pct(v):
    return f"{v * 100:.0f}%"


def _fmt_num(v):
    return f"{v:.2f}" if abs(v) < 100 else f"{v:.0f}"


def _fmt_deg(v):
    return f"{math.degrees(v):.0f}°"


class Knob:
    def __init__(self, label, get, set_, lo, hi, log=False, fmt=_fmt_num, undo=None):
        self.label = label
        self.get = get
        self.set = set_
        self.lo = lo
        self.hi = hi
        self.log = log
        self.fmt = fmt
        self.undo = undo or label

    def clamp(self, v):
        return min(self.hi, max(self.lo, v))

    def t(self):
        v = self.get()
        if self.log:
            return math.log(max(v, self.lo) / self.lo) / math.log(self.hi / self.lo)
        return (v - self.lo) / (self.hi - self.lo) if self.hi > self.lo else 0.0

    def drag(self, v0, d):
        """d: drag distance in 'knob units' (≈ 1 per 200 px)."""
        if self.log:
            return self.clamp(max(v0, self.lo) * (4.0 ** d))
        return self.clamp(v0 + d * (self.hi - self.lo) * 0.5)

    def step(self, direction):
        v = self.get()
        if self.log:
            self.set(self.clamp(max(v, self.lo) * (1.06 ** direction)))
        else:
            self.set(self.clamp(v + direction * (self.hi - self.lo) / 50.0))


def _iface(node, name):
    for it in node.node_tree.interface.items_tree:
        if getattr(it, "in_out", None) == "INPUT" and it.name == name:
            return it
    return None


def socket_knob(node, name, label=None):
    if node is None or name not in node.inputs:
        return None
    sock = node.inputs[name]
    it = _iface(node, name)
    lo = getattr(it, "min_value", 0.0)
    hi = getattr(it, "max_value", 1.0)
    subtype = getattr(it, "subtype", "NONE")
    log = name == "Size" or (subtype != "FACTOR" and lo >= 0.0 and hi / max(lo, 1e-4) > 50.0)
    if log:
        lo = max(lo, hi * 1e-4)
    if name == "Size":
        fmt = _fmt_mult
    elif subtype == "DISTANCE":
        fmt = _fmt_dist
    elif subtype == "FACTOR":
        fmt = _fmt_pct
    else:
        fmt = _fmt_num

    def get():
        return sock.default_value

    def set_(v):
        sock.default_value = v

    return Knob(label or name, get, set_, lo, hi, log, fmt)


def rotation_knob(mapping):
    rot = mapping.inputs["Rotation"]

    def get():
        return rot.default_value[2]

    def set_(v):
        r = list(rot.default_value)
        r[2] = v
        rot.default_value = r

    return Knob("Rotation", get, set_, -math.pi, math.pi, fmt=_fmt_deg)


def brightness_knob(sock, label):
    def get():
        r, g, b, _ = sock.default_value
        return colorsys.rgb_to_hsv(max(r, 0), max(g, 0), max(b, 0))[2]

    def set_(v):
        r, g, b, a = sock.default_value
        h, s, _ = colorsys.rgb_to_hsv(max(r, 0), max(g, 0), max(b, 0))
        sock.default_value = (*colorsys.hsv_to_rgb(h, s, max(v, 0.0)), a)

    return Knob(label, get, set_, 0.0, 1.0, fmt=_fmt_pct, undo=f"{label} Brightness")


# ---------------------------------------------------------------------------
# ring items
# ---------------------------------------------------------------------------

class Item:
    __slots__ = ("key", "label", "kind", "tex", "color", "value", "active", "click", "x", "y", "r")

    def __init__(self, key, label, kind="ball", tex=None, color=(0.5, 0.5, 0.5, 1),
                 value=None, active=False, click=None):
        self.key = key
        self.label = label
        self.kind = kind          # thumb | ball | param | toggle
        self.tex = tex            # thumbnail key
        self.color = color
        self.value = value        # 0..1 arc for params
        self.active = active
        self.click = click
        self.x = self.y = self.r = 0.0


# ---------------------------------------------------------------------------
# operator
# ---------------------------------------------------------------------------

class PP_OT_dial(bpy.types.Operator):
    """Open the Procedural Palette dial in the viewport"""
    bl_idname = "pp.dial"
    bl_label = "Palette Dial"
    bl_options = {"REGISTER"}

    # -- lifecycle ----------------------------------------------------------
    def invoke(self, context, event):
        global _running
        if _running is not None:
            _running.finish = True   # second press closes the open dial
            return {"CANCELLED"}
        if context.area is None or context.area.type != "VIEW_3D":
            area = next((a for a in context.screen.areas if a.type == "VIEW_3D"), None)
            if area is None:
                self.report({"WARNING"}, "Needs a 3D Viewport")
                return {"CANCELLED"}
        else:
            area = context.area
        self.area_ptr = area.as_pointer()
        self.finish = False
        self.page = _state["page"]
        self.sel = {}
        self.browse_family = None
        self.search = ""
        self.hover = None          # ("item", i) | ("page", i) | ("knob",) | ("button", i) | ("header",) | ("min",)
        self.drag = None           # ("knob", x, y, v0, knob) | ("move", x, y, ox, oy)
        self.items = []
        self.mouse = (0, 0)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_overlay, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        _running = self
        self._tag()
        return {"RUNNING_MODAL"}

    def _end(self, context):
        global _running
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
            self._handle = None
        _state["page"] = self.page
        try:
            context.window.cursor_set("DEFAULT")
        except (AttributeError, ReferenceError):
            pass
        _running = None
        self._tag()

    # -- context helpers --------------------------------------------------------
    def _area(self):
        for win in bpy.context.window_manager.windows:
            for a in win.screen.areas:
                if a.as_pointer() == self.area_ptr:
                    return a
        return None

    def _region(self):
        a = self._area()
        if a is None:
            return None
        return next((r for r in a.regions if r.type == "WINDOW"), None)

    def _tag(self):
        a = self._area()
        if a is not None:
            a.tag_redraw()

    @staticmethod
    def _mat():
        obj = bpy.context.object
        mat = obj.active_material if obj else None
        return mat if material.is_pp(mat) else None

    # -- geometry -------------------------------------------------------------------
    def _scale(self):
        return bpy.context.preferences.system.ui_scale * _state["scale"]

    def _geom(self, region):
        s = self._scale()
        g = {
            "s": s,
            "R": 262 * s,          # disk
            "Ri": 178 * s,         # item ring
            "r0": 74 * s,          # page ring inner
            "r1": 132 * s,         # page ring outer
            "rk": 64 * s,          # knob
        }
        if _state["offset"] is None:
            _state["offset"] = (0.5, 0.5)
        ox, oy = _state["offset"]
        cx = ox * region.width
        cy = oy * region.height
        # keep the dial inside the region
        margin = g["R"] * 0.6
        cx = min(max(cx, margin), region.width - margin)
        cy = min(max(cy, margin), region.height - margin)
        g["cx"], g["cy"] = cx, cy
        g["header"] = (cx, cy + g["R"] + 24 * s, 300 * s, 32 * s)
        g["buttons_y"] = cy - g["R"] - 26 * s
        return g

    def _button_rects(self, g):
        s = g["s"]
        w, h, gap = 78 * s, 28 * s, 8 * s
        total = len(BUTTONS) * w + (len(BUTTONS) - 1) * gap
        x0 = g["cx"] - total / 2 + w / 2
        return [(x0 + i * (w + gap), g["buttons_y"], w, h) for i in range(len(BUTTONS))]

    @staticmethod
    def _page_angle(i):
        return math.radians(90.0 - i * 360.0 / len(PAGES))

    # -- page content -----------------------------------------------------------------
    def _page_name(self):
        return PAGES[self.page]

    def _current_style(self):
        mat = self._mat()
        return presets.STYLE_BY_ID.get(mat.pp.style) if mat else None

    def build_items(self):
        mat = self._mat()
        page = self._page_name()
        if self.search:
            q = self.search.lower()
            # style names first; fall back to family names ("metal", "roof")
            hits = [st for st in presets.STYLES if q in st.name.lower()] or \
                   [st for st in presets.STYLES if q in presets.FAMILY_LABEL[st.family].lower()]
            hits = hits[:20]
            return [self._style_item(st, mat) for st in hits]
        if page in NEEDS_MATERIAL and mat is None:
            return []
        return getattr(self, "_items_" + page.lower())(mat)

    def _style_item(self, st, mat):
        return Item(st.id, st.name, "thumb", tex=thumbs.style_key(st.id),
                    active=mat is not None and mat.pp.style == st.id,
                    click=lambda st=st: self._apply_style(st))

    def _items_material(self, mat):
        cur = self.browse_family or (self._current_style().family if mat else bpy.context.scene.pp.family)
        out = []
        for key, label, _ in presets.FAMILIES:
            first = presets.styles_in(key)[0]
            short = label.split(",")[0].split(" &")[0]
            out.append(Item(key, short, "thumb", tex=thumbs.style_key(first.id), active=key == cur,
                            click=lambda key=key: self._open_family(key)))
        return out

    def _items_style(self, mat):
        fam = self.browse_family or (self._current_style().family if mat else bpy.context.scene.pp.family)
        return [self._style_item(st, mat) for st in presets.styles_in(fam)]

    def _param_items(self, node, names, page, colors=None, default=None):
        out = []
        sel = self.sel.get(page) or default
        for name in names:
            k = socket_knob(node, name)
            if k is None:
                continue
            col = (colors or {}).get(name, (0.42, 0.42, 0.45))
            out.append(Item(name, name, "param", color=(*col, 1.0), value=max(0.0, min(1.0, k.t())),
                            active=sel == name,
                            click=lambda name=name: self._select(page, name)))
        return out

    def _items_pattern(self, mat):
        surf = material.control_node(mat, material.SURFACE_NODE)
        names = ["Size"] + [s.name for s in surf.inputs
                            if s.type == "VALUE" and not s.is_linked
                            and s.name not in material.HIDDEN_INPUTS | {"Seed", "Size", "Box Blend"}]
        return self._param_items(surf, names, "Pattern", default="Size")

    def _color_sockets(self, mat):
        out = []
        surf = material.control_node(mat, material.SURFACE_NODE)
        for s in surf.inputs:
            if s.type == "RGBA" and not s.is_linked:
                out.append((material.SURFACE_NODE, s.name, s.name))
        det = material.control_node(mat, material.DETAIL_NODE)
        if det is not None:
            out.append((material.DETAIL_NODE, "Detail Color", "Detail"))
        fin = material.control_node(mat, material.FINISH_NODE)
        for amount, cname in (("Edge Wear", "Wear Color"), ("Rust", "Rust Color"), ("Dirt", "Dirt Color"),
                              ("Moss", "Moss Color"), ("Dust", "Dust Color")):
            if fin.inputs[amount].default_value > 0.0:
                out.append((material.FINISH_NODE, cname, cname))
        return out

    def _items_colour(self, mat):
        out = []
        sockets = self._color_sockets(mat)
        sel = self.sel.get("Colour") or (f"{sockets[0][0]}:{sockets[0][1]}" if sockets else None)
        for node_name, sock, label in sockets:
            node = material.control_node(mat, node_name)
            key = f"{node_name}:{sock}"
            out.append(Item(key, label, "ball", color=to_display(node.inputs[sock].default_value),
                            active=sel == key, click=lambda key=key: self._click_colour(key)))
        return out

    def _items_detail(self, mat):
        out = []
        for kind, label, _ in finish.DETAILS:
            out.append(Item(kind, label, "thumb", tex=thumbs.detail_key(kind), active=mat.pp.detail == kind,
                            click=lambda kind=kind: self._set_detail(kind)))
        det = material.control_node(mat, material.DETAIL_NODE)
        if det is not None:
            out += self._param_items(det, ["Detail Amount", "Detail Size", "Detail Depth", "Detail Sharpness"],
                                     "Detail", default="Detail Amount")
            for it in out[len(finish.DETAILS):]:
                it.label = it.label.replace("Detail ", "")
        return out

    def _items_weather(self, mat):
        fin = material.control_node(mat, material.FINISH_NODE)
        return self._param_items(fin, [n for n, _ in WEATHER_ITEMS], "Weather", dict(WEATHER_ITEMS),
                                 default="Edge Wear")

    def _items_relief(self, mat):
        fin = material.control_node(mat, material.FINISH_NODE)
        surf = material.control_node(mat, material.SURFACE_NODE)
        out = self._param_items(fin, ["Depth"], "Relief", {"Depth": (0.5, 0.42, 0.36)}, default="Depth")
        out += self._param_items(surf, [n for n in ("Box Blend", "Mortar Depth", "Grout Depth", "Bevel",
                                                    "Furrow Depth", "Grain Depth") if n in surf.inputs],
                                 "Relief")
        out.append(Item("REAL", "Real Depth", "toggle", color=(0.55, 0.55, 0.58, 1), active=mat.pp.real_depth,
                        click=self._toggle_real_depth))
        return out

    def _items_mapping(self, mat):
        out = [Item(m, MAPPING_LABELS[m], "toggle", color=(0.45, 0.5, 0.6, 1), active=mat.pp.mapping == m,
                    click=lambda m=m: self._set_mapping(m)) for m in ("OBJECT", "GENERATED", "WORLD", "UV")]
        out.append(Item("SYNC", "Sync Scale", "toggle", color=(0.4, 0.4, 0.42, 1), click=self._sync_scale))
        return out

    def _items_saved(self, mat):
        out = []
        for name, look in sorted(library.load().items(), key=lambda kv: kv[0].lower()):
            sid = look.get("style", "")
            if sid not in presets.STYLE_BY_ID:
                continue
            out.append(Item(name, name, "thumb", tex=thumbs.style_key(sid),
                            click=lambda name=name: self._apply_saved(name)))
        return out

    # -- knob per page ------------------------------------------------------------------
    def knob(self):
        mat = self._mat()
        if mat is None:
            return None
        page = self._page_name()
        sel = self.sel.get(page)
        surf = material.control_node(mat, material.SURFACE_NODE)
        if self.search or page in {"Material", "Style", "Saved"}:
            return socket_knob(surf, "Size")
        if page == "Pattern":
            return socket_knob(surf, sel or "Size")
        if page == "Colour":
            sockets = self._color_sockets(mat)
            key = sel if sel and any(f"{n}:{s}" == sel for n, s, _ in sockets) else (
                f"{sockets[0][0]}:{sockets[0][1]}" if sockets else None)
            if key is None:
                return None
            node_name, sock = key.split(":", 1)
            return brightness_knob(material.control_node(mat, node_name).inputs[sock], sock.replace(" Color", ""))
        if page == "Detail":
            det = material.control_node(mat, material.DETAIL_NODE)
            if det is None:
                return None
            k = socket_knob(det, sel or "Detail Amount")
            if k:
                k.label = k.label.replace("Detail ", "")
            return k
        if page == "Weather":
            return socket_knob(material.control_node(mat, material.FINISH_NODE), sel or "Edge Wear")
        if page == "Relief":
            fin = material.control_node(mat, material.FINISH_NODE)
            if sel and sel != "Depth" and sel in surf.inputs:
                return socket_knob(surf, sel)
            return socket_knob(fin, "Depth")
        if page == "Mapping":
            mp = material.control_node(mat, material.MAPPING_NODE)
            return rotation_knob(mp) if mp else None
        return None

    # -- actions ----------------------------------------------------------------------
    def _undo(self, msg):
        try:
            bpy.ops.ed.undo_push(message=f"Palette: {msg}")
        except RuntimeError:
            pass

    def _apply_style(self, st):
        sp = bpy.context.scene.pp
        sp.family = st.family
        sp.style = st.id
        bpy.ops.pp.apply_style(style_id=st.id, active_only=True)

    def _open_family(self, key):
        self.browse_family = key
        bpy.context.scene.pp.family = key
        self.page = PAGES.index("Style")

    def _select(self, page, name):
        self.sel[page] = name

    def _click_colour(self, key):
        if self.sel.get("Colour") == key:
            node_name, sock = key.split(":", 1)
            sp = bpy.context.scene.pp
            sp.color_node = node_name
            sp.color_socket = sock
            bpy.ops.wm.call_panel(name="PP_PT_color_pop", keep_open=True)
        self.sel["Colour"] = key

    def _set_detail(self, kind):
        mat = self._mat()
        mat.pp.detail = kind
        self._undo("Detail")

    def _toggle_real_depth(self):
        mat = self._mat()
        mat.pp.real_depth = not mat.pp.real_depth
        self._undo("Real Depth")

    def _set_mapping(self, mode):
        self._mat().pp.mapping = mode
        self._undo("Mapping")

    def _sync_scale(self):
        bpy.ops.pp.sync_scale()

    def _apply_saved(self, name):
        bpy.ops.pp.library_apply(name=name)

    def _button(self, name, context):
        if name == "Close":
            self.finish = True
        elif name == "Random":
            if self._mat() is not None:
                bpy.ops.pp.randomize()
            else:
                bpy.ops.pp.random_style()
        elif name == "Save":
            if self._mat() is not None:
                bpy.ops.pp.library_save("INVOKE_DEFAULT")
        elif name == "Panel":
            bpy.ops.wm.call_panel(name="PP_PT_quick", keep_open=True)
        elif name == "Bake":
            bpy.ops.wm.call_panel(name="PP_PT_bake_pop", keep_open=True)

    # -- hit testing ------------------------------------------------------------------
    def hit(self, mx, my, g):
        hx, hy, hw, hh = g["header"]
        if abs(mx - hx) <= hw / 2 and abs(my - hy) <= hh / 2:
            if mx > hx + hw / 2 - 30 * g["s"]:
                return ("min",)
            return ("header",)
        if _state["minimized"]:
            return None
        for i, (bx, by, bw, bh) in enumerate(self._button_rects(g)):
            if abs(mx - bx) <= bw / 2 and abs(my - by) <= bh / 2:
                return ("button", i)
        dx, dy = mx - g["cx"], my - g["cy"]
        r = math.hypot(dx, dy)
        if r > g["R"]:
            return None
        for i, it in enumerate(self.items):
            if math.hypot(mx - it.x, my - it.y) <= it.r + 4 * g["s"]:
                return ("item", i)
        if r <= g["rk"]:
            return ("knob",)
        if g["r0"] <= r <= g["r1"]:
            n = len(PAGES)
            deg = math.degrees(math.atan2(dy, dx))
            idx = round((90.0 - deg) / (360.0 / n)) % n
            return ("page", idx)
        return ("disk",)

    # -- events ----------------------------------------------------------------------------
    def modal(self, context, event):
        if self.finish:
            self._end(context)
            return {"FINISHED"}
        region = self._region()
        if region is None:
            self._end(context)
            return {"CANCELLED"}
        try:
            return self._modal(context, event, region)
        except ReferenceError:
            self._end(context)
            return {"CANCELLED"}

    def _modal(self, context, event, region):
        mx = event.mouse_x - region.x
        my = event.mouse_y - region.y
        inside_region = 0 <= mx < region.width and 0 <= my < region.height
        g = self._geom(region)
        self.items = self.build_items()
        self._layout_items(g)

        # active drags own every event until release
        if self.drag is not None:
            if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
                kind = self.drag[0]
                if kind == "knob":
                    _, x0, y0, v0, knob = self.drag
                    d = ((mx - x0) + (my - y0)) / (200.0 * g["s"])
                    if event.shift:
                        d *= 0.1
                    knob.set(knob.drag(v0, d))
                else:
                    _, x0, y0, ox, oy = self.drag
                    _state["offset"] = (ox + (mx - x0) / region.width, oy + (my - y0) / region.height)
                self._tag()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                if self.drag[0] == "knob":
                    self._undo(self.drag[4].undo)
                self.drag = None
                self._tag()
                return {"RUNNING_MODAL"}
            if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS" and self.drag[0] == "knob":
                self.drag[4].set(self.drag[3])
                self.drag = None
                self._tag()
                return {"RUNNING_MODAL"}
            return {"RUNNING_MODAL"}

        hit = self.hit(mx, my, g) if inside_region else None
        if hit != self.hover:
            self.hover = hit
            self._tag()
            try:
                context.window.cursor_set("HAND" if hit and hit[0] in {"item", "page", "button", "min"}
                                          else "SCROLL_XY" if hit and hit[0] in {"knob", "header"}
                                          else "DEFAULT")
            except (AttributeError, TypeError):
                pass

        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            return {"PASS_THROUGH"}

        over = hit is not None
        if event.type == "ESC" and event.value == "PRESS" and (over or not self.search):
            if self.search:
                self.search = ""
                self._tag()
                return {"RUNNING_MODAL"}
            self._end(context)
            return {"CANCELLED"}

        if not over:
            return {"PASS_THROUGH"}

        # typing searches styles while hovering the dial
        if event.value == "PRESS" and not (event.ctrl or event.alt or event.oskey):
            if event.type == "BACK_SPACE" and self.search:
                self.search = self.search[:-1]
                self._tag()
                return {"RUNNING_MODAL"}
            ch = event.unicode
            if ch and (ch.isalnum() or (ch == " " and self.search)) and not _state["minimized"]:
                self.search += ch
                self._tag()
                return {"RUNNING_MODAL"}

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            direction = 1 if event.type == "WHEELUPMOUSE" else -1
            if event.ctrl:
                _state["scale"] = min(1.8, max(0.55, _state["scale"] * (1.06 ** direction)))
            elif hit[0] == "page":
                self.page = (self.page - direction) % len(PAGES)
                self.search = ""
            else:
                k = self.knob()
                if k is not None:
                    k.step(direction * (0.2 if event.shift else 1.0))
                    self._undo(k.undo)
            self._tag()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            kind = hit[0]
            if kind == "header":
                ox, oy = _state["offset"]
                self.drag = ("move", mx, my, ox, oy)
            elif kind == "min":
                _state["minimized"] = not _state["minimized"]
            elif kind == "button":
                self._button(BUTTONS[hit[1]], context)
            elif kind == "page":
                self.page = hit[1]
                self.search = ""
                if PAGES[hit[1]] == "Material":
                    self.browse_family = None
            elif kind == "item":
                it = self.items[hit[1]]
                if it.click is not None:
                    it.click()
                    self.items = self.build_items()
            elif kind == "knob":
                k = self.knob()
                if k is not None:
                    self.drag = ("knob", mx, my, k.get(), k)
            self._tag()
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"}:
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    # -- drawing -------------------------------------------------------------------------
    def _layout_items(self, g):
        n = len(self.items)
        if n == 0:
            return
        s = g["s"]
        rb = min(23 * s, math.pi * g["Ri"] / n * 0.40)
        for i, it in enumerate(self.items):
            a = math.radians(90.0 - i * 360.0 / n)
            it.x = g["cx"] + math.cos(a) * g["Ri"]
            it.y = g["cy"] + math.sin(a) * g["Ri"]
            it.r = rb

    def draw_overlay(self):
        if bpy.context.area is None or bpy.context.area.as_pointer() != self.area_ptr:
            return
        region = bpy.context.region
        if region is None or region.type != "WINDOW":
            return
        import gpu
        gpu.state.blend_set("ALPHA")
        try:
            g = self._geom(region)
            self.items = self.build_items()
            self._layout_items(g)
            if not _state["minimized"]:
                self._draw_disk(g)
                self._draw_items(g)
                self._draw_pages(g)
                self._draw_knob(g)
                self._draw_buttons(g)
            self._draw_header(g)
        finally:
            gpu.state.blend_set("NONE")

    def _draw_disk(self, g):
        cx, cy, R = g["cx"], g["cy"], g["R"]
        dd.circle(cx, cy - 4 * g["s"], R + 6 * g["s"], (0, 0, 0, 0.25))   # soft drop shadow
        dd.circle(cx, cy, R, BG)
        dd.ring(cx, cy, R - 1.2 * g["s"], R, (1, 1, 1, 0.07))
        dd.ring(cx, cy, g["Ri"] - 0.6 * g["s"], g["Ri"] + 0.6 * g["s"], (1, 1, 1, 0.045))

    def _draw_items(self, g):
        s = g["s"]
        cx, cy = g["cx"], g["cy"]
        hover_i = self.hover[1] if self.hover and self.hover[0] == "item" else -1
        if not self.items:
            return
        fsize = 11 * s
        n = len(self.items)
        max_label = 92 * s
        for i, it in enumerate(self.items):
            hov = i == hover_i
            r = it.r * (1.1 if hov else 1.0)
            if it.active:
                dd.ring(it.x, it.y, r + 2.5 * s, r + 5 * s, ACCENT)
            elif hov:
                dd.ring(it.x, it.y, r + 2.5 * s, r + 4 * s, (1, 1, 1, 0.6))
            if it.kind == "thumb":
                tex = thumbs.texture(it.tex) if it.tex else None
                if tex is not None:
                    dd.thumb(it.x, it.y, r, tex)
                else:
                    dd.ball(it.x, it.y, r, (0.45, 0.45, 0.48, 1))
                    dd.text(it.x, it.y, it.label[:1], 13 * s, TEXT)
            elif it.kind == "toggle":
                dd.ball(it.x, it.y, r, ACCENT if it.active else it.color)
            else:
                dd.ball(it.x, it.y, r * (0.78 if it.kind == "param" else 1.0), it.color)
            if it.kind == "param" and it.value is not None:
                dd.ring(it.x, it.y, r - 2.2 * s, r, (1, 1, 1, 0.12))
                if it.value > 0.002:
                    a1 = math.pi / 2
                    dd.ring(it.x, it.y, r - 2.2 * s, r, ACCENT, a1 - it.value * math.tau, a1)
            # label, pushed radially outwards
            ang = math.atan2(it.y - cy, it.x - cx)
            lx = it.x + math.cos(ang) * (r + 9 * s)
            ly = it.y + math.sin(ang) * (r + 9 * s)
            c = math.cos(ang)
            align = "LEFT" if c > 0.3 else "RIGHT" if c < -0.3 else "CENTER"
            if align == "CENTER":
                ly += math.copysign(fsize * 0.55, math.sin(ang))
            label = dd.fit(it.label, fsize, max_label)
            dd.text(lx, ly, label, fsize, WHITE if (hov or it.active) else TEXT_DIM, align)

    def _draw_pages(self, g):
        s = g["s"]
        cx, cy = g["cx"], g["cy"]
        n = len(PAGES)
        step = math.tau / n
        gap = 2.2 * s
        hover_p = self.hover[1] if self.hover and self.hover[0] == "page" else -1
        for i, name in enumerate(PAGES):
            ac = self._page_angle(i)
            g0 = gap / g["r0"]
            a0, a1 = ac - step / 2 + g0, ac + step / 2 - g0
            active = i == self.page and not self.search
            col = ACCENT if active else PANEL_HOVER if i == hover_p else PANEL
            disabled = name in NEEDS_MATERIAL and self._mat() is None
            dd.ring(cx, cy, g["r0"], g["r1"], col, a0, a1)
            rm = (g["r0"] + g["r1"]) / 2
            tcol = WHITE if active else (0.45, 0.45, 0.48, 1) if disabled else TEXT
            dd.text(cx + math.cos(ac) * rm, cy + math.sin(ac) * rm, name, 10.5 * s, tcol, shadow=False)

    def _draw_knob(self, g):
        s = g["s"]
        cx, cy, rk = g["cx"], g["cy"], g["rk"]
        hov = self.hover == ("knob",) or (self.drag is not None and self.drag[0] == "knob")
        dd.circle(cx, cy, rk, (0.11, 0.11, 0.12, 1) if not hov else (0.14, 0.14, 0.15, 1))
        k = self.knob()
        a_start = math.radians(225)
        sweep = math.radians(270)
        tr0, tr1 = rk - 13 * s, rk - 7 * s
        dd.ring(cx, cy, tr0, tr1, (1, 1, 1, 0.09), a_start - sweep, a_start)
        if k is None:
            msg = "Pick a style" if self._mat() is None else "—"
            if self._page_name() == "Saved" and not self.items and not self.search:
                msg = "No saved looks"
            dd.text(cx, cy + 2 * s, msg, 11 * s, TEXT_DIM, shadow=False)
            return
        t = max(0.0, min(1.0, k.t()))
        if t > 0.001:
            dd.ring(cx, cy, tr0, tr1, ACCENT, a_start - sweep * t, a_start)
        ad = a_start - sweep * t
        rm = (tr0 + tr1) / 2
        dd.circle(cx + math.cos(ad) * rm, cy + math.sin(ad) * rm, 5.5 * s, WHITE)
        dd.text(cx, cy + 14 * s, dd.fit(k.label, 10.5 * s, rk * 1.4), 10.5 * s, TEXT, shadow=False)
        dd.text(cx, cy - 8 * s, k.fmt(k.get()), 19 * s, ACCENT, shadow=False)

    def _draw_header(self, g):
        s = g["s"]
        hx, hy, hw, hh = g["header"]
        dd.rrect(hx, hy, hw, hh, hh / 2, (0.09, 0.09, 0.1, 0.96))
        dd.rrect(hx, hy, hw, hh, hh / 2, (1, 1, 1, 0.08), outline=1.0 * s)
        # grip
        gx = hx - hw / 2 + 16 * s
        for dx in (-2.5, 2.5):
            for dy in (-5, 0, 5):
                dd.circle(gx + dx * s, hy + dy * s, 1.3 * s, TEXT_DIM)
        # minimise / restore
        mxp = hx + hw / 2 - 16 * s
        hov = self.hover == ("min",)
        if hov:
            dd.circle(mxp, hy, 10 * s, (1, 1, 1, 0.1))
        if _state["minimized"]:
            dd.text(mxp, hy, "+", 14 * s, TEXT, shadow=False)
        else:
            dd.rrect(mxp, hy, 9 * s, 1.6 * s, 0.8 * s, TEXT)
        if self.search:
            title = f"Search: {self.search}_"
        else:
            st = self._current_style()
            mat = self._mat()
            if st is not None:
                title = f"{presets.FAMILY_LABEL[st.family].split(',')[0].split(' &')[0]}  ·  {st.name}  ·  " \
                        f"{MAPPING_LABELS.get(mat.pp.mapping, '')}"
            else:
                title = "Procedural Palette"
        dd.text(hx, hy, dd.fit(title, 11.5 * s, hw - 70 * s), 11.5 * s, TEXT, shadow=False)

    def _draw_buttons(self, g):
        s = g["s"]
        hover_b = self.hover[1] if self.hover and self.hover[0] == "button" else -1
        for i, (bx, by, bw, bh) in enumerate(self._button_rects(g)):
            col = (0.2, 0.2, 0.215, 0.96) if i == hover_b else (0.11, 0.11, 0.12, 0.94)
            dd.rrect(bx, by, bw, bh, bh / 2, col)
            dd.rrect(bx, by, bw, bh, bh / 2, (1, 1, 1, 0.07), outline=1.0 * s)
            dd.text(bx, by, BUTTONS[i], 11 * s, TEXT, shadow=False)


def register():
    bpy.utils.register_class(PP_OT_dial)


def unregister():
    global _running
    if _running is not None:
        try:
            if _running._handle is not None:
                bpy.types.SpaceView3D.draw_handler_remove(_running._handle, "WINDOW")
                _running._handle = None
        except (ValueError, ReferenceError):
            pass
        _running = None
    bpy.utils.unregister_class(PP_OT_dial)
    dd.free()
