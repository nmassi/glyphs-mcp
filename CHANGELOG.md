# Changelog

## v0.2.0 — 2026-02-25

### New features

- **Master compatibility check** (`check_compatibility` tool / `POST /api/font/compatibility/check`)

  Checks all glyphs (or a subset) for interpolation compatibility across masters. Returns a formatted markdown report with only problematic glyphs.

  **What it checks:**
  - Path count — must match between masters
  - Node count per path — must match
  - Node types — line/curve/offcurve sequence must match per path
  - Path directions — CW/CCW must match per path
  - Path order — spatial position (bounding box center) of each path must correspond across masters. Detects when paths are drawn in different order (e.g. left stem first in Light, right stem first in Black)
  - Starting node position — first on-curve node of each path must be in a similar position across masters (threshold: 30% of glyph width or 100u)
  - Component count and names — must match
  - Anchor names — must match

  **How it marks glyphs in GlyphsApp:**
  - Red (0) = incompatible (structural or ordering mismatch)
  - Orange (1) = empty or partially drawn (missing drawing in one or more masters)
  - Green (4) = fully compatible

  **Report format:** Markdown table showing only incompatible and partially drawn glyphs. Compatible glyphs are not listed.

- **Kerning analysis** (`analyze_kerning` tool / `POST /api/font/kerning/analyze`)

  Analyzes kerning quality across all masters. Returns a formatted markdown report.

  **What it checks:**
  - Cross-master missing pairs — pair exists in some masters but not all (interpolates to/from 0)
  - Cross-master sign changes — pair is negative in one master, positive in another
  - Outlier values — kerning exceeding 40% of UPM
  - Redundant exceptions — glyph-level pairs that match their group value (can be removed)
  - Group orphans — Letter glyphs missing kerning group assignments

  **How it marks glyphs in GlyphsApp:**
  - Red (0) = cross-master issue (missing pair or sign change)
  - Yellow (3) = outlier value or quality warning

- **Spacing analysis** (`analyze_spacing` tool / `POST /api/font/spacing/analyze`)

  Analyzes spacing quality using scanline-based white space measurement. Returns a formatted markdown report.

  **What it checks:**
  - Sidebearing group consistency — n-group (h,i,k,l,m,n,p,r) and o-group (c,d,e,g,o,q) should have matching sidebearings, same for uppercase H-group and O-group
  - Symmetric glyph check — o, O, H, I, X should have LSB ≈ RSB
  - Reference ratios — n LSB / o LSB should be ~1.2–2.0
  - Cross-master spacing drift — spacing ratios relative to reference should be maintained across masters

  **How it marks glyphs in GlyphsApp:**
  - Red (0) = significant spacing inconsistency
  - Yellow (3) = minor deviation or asymmetry
  - Green (4) = passed

- **Unified markdown output for all analysis tools** — All analysis tools (`compare_stems`, `compare_color`, `audit_font_color`, `check_overshoots`, `compare_proportions`, `check_diagonal_weights`, `check_junctions`, `check_related_forms`, `check_punctuation`) now return formatted markdown tables instead of raw JSON. Only problematic glyphs are shown — passing glyphs are omitted. Consistent format across all tools.

### Improvements

- **Bridge timeout increased** from 10s to 30s — prevents timeouts on large fonts during analysis operations.

### Bug fixes

- **Node types not preserved when creating/setting paths** — `_str_to_node_type()` returned Glyphs 2 integer constants (1, 35, 65) instead of Glyphs 3 strings ("line", "curve", "offcurve"). All paths created via the API were rendered as straight lines. Fixed to return string types directly.

- **Undo/Redo not working for any write operation** — Most write handlers (`set_paths`, `create_glyph`, `set_width`, `set_color`, `rename`, `duplicate`, `set_unicode`, RMX filters, and all analysis color-marking) bypassed GlyphsApp's undo system. Cmd+Z would skip to the last state GlyphsApp knew about, often deleting all work. Fixed by wrapping all mutations:
  - **Layer changes** (paths, width, transforms): `layer.beginChanges()` / `layer.endChanges()`
  - **Glyph properties** (color, name, unicode): `glyph.beginUndo()` / `glyph.endUndo()`
  - **Font-level changes** (features, kerning): already tracked by GlyphsApp automatically

  Affected handlers:
  - `handle_set_glyph_paths` — paths
  - `handle_create_glyph` — paths on new glyph
  - `handle_set_width` — advance width
  - `handle_set_glyph_color` — color label
  - `handle_rename_glyph` — glyph name
  - `handle_duplicate_glyph` — paths on duplicated layers
  - `handle_set_glyph_unicode` — unicode value
  - `handle_rmx_harmonize` — curve optimization
  - `handle_rmx_scale` — scale transform
  - `handle_rmx_monospace` — monospace transform
  - `handle_compare_stems` — color marking
  - `handle_compare_color` — color marking
  - `handle_color_audit` — color marking
  - `handle_check_overshoots` — color marking
  - `handle_compare_proportions` — color marking
  - `handle_check_diagonals` — color marking
  - `handle_check_junctions` — color marking
  - `handle_check_related_forms` — color marking
  - `handle_check_punctuation` — color marking
