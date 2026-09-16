# Recipe: Clean Up Timestamped Layers Safely

Remove backup layers whose names exactly match `18 Jul 26 at 15:04` from every
glyph while preserving every current font master, custom-named layer, and
special layer. This workflow is intentionally strict: it inventories first,
creates a recoverable sibling backup, checks an exact target fingerprint,
applies the deletion, and verifies the saved font.

## When to use

- A Glyphs file accumulated layers named exactly like `18 Jul 26 at 15:04`.
- The designer explicitly wants dated layers removed from all glyphs.
- Master layers and non-dated special layers must remain untouched.

## Safety rules

- Never identify masters by layer name alone. Protect every current master ID
  and every layer for which Glyphs reports `isMasterLayer`.
- Never delete a layer for which Glyphs reports `isSpecialLayer` or
  `isColorLayer`, a layer with attributes, or a layer whose name contains
  brace/bracket syntax (`{}`, `[]`).
- Only an exact `DD Mon YY at HH:MM` match is eligible. The day and time must be
  zero-padded, the month must be an English three-letter abbreviation, and all
  spacing and the literal `at` must match. All other names are preserved.
- Never apply changes to an unsaved font.
- Never apply if the family, path, master IDs, target count, or target
  fingerprint differs from the dry run.
- Always create a sibling backup before deleting anything.
- If a guard fails, stop and report it. Do not weaken the guard automatically.

## Steps

### 1. Verify the open font and master structure

Run all of these tools:

- `get_font_info`
- `get_selection`
- `get_masters`

Then run this read-only runtime check with `execute_in_glyphs`:

```python
font = Glyphs.font
assert font is not None, "No font is open"
assert font.filepath, "Save the font before running this recipe"
print("FAMILY:", font.familyName)
print("PATH:", font.filepath)
print("GLYPHS:", len(font.glyphs))
print("MASTERS:", [(str(master.id), master.name) for master in font.masters])
print("GLYPHS_VERSION:", getattr(Glyphs, "versionNumber", None))
print("GLYPHS_BUILD:", getattr(Glyphs, "buildNumber", None))
```

Report the exact family, file path, glyph count, and masters. Every master
listed here is protected, regardless of its name.

### 2. Inventory eligible layers without modifying the font

Run this dry-run code with `execute_in_glyphs`:

```python
import hashlib
import json
import re

font = Glyphs.font
assert font is not None, "No font is open"
assert font.filepath, "Save the font before running this recipe"

timestamp_name = re.compile(
    r"^(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"[0-9]{2} at (?:[01][0-9]|2[0-3]):[0-5][0-9]$"
)

def protection_reasons(layer, master_ids):
    reasons = []
    layer_id = str(layer.layerId)
    layer_name = str(layer.name or "")
    if layer_id in master_ids or bool(getattr(layer, "isMasterLayer", False)):
        reasons.append("master")
    if bool(getattr(layer, "isSpecialLayer", False)):
        reasons.append("special")
    if bool(getattr(layer, "isColorLayer", False)):
        reasons.append("color")
    attributes = getattr(layer, "attributes", None)
    attribute_keys = []
    if attributes is not None:
        try:
            attribute_keys = sorted(str(key) for key in attributes.keys())
        except Exception:
            attribute_keys = ["unreadable"]
    if attribute_keys:
        reasons.append("attributes:" + ",".join(attribute_keys))
    if any(character in layer_name for character in "{}[]"):
        reasons.append("brace-or-bracket-name")
    return reasons

master_ids = {str(master.id) for master in font.masters}
targets = []
preserved_extras = []

for glyph in font.glyphs:
    for layer in list(glyph.layers):
        layer_id = str(layer.layerId)
        layer_name = str(layer.name or "")
        record = (str(glyph.name), layer_name, layer_id)
        reasons = protection_reasons(layer, master_ids)
        if timestamp_name.fullmatch(layer_name) and not reasons:
            targets.append(record)
        elif layer_id not in master_ids:
            preserved_extras.append({
                "glyph": str(glyph.name),
                "name": layer_name,
                "layerId": layer_id,
                "reasons": reasons or ["name-does-not-exactly-match"],
            })

fingerprint_input = "\n".join(
    "\t".join(record) for record in sorted(targets)
).encode("utf-8")
fingerprint = hashlib.sha256(fingerprint_input).hexdigest()

print(json.dumps({
    "family": str(font.familyName),
    "path": str(font.filepath),
    "glyphCount": len(font.glyphs),
    "masters": sorted(
        (str(master.id), str(master.name)) for master in font.masters
    ),
    "targetCount": len(targets),
    "targetFingerprint": fingerprint,
    "targets": sorted(targets),
    "preservedNonMasterLayers": sorted(
        preserved_extras,
        key=lambda record: (
            record["glyph"],
            record["name"],
            record["layerId"],
        ),
    ),
}, indent=2, ensure_ascii=False))
```

Report the target count, the complete target-name summary, every preserved
non-master layer with its protection reason, and the fingerprint. If any custom,
intermediate, brace, bracket, color, or attributed layer enters the target set,
stop. Obtain explicit confirmation of this exact scope before proceeding to the
destructive step.

### 3. Create a backup and delete the exact dry-run target set

Proceed only after the designer explicitly confirmed the Step 2 scope. Copy
the Step 2 values into every `EXPECTED_*` constant below. Do not execute with
placeholders, and do not substitute newly observed values when a guard fails.

Run with `execute_in_glyphs`:

```python
import datetime
import hashlib
import os
import re
import shutil

EXPECTED_FAMILY = "<family from Step 2>"
EXPECTED_PATH = "<path from Step 2>"
EXPECTED_GLYPH_COUNT = -1  # Replace with Step 2 glyphCount.
EXPECTED_MASTER_IDS = {"<master ID from Step 2>"}
EXPECTED_TARGET_COUNT = -1  # Replace with Step 2 targetCount.
EXPECTED_TARGET_FINGERPRINT = "<fingerprint from Step 2>"

font = Glyphs.font
assert font is not None, "No font is open"
assert font.filepath, "Save the font before running this recipe"
assert str(font.familyName) == EXPECTED_FAMILY, "The open family changed"
assert str(font.filepath) == EXPECTED_PATH, "The open file changed"
assert len(font.glyphs) == EXPECTED_GLYPH_COUNT, "The glyph count changed"

master_ids = {str(master.id) for master in font.masters}
assert master_ids == EXPECTED_MASTER_IDS, "The master IDs changed"

timestamp_name = re.compile(
    r"^(?:0[1-9]|[12][0-9]|3[01]) "
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"[0-9]{2} at (?:[01][0-9]|2[0-3]):[0-5][0-9]$"
)

def protection_reasons(layer, master_ids):
    reasons = []
    layer_id = str(layer.layerId)
    layer_name = str(layer.name or "")
    if layer_id in master_ids or bool(getattr(layer, "isMasterLayer", False)):
        reasons.append("master")
    if bool(getattr(layer, "isSpecialLayer", False)):
        reasons.append("special")
    if bool(getattr(layer, "isColorLayer", False)):
        reasons.append("color")
    attributes = getattr(layer, "attributes", None)
    attribute_keys = []
    if attributes is not None:
        try:
            attribute_keys = sorted(str(key) for key in attributes.keys())
        except Exception:
            attribute_keys = ["unreadable"]
    if attribute_keys:
        reasons.append("attributes:" + ",".join(attribute_keys))
    if any(character in layer_name for character in "{}[]"):
        reasons.append("brace-or-bracket-name")
    return reasons

targets = []
preserved_before = set()
for glyph in font.glyphs:
    for master_id in master_ids:
        assert glyph.layers[master_id] is not None, (
            "Missing master layer %s in %s" % (master_id, glyph.name)
        )
    for layer in list(glyph.layers):
        layer_id = str(layer.layerId)
        layer_name = str(layer.name or "")
        record = (str(glyph.name), layer_name, layer_id)
        reasons = protection_reasons(layer, master_ids)
        if timestamp_name.fullmatch(layer_name) and not reasons:
            targets.append((glyph, layer_id, record))
        else:
            preserved_before.add((str(glyph.name), layer_id))

target_records = sorted(record for _, _, record in targets)
fingerprint_input = "\n".join(
    "\t".join(record) for record in target_records
).encode("utf-8")
fingerprint = hashlib.sha256(fingerprint_input).hexdigest()

assert len(targets) == EXPECTED_TARGET_COUNT, "The target count changed"
assert fingerprint == EXPECTED_TARGET_FINGERPRINT, (
    "The target fingerprint changed"
)
assert all(layer_id not in master_ids for _, layer_id, _ in targets), (
    "A master layer entered the target set"
)
assert all(
    not protection_reasons(glyph.layers[layer_id], master_ids)
    for glyph, layer_id, _ in targets
), "A protected layer entered the target set"

# Save first, then create a recoverable sibling copy.
font.save()
source_path = str(font.filepath)
stem, extension = os.path.splitext(source_path)
timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
backup_path = "%s.pre-dated-layer-cleanup-%s%s" % (
    stem,
    timestamp,
    extension,
)
counter = 1
while os.path.exists(backup_path):
    backup_path = "%s.pre-dated-layer-cleanup-%s-%d%s" % (
        stem,
        timestamp,
        counter,
        extension,
    )
    counter += 1

if os.path.isdir(source_path):
    shutil.copytree(source_path, backup_path)
else:
    shutil.copy2(source_path, backup_path)

font.disableUpdateInterface()
try:
    for glyph, layer_id, _ in targets:
        del glyph.layers[layer_id]
finally:
    font.enableUpdateInterface()

remaining_targets = []
preserved_after = set()
for glyph in font.glyphs:
    for master_id in master_ids:
        assert glyph.layers[master_id] is not None, (
            "A protected master layer is missing: %s / %s"
            % (glyph.name, master_id)
        )
    for layer in list(glyph.layers):
        layer_id = str(layer.layerId)
        layer_name = str(layer.name or "")
        reasons = protection_reasons(layer, master_ids)
        if timestamp_name.fullmatch(layer_name) and not reasons:
            remaining_targets.append((str(glyph.name), layer_name, layer_id))
        else:
            preserved_after.add((str(glyph.name), layer_id))

assert not remaining_targets, "Some dated layers remain"
assert preserved_after == preserved_before, "A preserved layer changed"
assert {str(master.id) for master in font.masters} == EXPECTED_MASTER_IDS, (
    "The master set changed"
)
assert len(font.glyphs) == EXPECTED_GLYPH_COUNT, "The glyph count changed"

font.save()
print("DELETED:", len(targets))
print("BACKUP:", backup_path)
print("SAVED:", font.filepath)
```

Report the deleted count, backup path, and saved font path. If the code raises
after deletion begins, do not save again; report the error and the backup path.

### 4. Verify the saved result independently

Run all of these tools again:

- `get_font_info`
- `get_masters`

Then rerun the Step 2 dry-run code. It must report:

- `targetCount` equal to `0`
- the same family, file path, glyph count, and master IDs
- every custom-named, special, intermediate, brace, bracket, color, and
  attributed layer still present

Confirm that the backup path printed in Step 3 exists. Report the final counts
and the recoverable backup location to the designer.

## Recovery

If verification fails, stop editing the current font. The Step 3 backup is a
complete pre-cleanup copy next to the source file. Restore it manually or open
it as a separate font; never overwrite the current source automatically.
