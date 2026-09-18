# Recipe: Create a Glyphs Plugin

Create a standalone GlyphsApp Python plugin (`.glyphsPlugin`) with a valid bundle
and a General plugin class.

**Verified against:** Glyphs 4.1.1 (build 4108), using the official Glyphs SDK
"General Plugin" Python template (`schriftgestalt/GlyphsSDK`,
`Python Templates/General Plugin`) and the plugin wrapper shipped inside the app
at `Glyphs 4.app/Contents/Scripts/GlyphsApp/plugins.py`. The same bundle layout
also loads in Glyphs 3; only the Python API differs. Check
`Glyphs.versionNumber` and `Glyphs.buildNumber` at runtime before relying on
version-specific behavior.

## Parameters needed
- `plugin_name`: PascalCase class name (e.g. `CopycatMaster`). Used for the class, the bundle folder, and `NSPrincipalClass`.
- `bundle_id`: reverse-DNS identifier (e.g. `com.nico.copycatmaster`). Must be unique across installed plugins.
- `description`: what the plugin does
- `menu_location`: menu constant for the menu item (usually `WINDOW_MENU`)
- `output_path`: where to create the plugin folder (e.g. `/Users/.../Apps/MyPlugin`)
- `bundle_version`: machine-readable version, integer or float (e.g. `1.0`)
- `copyright`: optional author line (e.g. `Copyright, Nico, 2026`)

## Steps

### 1. Create the bundle structure

Every GlyphsApp plugin is a `.glyphsPlugin` bundle (a folder macOS treats as a
package):

```
{plugin_name}.glyphsPlugin/
  Contents/
    Info.plist          ← Bundle metadata
    MacOS/
      plugin            ← Binary loader stub (copy from an existing .glyphsPlugin)
    Resources/
      plugin.py         ← Main plugin code (entry point)
```

**Create all directories:**
```
mkdir -p {output_path}/{plugin_name}.glyphsPlugin/Contents/{MacOS,Resources}
```

**Copy the binary loader stub.** This stub is a universal arm64 + x86_64 Mach-O
bundle (`120256` bytes, md5 `e089046f66cec3264e14e3f7db1cbfcc`). It is identical
across Glyphs 3 and Glyphs 4, so any existing `.glyphsPlugin` works as a source:

```
cp ~/Library/Application\ Support/Glyphs\ 4/Plugins/GlyphsMCP.glyphsPlugin/Contents/MacOS/plugin \
   {output_path}/{plugin_name}.glyphsPlugin/Contents/MacOS/plugin
```

### 2. Create Info.plist

CRITICAL: `NSPrincipalClass` MUST match the Python class name exactly, and
`PyMainFileNames` MUST list `plugin.py`. Without `PyMainFileNames`, Glyphs does
not load a Python plugin.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
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
    <string>{bundle_version}</string>
    <key>CFBundleVersion</key>
    <string>{bundle_version}</string>
    <key>NSHumanReadableCopyright</key>
    <string>{copyright}</string>
    <key>NSPrincipalClass</key>
    <string>{plugin_name}</string>
    <key>PyMainFileNames</key>
    <array>
        <string>plugin.py</string>
    </array>
</dict>
</plist>
```

Optionally add `UpdateFeedURL` and `productPageURL` to participate in Glyphs'
automatic update notifications (the online plist must contain at least
`CFBundleVersion` and `productPageURL`).

### 3. Create plugin.py

Template for a `GeneralPlugin` with a menu item and an action:

```python
# encoding: utf-8
"""
{plugin_name} — {description}
"""

import objc
from GlyphsApp import Glyphs, WINDOW_MENU
from GlyphsApp.plugins import GeneralPlugin
from AppKit import NSMenuItem


class {plugin_name}(GeneralPlugin):

    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({"en": "{plugin_name}"})

    @objc.python_method
    def start(self):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "{menu_title}", self.action_, ""
        )
        item.setTarget_(self)
        Glyphs.menu[WINDOW_MENU].append(item)

    def action_(self, sender):
        """Main action — triggered from the menu."""
        font = Glyphs.font
        if not font:
            Glyphs.showNotification("{plugin_name}", "Open a font first.")
            return

        # Your logic here
        pass

    @objc.python_method
    def __file__(self):
        """Please leave this method unchanged"""
        return __file__
