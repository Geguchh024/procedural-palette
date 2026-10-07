# SPDX-License-Identifier: GPL-3.0-or-later
"""Personal look library stored as JSON in the user config folder."""

import json
import os

import bpy

PKG = __package__


def library_dir():
    try:
        path = bpy.utils.extension_path_user(PKG, path="", create=True)
    except (ValueError, AttributeError):
        path = bpy.utils.user_resource("CONFIG", path="procedural_palette", create=True)
    return path


def library_file():
    return os.path.join(library_dir(), "library.json")


def load():
    try:
        with open(library_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data):
    path = library_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def add(name, look):
    data = load()
    data[name] = look
    save(data)


def remove(name):
    data = load()
    if data.pop(name, None) is not None:
        save(data)


_items = []


def enum_items(self, context):
    global _items
    names = sorted(load().keys(), key=str.lower)
    _items = [(n, n, "") for n in names] or [("NONE", "(empty)", "")]
    return _items
