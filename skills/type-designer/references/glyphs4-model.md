# Glyphs 4 Model and Scripting

Use this reference for the conceptual model of the editor and for API and scripting decisions. It is not an API signature list.

## Document model

- A font has masters at design extremes located on axes, and can expose axes, instances, and a variable designspace.
- Each glyph holds one layer per master; layers carry paths, components, anchors, width, and metrics.
- Components reference other glyphs and resolve at render or export; paths are literal outlines.
- Anchors drive mark attachment and component alignment.
- Special or bracket layers handle non-interpolating designs and are not interpolated like master layers.
- Kerning groups, OpenType features, and custom parameters live at font or master scope.

## Glyphs 3 versus Glyphs 4

- Both versions are targeted. Do not assume a Glyphs 4-only feature exists in Glyphs 3; verify the running app version and behavior before relying on it.
- App behavior and the Python API change between major versions. Treat any remembered API detail as unverified until confirmed.

## Authoritative API knowledge

- Do not embed or trust memorized API signatures; the Python API surface is large and versioned.
- Consult the authoritative sources on demand: the Glyphs Handbook and the Glyphs Python API documentation, through a local documentation MCP when available, otherwise the official sites.
- Confirm class and method availability for the running version before generating code.

## Script versus plugin

- Use a script for a one-shot or explicitly triggered transformation.
- Build a plugin only when behavior must stay live, reactive, or integrated in the editor.
- Prefer a typed MCP tool over arbitrary code. Keep `execute_in_glyphs` as a last resort, define scope, rollback, and verification first, and never touch Glyphs objects from a background thread.