```

### 4. Critical rules

Follow these rules or the plugin will crash or silently fail:

1. **Class name = `NSPrincipalClass`**: the class in `plugin.py` MUST match `NSPrincipalClass` in `Info.plist` exactly, and `plugin.py` MUST be listed in `PyMainFileNames`.
2. **Imports**: use explicit imports — `from GlyphsApp import Glyphs, WINDOW_MENU` plus `from GlyphsApp.plugins import GeneralPlugin`. Menu constants and callback hooks come from `GlyphsApp`; the base classes come from `GlyphsApp.plugins`. Do not rely on `from GlyphsApp import *`.
3. **ObjC action methods**: methods called from ObjC (menu actions) use camelCase with a trailing underscore (e.g. `action_`). Do NOT add `@objc.python_method` to them.
4. **Python methods**: internal helpers and lifecycle methods (`settings`, `start`, `__file__`) MUST have the `@objc.python_method` decorator.
5. **No external packages**: only stdlib + PyObjC + GlyphsApp. Do not import pip packages; installable modules come from the Plugin Manager's "Modules" tab.
6. **Threading**: all GlyphsApp API calls (GSFont, GSGlyph, GSLayer, etc.) MUST happen on the main thread. Menu actions already run on the main thread. Never touch Glyphs objects from a background thread.
7. **Bulk changes**: wrap multi-glyph writes in `font.disableUpdateInterface()` / `font.enableUpdateInterface()`.
8. **Path mutation**: the [GSLayer.paths reference](https://docu.glyphsapp.com/#GSLayer.paths) describes an iteration helper and directs additions/removals to `GSLayer.shapes`. Preserve components and other shapes for paths-only operations.
9. **Node types are strings**: `node.type` returns `"line"`, `"curve"`, `"offcurve"`, or `"qcurve"` — NOT integers.
10. **Version checks**: use `Glyphs.versionNumber` (float, major.minor) for feature gates, e.g. `if Glyphs.versionNumber < 4:`. Use `Glyphs.buildNumber` when preview builds matter. `Glyphs.versionNumber` is derived from the version string and only keeps major.minor.
11. **`__file__` method**: keep it unchanged. It is required for `self.loadNib("name", __file__)` and for resolving resources inside the bundle.

### 5. Optional — react to editor events with callbacks

A `GeneralPlugin` is the right place for callbacks that must stay live. Register
in `start()` and remove them when the plugin is torn down:

```python
@objc.python_method
def start(self):
    Glyphs.addCallback(self.draw_overlay_, DRAWFOREGROUND)

def draw_overlay_(self, layer, info):
    try:
        # layer is a GSLayer; info is a dict with at least "Scale"
        pass
    except Exception:
        import traceback
        traceback.print_exc()
```

Available hooks include `DRAWFOREGROUND`, `DRAWBACKGROUND`, `DRAWINACTIVE`,
`DOCUMENTOPENED`, `DOCUMENTACTIVATED`, `DOCUMENTWASSAVED`, `DOCUMENTEXPORTED`,
`DOCUMENTWILLCLOSE`, `DOCUMENTDIDCLOSE`, `TABDIDOPEN`, `TABWILLCLOSE`,
`UPDATEINTERFACE`, and the `MOUSE*` hooks. Remove a callback with
`Glyphs.removeCallback(function)` using the same function reference.

### 6. Install the plugin

Following the [official installation guidance](https://handbook.glyphsapp.com/plugins/),
drag the completed bundle onto the intended Glyphs app icon in the Dock. The
handbook advises against manually moving bundles into the Plugins folder because
that interferes with macOS security. Make sure the target app matches the build
used to validate the plugin.

Then restart GlyphsApp.

### 7. Verify

After restart, check:
- Menu item appears under the expected menu
- Click the menu item — action runs without errors
- Check GlyphsApp's Macro Panel (Window > Macro Panel) for any error output
- Confirm the plugin loaded under Glyphs > Settings > Plugins

### Common AppKit UI patterns

**NSAlert dialog (confirmation):**
```python
from AppKit import NSAlert, NSAlertFirstButtonReturn

alert = NSAlert.alloc().init()
alert.setMessageText_("Title")
alert.setInformativeText_("Description")
alert.addButtonWithTitle_("OK")
alert.addButtonWithTitle_("Cancel")
if alert.runModal() == NSAlertFirstButtonReturn:
    pass
```

**NSPopUpButton (dropdown in dialog):**
```python
from AppKit import NSAlert, NSPopUpButton, NSView, NSMakeRect

accessory = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 30))
popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 280, 26), False)
popup.addItemWithTitle_("Option A")
popup.addItemWithTitle_("Option B")
accessory.addSubview_(popup)
alert.setAccessoryView_(accessory)
# After runModal:
selected = popup.indexOfSelectedItem()
```

**Notification (macOS Notification Center):**
```python
Glyphs.showNotification("Title", "Body text")
```

**Modal alert (blocking):**
```python
from GlyphsApp import Message  # or use Glyphs.showNotification for non-blocking

Message(title="Title", message="Body text")
```
