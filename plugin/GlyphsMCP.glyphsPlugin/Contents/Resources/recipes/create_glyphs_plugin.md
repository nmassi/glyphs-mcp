# Recipe: Create a GlyphsApp Plugin

Create a standalone GlyphsApp 3 plugin with proper bundle structure.

**Version scope (2026-09-05):** this remains a Glyphs 3 template, not a verified
Glyphs 4 recipe. Confirm the intended app version/build and consult the
[Python wrapper](https://docu.glyphsapp.com/) and
[Core API](https://docu.glyphsapp.com/Core/) before adapting it. The `GLYPHS4`
symbol page alone does not establish compatibility. The local `docs-mcp` snapshot
was ingested on 2026-03-05; the template was not runtime-tested in this audit.

## Parameters needed
- `plugin_name`: PascalCase name (e.g., `CopycatMaster`). Used for class, bundle, and folder names.
- `bundle_id`: reverse-DNS identifier (e.g., `com.nico.copycatmaster`)
- `description`: what the plugin does
- `menu_location`: where to add the menu item (usually `WINDOW_MENU`)
- `output_path`: where to create the plugin folder (e.g., `/Users/.../Apps/MyPlugin`)

## Steps

### 1. Create bundle structure

Every GlyphsApp plugin is a `.glyphsPlugin` bundle (a folder macOS treats as a package):

```
{plugin_name}.glyphsPlugin/
  Contents/
    Info.plist          ← Bundle metadata
    MacOS/
      plugin            ← Binary stub (copy from any existing .glyphsPlugin)
    Resources/
      plugin.py         ← Main plugin code (entry point)
```

**Create all directories:**
```
mkdir -p {output_path}/{plugin_name}.glyphsPlugin/Contents/{MacOS,Resources}
```

**Copy binary stub** from an existing plugin validated for the target app build.
The following source path is a Glyphs 3 example only; verify it before copying:
```
cp ~/Library/Application\ Support/Glyphs\ 3/Plugins/GlyphsMCP.glyphsPlugin/Contents/MacOS/plugin \
   {output_path}/{plugin_name}.glyphsPlugin/Contents/MacOS/plugin
```

### 2. Create Info.plist

CRITICAL: `NSPrincipalClass` MUST match the Python class name exactly.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>English</string>
    <key>CFBundleExecutable</key>
    <string>plugin</string>
    <key>CFBundleIdentifier</key>
    <string>{bundle_id}</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>{plugin_name}</string>
    <key>CFBundlePackageType</key>
    <string>BNDL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>NSPrincipalClass</key>
    <string>{plugin_name}</string>
</dict>
</plist>
```

### 3. Create plugin.py

Template for a GeneralPlugin with a menu item and dialog:

```python
# encoding: utf-8
"""
{plugin_name} — {description}
"""

from GlyphsApp import *
from GlyphsApp.plugins import *
from AppKit import NSMenu, NSMenuItem, NSAlert
from Foundation import NSPoint


class {plugin_name}(GeneralPlugin):

    @objc.python_method
    def settings(self):
        self.name = "{plugin_name}"

    @objc.python_method
    def start(self):
        self._menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "{menu_title}", self.action_, ""
        )
        self._menu_item.setTarget_(self)
        Glyphs.menu[WINDOW_MENU].append(self._menu_item)

    def action_(self, sender):
        """Main action — triggered from menu."""
        font = Glyphs.font
        if not font:
            Message("No font open", "Open a font first.")
            return

        # Your logic here
        pass

    @objc.python_method
    def __del__(self):
        if hasattr(self, '_menu_item') and self._menu_item:
            try:
                Glyphs.menu[WINDOW_MENU].submenu().removeItem_(self._menu_item)
            except Exception:
                pass

    @objc.python_method
    def __file__(self):
        """Please leave this method unchanged"""
        return __file__
```

### 4. Critical rules

Follow these rules or the plugin will crash or silently fail:

1. **Class name = NSPrincipalClass**: The class in plugin.py MUST match `NSPrincipalClass` in Info.plist exactly.
2. **Imports**: MUST use BOTH `from GlyphsApp import *` AND `from GlyphsApp.plugins import *`. Menu constants like `WINDOW_MENU` only come from the first.
3. **ObjC methods**: Methods called from ObjC (menu actions) use camelCase with trailing underscore (e.g., `action_`). Do NOT add `@objc.python_method`.
4. **Python methods**: Internal helpers MUST have `@objc.python_method` decorator if they don't follow ObjC naming conventions.
5. **No external packages**: Only stdlib + PyObjC + GlyphsApp. No pip packages.
6. **Threading**: All GlyphsApp API calls (GSFont, GSGlyph, GSLayer, etc.) MUST happen on the main thread. Menu actions already run on main thread, so this is automatic for UI plugins.
7. **Bulk changes**: Wrap multi-glyph writes in `font.disableUpdateInterface()` / `font.enableUpdateInterface()`.
8. **Path mutation**: The current [GSLayer.paths reference](https://docu.glyphsapp.com/#GSLayer.paths) describes an iteration helper and directs additions/removals to `GSLayer.shapes`. Verify mutations on the target build; preserve components and other shapes for paths-only operations.
9. **Node types are strings**: In Glyphs 3, `node.type` returns `"line"`, `"curve"`, `"offcurve"`, `"qcurve"` — NOT integers.

### 5. Install the plugin

Following the [official installation guidance](https://handbook.glyphsapp.com/plugins/),
drag the completed bundle onto the intended Glyphs app icon in the Dock. The
handbook advises against manually moving bundles into the Plugins folder because
that interferes with macOS security. Make sure the target app matches the build
used to validate the plugin.

Then restart GlyphsApp.

### 6. Verify

After restart, check:
- Menu item appears under Window menu
- Click the menu item — action runs without errors
- Check GlyphsApp's Macro Panel (Window > Macro Panel) for any error output

### Common AppKit UI patterns

**NSAlert dialog (confirmation):**
```python
alert = NSAlert.alloc().init()
alert.setMessageText_("Title")
alert.setInformativeText_("Description")
alert.addButtonWithTitle_("OK")
alert.addButtonWithTitle_("Cancel")
response = alert.runModal()
if response == 1000:  # OK clicked
    pass
```

**NSPopUpButton (dropdown in dialog):**
```python
from AppKit import NSPopUpButton, NSView
accessory = NSView.alloc().initWithFrame_(((0, 0), (300, 30)))
popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 0), (280, 26)), False)
popup.addItemWithTitle_("Option A")
popup.addItemWithTitle_("Option B")
accessory.addSubview_(popup)
alert.setAccessoryView_(accessory)
# After runModal:
selected = popup.indexOfSelectedItem()
```

**Message (simple notification):**
```python
Message("Title", "Body text")
```
