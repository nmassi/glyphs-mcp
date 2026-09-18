# Recipe: Create a Glyphs Script

Create a standalone GlyphsApp Python script that appears in the Script menu.

**Verified against:** Glyphs 4.1.1 (build 4108), the Glyphs Handbook chapter
[Scripts](https://handbook.glyphsapp.com/scripts/), and the current `mekkablue`
script collection. Scripts use the same Python API as plugins; only the
packaging differs: a script is a plain `.py` file, with no bundle and no
`Info.plist`. Check `Glyphs.versionNumber` and `Glyphs.buildNumber` at runtime
before relying on version-specific behavior.

## Parameters needed
- `script_name`: display name in the Script menu (e.g. `Round All Corners`)
- `file_name`: file name without extension (usually the same as `script_name`)
- `submenu`: optional subfolder used to group scripts (e.g. `My Tools`)
- `description`: tooltip shown when the pointer rests on the menu item
- `output_path`: Scripts folder, normally `~/Library/Application Support/Glyphs 4/Scripts`
- `script_body`: the code that does the work

## Steps

### 1. Locate the Scripts folder

The Script menu mirrors the files of the Scripts folder, which sits next to the
Plugins folder:
- Glyphs 4: `~/Library/Application Support/Glyphs 4/Scripts/`
- Glyphs 3: `~/Library/Application Support/Glyphs 3/Scripts/`

Open it from inside the app with Script → Open Scripts Folder
(Cmd-Shift-Y). Create the file and any grouping folder with:

```
mkdir -p "{output_path}/{submenu}"
```

Subfolders become submenus. The part of the file name before `.py` is arbitrary,
but naming it after the intended menu title keeps the folder readable. For a
distributed collection, keep the `.py` files in a repository and symlink the
folder into `Scripts/`.

### 2. Create the script file

Minimal working script (`{file_name}.py`):

```python
# MenuTitle: {script_name}
# -*- coding: utf-8 -*-
__doc__ = """
{description}
"""

from GlyphsApp import Glyphs


def main():
    font = Glyphs.font
    if font is None:
        Glyphs.showNotification("{script_name}", "Open a font first.")
        return

    # Your logic here
    pass


main()
```

- `# MenuTitle:` on the first line sets the Script-menu label. Without it the
  file name is used.
- `__doc__` is the hover tooltip. It may span multiple lines.
- `# -*- coding: utf-8 -*-` is optional on Python 3; keep it only for
  compatibility with older setups.
- Import from `GlyphsApp` explicitly (`from GlyphsApp import Glyphs, ...`); do
  not rely on a star import.

### 3. Script structure rules

Follow these rules or the script will error or corrupt data:

1. **Top-level code runs immediately** when the menu item is clicked, on the
   main thread. Put the work in a `main()` function and call it at the end so
   you can use early `return` guards.
2. **Guard the context**: check `Glyphs.font`, `font.selectedLayers`, and the
   active master before assuming they exist.
3. **Bulk changes**: wrap multi-glyph writes in
   `font.disableUpdateInterface()` / `font.enableUpdateInterface()`. Always
   release it in a `finally` block.
4. **Main thread only**: never touch GlyphsApp objects from a background thread.
   If you parallelize I/O, marshal results back with `AppHelper.callAfter`.
5. **No pip imports**: only stdlib + PyObjC + GlyphsApp + bundled modules.
   Installable modules come from the Plugin Manager's "Modules" tab.
6. **Reporting**: use `print()` for the Macro Panel and
   `Glyphs.showNotification(title, message)` for user-facing feedback. Call
   `Glyphs.clearLog()` at the start and `Glyphs.showMacroWindow()` when you want
   the log visible.
7. **Undo hygiene**: prefer small, explicit edits; users can undo a whole script
   run, so avoid destructive multi-step operations that are hard to reverse.
8. **Resources**: `__file__` is defined when Glyphs runs the script, so
   `os.path.dirname(__file__)` resolves sibling files.

### 4. Optional — batch pattern over selected layers

A common shape for a batch operation:

```python
from GlyphsApp import Glyphs


def main():
    font = Glyphs.font
    if font is None:
        Glyphs.showNotification("Script", "Open a font first.")
        return

    layers = [layer for layer in font.selectedLayers if layer is not None]
    if not layers:
        Glyphs.showNotification("Script", "Select at least one glyph.")
        return

    font.disableUpdateInterface()
    try:
        for layer in layers:
            for path in layer.paths:
                for node in path.nodes:
                    # mutate node here
                    pass
    finally:
        font.enableUpdateInterface()

    print("Updated %i layer(s)." % len(layers))


main()
```

`layer.shapes` is the collection to use for adding/removing paths, components,
and anchors; `layer.paths` is an iteration helper. `node.type` is a string
(`"line"`, `"curve"`, `"offcurve"`, `"qcurve"`), not an integer.

### 5. Reload and run

- Reload the Script menu with Option → Script → Reload Scripts
  (Cmd-Opt-Shift-Y). A relaunch is not required.
- Re-run the last script with Cmd-Opt-R.
- Assign a keyboard shortcut in Settings → Shortcuts.

### 6. Verify

- The menu item appears under Script, inside the expected submenu.
- Hovering it shows the `__doc__` tooltip.
- Running it produces no error; check the Macro Panel (Window → Macro Panel)
  and Console.app filtered by `Glyphs` for output.

### Choosing a script, a plugin, or an MCP tool

- **Script**: a saved command the designer triggers on demand from the Script
  menu. Best for one-shot or explicitly triggered transformations.
- **Plugin**: behavior that must stay live or reactive (drawing overlays,
  callbacks, persistent panels). See `create_glyphs_plugin`.
- **GlyphsMCP tool**: if a typed tool already does the job, use it instead of
  writing a script. Keep `execute_in_glyphs` for ad-hoc, one-off code with a
  defined scope, rollback, and verification.

### Common patterns

**User feedback:**
```python
Glyphs.showNotification("Title", "Body text")
```

**Log to the Macro Panel:**
```python
Glyphs.clearLog()
print("value:", some_value)
Glyphs.showMacroWindow()
```

**Refresh the UI after edits:**
```python
Glyphs.redraw()
```
