# GlyphsMCP Operations

Use this reference only when GlyphsMCP is available. It maps type-design intent to safe operations; it does not replace visual judgment.

## Operating order

1. Inspect the live font with `get_font_info()`, `get_masters()`, `get_selection()`, `list_glyphs()`, or `get_glyph()` as appropriate.
2. For multi-step work, call `list_recipes()` and follow a matching recipe one step at a time.
3. Prefer a dedicated read or edit tool.
4. Use `execute_in_glyphs()` only when no dedicated operation covers the task or a runtime-only Glyphs behavior must be verified.

Never infer current font state from previous messages when a live inspection can answer it.

## Tool classes

| Class | Tools |
| --- | --- |
| Read-only state | `get_font_info`, `get_masters`, `get_selection`, `list_glyphs`, `get_glyph`, `get_glyph_svg`, `get_kerning`, `get_features` |
| Analysis and QA | `measure_stems`, `get_stem_targets`, `measure_color`, `analyze_spacing`, `analyze_kerning`, `analyze_kerning_groups`, `auto_kern`, `compare_proportions`, `check_overshoots`, `check_diagonal_weights`, `check_junctions`, `check_related_forms`, `check_punctuation`, `check_compatibility`, `check_glyphset_coverage`, `check_language_support`, `review_production` |
| Writes | `create_glyph`, `delete_glyph`, `set_glyph_paths`, `set_glyph_width`, `set_glyph_unicode`, `set_glyph_color`, `rename_glyph`, `duplicate_glyph`, `set_kerning_pair`, `delete_kerning_pair`, `set_feature_code` |
| Outline transforms | `smart_scale`, `rmx_harmonize`, `rmx_scale`, `rmx_tune`, `rmx_monospace`, `rmx_batch` |

Several analysis tools accept `mark_glyphs` and will recolor glyphs when it is true. Treat analysis as non-mutating only while no marking flag is set. Reads never authorize writes.

## Authorization boundary

Read-only analysis does not authorize mutation. Keep these separate:

- Analysis: measurements, compatibility checks, coverage reports, proofs, and recommendations
- Marking: glyph color labels, only with explicit intent and `mark_glyphs=True`
- Editing: outlines, widths, groups, kerning, features, Unicode, names, and metadata
- Persistence: saving source files or generated changes
- Delivery: exporting binaries or other artifacts

Permission for one category does not imply permission for another.

## Safe previews

- Kerning groups: start with `analyze_kerning_groups(apply=False)`.
- Auto-kerning: start with `auto_kern(..., dry_run=True)`.
- Scaling: inspect stems before and after; keep backups enabled.
- Glyph replacement: duplicate or preserve a source layer before `set_glyph_paths()`.
- Coverage: use `check_glyphset_coverage()` without `add_missing=True` unless glyph creation is authorized.
- Audits: omit `mark_glyphs` or keep it `False` unless coloring was requested.

## Intent-to-tool map

| Intent | First operations |
| --- | --- |
| Current structure | `get_font_info()`, `get_masters()`, `get_glyph()` |
| Stems and weight | `get_stem_targets()`, `measure_stems()`, `compare_stems()` |
| Color | `measure_color()`, `compare_color()`, `audit_font_color()` |
| Proportions | `compare_proportions()` |
| Spacing | `analyze_spacing()`, `get_spacing_strings()` |
| Kerning | kerning recipe, group preview, dry-run auto-kern, `analyze_kerning()` |
| Interpolation | `check_compatibility()` and intermediate-instance inspection |
| Optical details | overshoot, diagonal, junction, punctuation, and related-form checks |
| Production | `review_production()`, coverage and language checks |
| Naming | `check_font_name()` as collision screening, never legal clearance |

## Modification discipline

Make the smallest controlled change that tests the hypothesis. Work on a representative glyph or family before batch operations. After edits, repeat the baseline measurements and inspect related forms in every affected master.

When scaling, prefer operations with stem compensation over native affine transforms. Use RMX only when available and appropriate to the font's master setup. Do not confuse compatibility with optical success.

## Destructive and scripted work

For dotted, component-fill, or generated-outline prototypes, preserve original outlines in a backup or source layer and regenerate from that source. A decomposed copy may contain valid paths while reporting zero bounds; compute effective bounds from path and component bounds when necessary.

Use a script for one-shot transformations. Build a plugin only when the behavior must remain live and respond to editor changes. Before arbitrary execution, define input scope, expected output, rollback, and verification.

## Recipes

For multi-step work, call `list_recipes()`, then `get_recipe()` or `get_recipe_step()` and follow one step at a time. Bundled recipes include `audit_consistency`, `spacing_workflow`, `kerning_from_scratch`, `scale_proportions`, `master_compatibility`, and `create_glyphs_plugin`. Follow a matching recipe before improvising, and do not skip steps.

## Reporting

State which live font and masters were inspected, which tools ran, whether any mutation or marking occurred, and what validation followed. Export failures are communication-only: report logs and blockers rather than modifying the font to force a successful export.
