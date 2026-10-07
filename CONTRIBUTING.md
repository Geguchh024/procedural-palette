# Contributing

Thanks for helping. Below is everything you need to work on the add-on.

## Project layout

```
procedural_palette/        the add-on (this folder is what gets zipped)
  __init__.py              registration, hotkey, upgrade-on-load handler
  blender_manifest.toml    extension manifest (id, version, license)
  nodekit.py               small DSL for building node trees in Python
  patterns.py              the 20 pattern generators (shader node groups)
  finish.py                weathering group and detail overlays
  presets.py               the built-in styles
  material.py              material assembly, snapshot/restore, upgrades
  props.py / ops.py / ui.py   properties, operators, sidebar panels
  dial.py / dial_draw.py   the viewport dial (modal operator + GPU drawing)
  thumbs.py, thumbs/       preview thumbnails
  bake.py, library.py      texture baking, saved looks
tools/render_thumbnails.py renders the preview thumbnails
tests/run_tests.py         headless test suite
docs/images/               screenshots used in the README
```

## Running from source

Point Blender at the repo instead of installing the zip. Add the repo root to
*Preferences → File Paths → Script Directories*, or symlink `procedural_palette`
into your user extensions folder. Then enable the add-on.

## Tests

```
blender -b --factory-startup --python tests/run_tests.py
```

The script exits with a non-zero status if any test fails. Please run it before
opening a pull request.

## Building the zip

Run this from the repo root:

```
blender --command extension build --source-dir procedural_palette --output-dir dist
```

## Adding a pattern

1. In `patterns.py`, write a builder function and decorate it with
   `@pattern(key, label, dims, inputs, simple)`.
   - `dims=2` means a planar pattern, which gets box-projected automatically.
   - Sizes are in metres.
   - Return a dict of the standard outputs. Keep Height near 1 on the outer
     surface: the weathering layer treats low height as a cavity.
2. Add styles that use the pattern in `presets.py`.
3. Render their thumbnails:
   `blender -b --factory-startup --python tools/render_thumbnails.py -- "Style Name"`

## Changing an existing node group

Node groups are shared by every Palette material in a file. If you change a
group's inputs or outputs, bump `GROUP_VERSION` in `nodekit.py`. Opening an
older file then upgrades its materials safely (see `material.upgrade_file`).
Never rebuild a group in place without going through that path, because it
disconnects every material that uses the group.

## Releases

1. Bump `version` in `procedural_palette/blender_manifest.toml`.
2. Add an entry to `CHANGELOG.md`.
3. Build the zip and attach it to a GitHub release.
