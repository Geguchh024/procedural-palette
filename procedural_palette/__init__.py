# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural Palette: an open source procedural material tool for Blender."""

import bpy
from bpy.app.handlers import persistent

from . import dial, material, ops, props, thumbs, ui

_keymaps = []


@persistent
def _upgrade_on_load(*_args):
    """Upgrade Palette materials saved with an older add-on version."""
    try:
        material.upgrade_file()
    except Exception as e:  # never block file loading
        print("Procedural Palette: upgrade failed:", e)


def _upgrade_current():
    _upgrade_on_load()
    return None


def register():
    props.register()
    ops.register()
    ui.register()
    dial.register()
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new("pp.dial", "M", "PRESS", shift=True, alt=True)
        _keymaps.append((km, kmi))
    bpy.app.handlers.load_post.append(_upgrade_on_load)
    # the open file at install/update time (bpy.data is restricted during register)
    bpy.app.timers.register(_upgrade_current, first_interval=0.5)


def unregister():
    if _upgrade_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_upgrade_on_load)
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    dial.unregister()
    ui.unregister()
    thumbs.clear()
    ops.unregister()
    props.unregister()
