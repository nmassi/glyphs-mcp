#!/usr/bin/env python3
"""
glyphs_mcp_server.py — MCP Server for GlyphsApp integration.

Standalone process that Claude Code spawns via stdio.
Translates MCP tool calls into HTTP requests to the GlyphsMCP plugin
running inside GlyphsApp on http://127.0.0.1:7745.

Install: pip install "mcp[cli]"
Add to Claude Code: claude mcp add glyphs-mcp -- python3 glyphs_mcp_server.py

See ARCHITECTURE.md §4 for design details.
"""

import json
import urllib.request
import urllib.error
import base64
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("glyphs-mcp")

GLYPHS_URL = "http://127.0.0.1:7745"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str) -> dict:
    """GET request to the GlyphsApp plugin."""
    url = f"{GLYPHS_URL}{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to GlyphsApp plugin at {url}. Is GlyphsApp running with GlyphsMCP plugin? ({e})"}
    except Exception as e:
        return {"error": str(e)}


def _delete(path: str, body: dict = None) -> dict:
    """DELETE request to the GlyphsApp plugin."""
    url = f"{GLYPHS_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    try:
        req = urllib.request.Request(url, data=data, method="DELETE")
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to GlyphsApp plugin at {url}. Is GlyphsApp running with GlyphsMCP plugin? ({e})"}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict) -> dict:
    """POST request to the GlyphsApp plugin."""
    url = f"{GLYPHS_URL}{path}"
    data = json.dumps(body).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"error": f"Cannot connect to GlyphsApp plugin at {url}. Is GlyphsApp running with GlyphsMCP plugin? ({e})"}
    except Exception as e:
        return {"error": str(e)}


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_font_info() -> dict:
    """Get information about the currently open font in GlyphsApp.

    Returns font family name, units per em, glyph count, masters, axes,
    metrics (ascender, descender, x-height, cap height), and instances.
    """
    return _get("/api/font")


@mcp.tool()
def list_glyphs() -> dict:
    """List all glyphs in the open font with basic metadata.

    Returns glyph names, unicodes, layer count, script, category.
    Does NOT include path data — use get_glyph() for full details.
    """
    return _get("/api/font/glyphs")


@mcp.tool()
def get_glyph(glyph_name: str) -> dict:
    """Get complete data for a specific glyph including all paths and metrics.

    Returns all layers with: paths (nodes with x,y coordinates, type, smooth),
    components, anchors, width, sidebearings.

    Node types: "line" (straight on-curve), "curve" (smooth on-curve),
    "offcurve" (cubic bezier control point).
    Path direction: -1 = counter-clockwise (outer contours),
    1 = clockwise (inner contours/counters).
    """
    return _get(f"/api/font/glyphs/{glyph_name}")


@mcp.tool()
def get_glyph_svg(glyph_name: str, master_id: str = "") -> dict:
    """Get a glyph rendered as SVG markup.

    Returns SVG with viewBox matching the glyph's metrics.
    Useful for seeing the glyph shape as a vector image.
    """
    path = f"/api/font/glyphs/{glyph_name}/svg"
    if master_id:
        path += f"?master={master_id}"
    return _get(path)


@mcp.tool()
def get_selection() -> dict:
    """Get the user's current selection in GlyphsApp's editor.

    Returns the active glyph name, layer, and any selected paths or nodes.
    Returns null selection if no tab or layer is active.
    """
    return _get("/api/font/selection")


@mcp.tool()
def get_masters() -> dict:
    """Get all font masters with their metrics and axis positions.

    Returns master ID, name, ascender, descender, x-height, cap-height,
    and axis values for each master.
    """
    return _get("/api/font/masters")


@mcp.tool()
def get_kerning(master_id: str = "") -> dict:
    """Get kerning pairs for a specific master (or first master if not specified)."""
    path = "/api/font/kerning"
    if master_id:
        path += f"?master={master_id}"
    return _get(path)


@mcp.tool()
def get_features() -> dict:
    """Get OpenType feature code from the font."""
    return _get("/api/font/features")


@mcp.tool()
def set_glyph_paths(glyph_name: str, paths: list[dict], master_id: str = "") -> dict:
    """Replace ALL paths on a glyph's layer in GlyphsApp.

    This OVERWRITES existing paths. The glyph must already exist.

    Each path is: {"closed": true, "nodes": [{"x": 100, "y": 0, "type": "line", "smooth": false}, ...]}

    Node types:
      - "line": on-curve point with straight connection to previous
      - "curve": on-curve point with smooth cubic bezier connection
      - "offcurve": cubic bezier control handle (always in pairs before a "curve")
      - Curve segments: offcurve, offcurve, curve (3 nodes per segment)

    Path direction: outer contours counter-clockwise, counters/holes clockwise.
    correctPathDirection() is called automatically after setting paths.

    If master_id is empty, uses the first master.
    """
    body = {"paths": paths}
    if master_id:
        body["masterId"] = master_id
    return _post(f"/api/font/glyphs/{glyph_name}/paths", body)


@mcp.tool()
def create_glyph(glyph_name: str, width: float = 600, unicode_value: str = "", paths: list[dict] = []) -> dict:
    """Create a new glyph in the open font.

    Args:
        glyph_name: Name for the new glyph (e.g., "a.ss01", "uni0041")
        width: Advance width in font units
        unicode_value: Optional unicode value (e.g., "0061" for 'a')
        paths: Optional initial paths (same format as set_glyph_paths)
    """
    body = {"name": glyph_name, "width": width}
    if unicode_value:
        body["unicode"] = unicode_value
    if paths:
        body["paths"] = paths
    return _post("/api/font/glyphs", body)


@mcp.tool()
def set_glyph_width(glyph_name: str, width: float, master_id: str = "") -> dict:
    """Set the advance width of a glyph."""
    body = {"width": width}
    if master_id:
        body["masterId"] = master_id
    return _post(f"/api/font/glyphs/{glyph_name}/width", body)


@mcp.tool()
def set_kerning_pair(left: str, right: str, value: float, master_id: str = "") -> dict:
    """Set a kerning pair value between two glyphs.

    Args:
        left: Left glyph name or group (@MMK_L_...)
        right: Right glyph name or group (@MMK_R_...)
        value: Kerning value (negative = tighter)
        master_id: Optional master ID (uses first master if empty)
    """
    body = {"left": left, "right": right, "value": value}
    if master_id:
        body["masterId"] = master_id
    return _post("/api/font/kerning", body)


@mcp.tool()
def set_glyph_color(glyph_name: str, color: int) -> dict:
    """Set the color label of a glyph in the font view.

    Color index (0–11):
      0=red, 1=orange, 2=brown, 3=yellow, 4=light green,
      5=dark green, 6=teal, 7=blue, 8=purple, 9=pink, 10=light gray, 11=charcoal

    Use None/no color by setting any value outside this range.
    """
    return _post(f"/api/font/glyphs/{glyph_name}/color", {"color": color})


@mcp.tool()
def delete_glyph(glyph_name: str) -> dict:
    """Delete a glyph from the font. This cannot be undone via MCP."""
    return _delete(f"/api/font/glyphs/{glyph_name}")


@mcp.tool()
def rename_glyph(glyph_name: str, new_name: str) -> dict:
    """Rename a glyph. Fails if new_name already exists."""
    return _post(f"/api/font/glyphs/{glyph_name}/rename", {"newName": new_name})


@mcp.tool()
def duplicate_glyph(glyph_name: str, new_name: str) -> dict:
    """Duplicate a glyph (all layers and paths) under a new name.

    Useful for creating alternates or backups before editing.
    """
    return _post(f"/api/font/glyphs/{glyph_name}/duplicate", {"newName": new_name})


@mcp.tool()
def set_glyph_unicode(glyph_name: str, unicode_value: str) -> dict:
    """Set the unicode value of a glyph.

    Args:
        glyph_name: Name of the glyph to update
        unicode_value: Hex unicode string e.g. "0061" for 'a'. Pass "" to clear.
    """
    return _post(f"/api/font/glyphs/{glyph_name}/unicode", {"unicode": unicode_value or None})


@mcp.tool()
def delete_kerning_pair(left: str, right: str, master_id: str = "") -> dict:
    """Delete a kerning pair.

    Args:
        left: Left glyph name or group (@MMK_L_...)
        right: Right glyph name or group (@MMK_R_...)
        master_id: Optional master ID (uses first master if empty)
    """
    body = {"left": left, "right": right}
    if master_id:
        body["masterId"] = master_id
    return _delete("/api/font/kerning", body)


@mcp.tool()
def set_feature_code(feature_name: str, code: str, active: bool = True) -> dict:
    """Create or update an OpenType feature.

    Args:
        feature_name: Feature tag e.g. "liga", "kern", "ss01"
        code: OpenType feature code (AFDKO syntax)
        active: Whether the feature is enabled (default True)
    """
    return _post(f"/api/font/features/{feature_name}", {"code": code, "active": active})


# ── RMX Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def rmx_harmonize(glyph_name: str, mode: str = "harmonize", master_id: str = "") -> dict:
    """Optimize bezier curves on a glyph using RMX Harmonizer.

    Modes:
      - "harmonize": Full curve optimization (recommended default)
      - "dekink": Only fix kinks at smooth connections
      - "extract handles": Reset handles to default positions
      - "supersmooth diagonals": Extra smoothing on diagonal segments
      - "supersmooth all": Maximum smoothing everywhere

    Use after drawing or modifying paths to ensure clean curves.
    Works on any glyph — no multi-master requirement.
    """
    body = {"glyphName": glyph_name, "mode": mode}
    if master_id:
        body["masterId"] = master_id
    return _post("/api/filters/rmx/harmonize", body)


@mcp.tool()
def rmx_scale(glyph_name: str, width: int = 100, height: int = 100,
              weight: int = 0, adjust_space: int = 0,
              vertical_shift: int = 0, master_id: str = "") -> dict:
    """Scale a glyph by percentage in width and/or height.

    USE THIS for percentage-based scaling requests like "make 30% wider".

    Tries RMX Scaler first (stroke weight compensation via master interpolation).
    Falls back to native affine transform if RMX headless API is unavailable
    (GlyphsApp 3.5+ changed internal APIs). Response includes "method" field:
    "rmx" = RMX Scaler was used, "native_transform" = affine transform fallback.

    Args:
        glyph_name: Name of the glyph to scale
        width: Width scale as percentage. 100 = no change, 130 = 30% wider, 70 = 30% narrower
        height: Height scale as percentage. 100 = no change
        weight: Stroke weight delta (RMX only, ignored in native fallback)
        adjust_space: Sidebearing adjustment delta
        vertical_shift: Vertical position offset
        master_id: Optional master ID (empty = first master)

    Examples:
        "Make R 30% wider" → rmx_scale("R", width=130)
        "Make R 20% narrower" → rmx_scale("R", width=80)
        "Scale R to 90% height, keep width" → rmx_scale("R", height=90)
    """
    body = {
        "glyphName": glyph_name,
        "width": width,
        "height": height,
        "weight": weight,
        "adjustSpace": adjust_space,
        "verticalShift": vertical_shift,
    }
    if master_id:
        body["masterId"] = master_id
    return _post("/api/filters/rmx/scale", body)


@mcp.tool()
def rmx_tune(glyph_name: str, weight: int = 0, width: int = 0,
             height: int = 0, slant: int = 0, fixed_width: bool = False,
             master_id: str = "") -> dict:
    """Adjust a glyph's weight, width, height, or slant using RMX Tuner.

    USE THIS for qualitative adjustments like "make bolder" or "add italic slant".
    For percentage-based width/height changes, prefer rmx_scale() instead.

    IMPORTANT: Values are NOT percentages — they are relative adjustment deltas
    in arbitrary units. Typical useful range: -100 to +100.

    Internally uses master interpolation along the font's weight axis for
    weight/width/height, and native affine shear for slant.  Requires 2+
    masters.  All changes are undoable (Cmd+Z).

    Args:
        glyph_name: Name of the glyph
        weight: Stroke weight delta (+ = bolder, - = lighter)
        width: Horizontal expansion delta (+ = wider, - = narrower)
        height: Vertical expansion delta (+ = taller, - = shorter)
        slant: Italic slant in degrees (+ = right lean)
        fixed_width: Keep advance width unchanged during adjustment
        master_id: Optional master ID

    Examples:
        "Make R bolder" → rmx_tune("R", weight=30)
        "Make R much lighter" → rmx_tune("R", weight=-50)
        "Add 12° italic slant to R" → rmx_tune("R", slant=12)
        "Make R bolder but keep same width" → rmx_tune("R", weight=30, fixed_width=True)

    Requires 2+ masters in the font. Returns width before and after.
    """
    body = {
        "glyphName": glyph_name,
        "weight": weight,
        "width": width,
        "height": height,
        "slant": slant,
        "fixedWidth": fixed_width,
    }
    if master_id:
        body["masterId"] = master_id
    return _post("/api/filters/rmx/tune", body)


@mcp.tool()
def rmx_monospace(glyph_name: str, mono_width: int = 0,
                  keep_stroke: int = 100, use_spacing: int = 40,
                  master_id: str = "") -> dict:
    """Adjust a glyph to a fixed width using RMX Monospacer.

    Intelligently distributes width change between outline scaling and
    spacing adjustment to maintain visual quality.

    Args:
        glyph_name: Name of the glyph
        mono_width: Target advance width (0 = keep current width)
        keep_stroke: How much to preserve stroke weight, 0-100%
        use_spacing: How much width change goes to spacing vs outline, 0-100%
        master_id: Optional master ID

    Example:
        "Make all uppercase letters 600 units wide" →
        rmx_batch("monospace", ["A","B",...,"Z"], params={"monoWidth": 600})

    Requires 2+ masters.
    """
    body = {"glyphName": glyph_name}
    if mono_width:
        body["monoWidth"] = mono_width
    body["keepStroke"] = keep_stroke
    body["useSpacing"] = use_spacing
    if master_id:
        body["masterId"] = master_id
    return _post("/api/filters/rmx/monospace", body)


@mcp.tool()
def rmx_batch(filter_name: str, glyph_names: list[str],
              params: dict = {}, master_id: str = "") -> dict:
    """Apply an RMX filter to multiple glyphs at once.

    Args:
        filter_name: One of "harmonize", "tune", "scale", "monospace"
        glyph_names: List of glyph names to process
        params: Filter-specific parameters (same as individual tool params)
        master_id: Optional master ID

    Examples:
        "Harmonize all lowercase" →
        rmx_batch("harmonize", ["a","b",...,"z"], params={"mode": "harmonize"})

        "Make all caps 20% wider" →
        rmx_batch("scale", ["A","B",...,"Z"], params={"width": 120})
    """
    body = {
        "filter": filter_name,
        "glyphNames": glyph_names,
        "params": params,
    }
    if master_id:
        body["masterId"] = master_id
    return _post("/api/filters/rmx/batch", body)


@mcp.tool()
def execute_in_glyphs(code: str) -> dict:
    """Execute arbitrary Python code inside GlyphsApp.

    The code runs with access to the Glyphs object and all GlyphsApp API.
    stdout is captured and returned. This is powerful but must be used carefully.

    NOTE: This endpoint is disabled by default. The user must enable it
    in GlyphsApp preferences (com.glyphsmcp.allowExecute = True).

    Example: execute_in_glyphs("print(Glyphs.font.familyName)")
    """
    return _post("/api/execute", {"code": code})


# ── Analysis report formatters ────────────────────────────────────────────────

def _format_verdict_table(master_data: dict, masters_key="masters") -> str:
    """Generic formatter for per-master, per-glyph verdict tools.

    Works with compare_stems, compare_color, audit_font_color.
    Structure: {masters: {name: {glyphs: [{glyph, value, verdict, ...}], summary: {...}}}}
    """
    data = master_data
    masters = data.get(masters_key, {})
    if not masters:
        return str(data)

    lines = []

    for mname, md in masters.items():
        mid = md.get("masterId", "")
        glyphs = md.get("glyphs", [])
        summary = md.get("summary", {})
        ref = md.get("reference", {})

        lines.append(f"### {mname}")

        # Reference info
        ref_parts = []
        for case_key in ("lowercase", "uppercase"):
            r = ref.get(case_key, {})
            if r:
                ref_parts.append(f"{r.get('glyph', '?')}={r.get('verticalStem', r.get('density', '?'))}")
        if ref_parts:
            lines.append(f"Reference: {', '.join(ref_parts)}")
        lines.append("")

        # Only show non-passing glyphs
        issues = [g for g in glyphs if g.get("verdict") not in ("pass",)]
        if not issues:
            lines.append(f"All {len(glyphs)} glyphs passed.")
            lines.append("")
            continue

        lines.append("| Glyph | Value | Ref | Dev | Verdict |")
        lines.append("|-------|-------|-----|-----|---------|")
        for g in issues[:50]:
            dev = g.get("deviation", "")
            dev_str = f"+{dev}" if isinstance(dev, (int, float)) and dev > 0 else str(dev)
            note = g.get("note", "")
            verdict = g.get("verdict", "")
            if note:
                verdict = f"{verdict} ({note})"
            lines.append(f"| {g['glyph']} | {g.get('value', '')} | {g.get('reference', '')} | {dev_str} | {verdict} |")
        if len(issues) > 50:
            lines.append(f"| ... | | | | +{len(issues) - 50} more |")
        lines.append("")

        # Summary
        parts = [f"{k}: {v}" for k, v in summary.items() if v > 0]
        lines.append(f"**Summary:** {', '.join(parts)}")
        lines.append("")

    return "\n".join(lines)


def _format_compare_stems(data: dict) -> str:
    """Format compare_stems results."""
    if "error" in data and "masters" not in data:
        return data.get("error", "Unknown error")
    return f"## Stem Comparison\n\n{_format_verdict_table(data)}"


def _format_compare_color(data: dict) -> str:
    """Format compare_color results."""
    if "error" in data and "masters" not in data:
        return data.get("error", "Unknown error")
    return f"## Color Comparison\n\n{_format_verdict_table(data)}"


def _format_audit_color(data: dict) -> str:
    """Format audit_font_color results."""
    if "error" in data and "masters" not in data:
        return data.get("error", "Unknown error")
    return f"## Font Color Audit\n\n{_format_verdict_table(data)}"


def _format_overshoots(data: dict) -> str:
    """Format check_overshoots results."""
    masters = data.get("masters", {})
    if not masters:
        return str(data)

    lines = ["## Overshoot Check", ""]

    for mname, md in masters.items():
        glyphs = md.get("glyphs", [])
        stats = md.get("statistics", {})

        lines.append(f"### {mname}")
        lines.append("")

        issues = [g for g in glyphs if g.get("verdict") != "pass"]
        if not issues:
            lines.append(f"All {len(glyphs)} glyphs passed.")
            lines.append("")
            # Show averages
            avg_rt = stats.get("avgRoundTopOvershoot", 0)
            avg_rb = stats.get("avgRoundBottomOvershoot", 0)
            if avg_rt or avg_rb:
                lines.append(f"Avg round overshoot: top={avg_rt}u, bottom={avg_rb}u")
                lines.append("")
            continue

        lines.append("| Glyph | Type | Top OS | Bottom OS | Top % | Bottom % | Verdict |")
        lines.append("|-------|------|--------|-----------|-------|----------|---------|")
        for g in issues[:50]:
            lines.append(f"| {g['glyph']} | {g.get('type','')} | {g.get('topOvershoot','')} | {g.get('bottomOvershoot','')} | {g.get('topPct','')}% | {g.get('bottomPct','')}% | {g['verdict']} |")
        lines.append("")

    return "\n".join(lines)


def _format_proportions(data: dict) -> str:
    """Format compare_proportions results."""
    masters = data.get("masters", {})
    if not masters:
        return str(data)

    lines = ["## Width Proportions", ""]

    for mid, md in masters.items():
        mname = md.get("masterName", mid)
        refs = md.get("references", {})
        groups = md.get("groups", [])
        order_v = md.get("orderViolations", [])
        range_o = md.get("rangeOutliers", [])
        summary = md.get("summary", {})

        lines.append(f"### {mname}")
        ref_str = ", ".join(f"{k}={v}u" for k, v in refs.items())
        lines.append(f"Reference: {ref_str}")
        lines.append("")

        has_issues = groups or order_v or range_o
        if not has_issues:
            lines.append(f"All {summary.get('glyphsChecked', 0)} glyphs passed.")
            lines.append("")
            continue

        if groups:
            lines.append(f"**Group inconsistencies ({len(groups)}):**")
            lines.append("")
            lines.append("| Group | Issue |")
            lines.append("|-------|-------|")
            for g in groups[:30]:
                lines.append(f"| {g.get('group', '')} | {g.get('issue', '')} |")
            lines.append("")

        if order_v:
            lines.append(f"**Ordering violations ({len(order_v)}):**")
            lines.append("")
            for v in order_v[:20]:
                lines.append(f"- {v.get('issue', v)}")
            lines.append("")

        if range_o:
            lines.append(f"**Outside industry range ({len(range_o)}):**")
            lines.append("")
            lines.append("| Glyph | Ratio | Expected |")
            lines.append("|-------|-------|----------|")
            for o in range_o[:30]:
                rng = o.get("range", [0, 0])
                lines.append(f"| {o['glyph']} | {o['ratio']}% | {rng[0]}–{rng[1]}% |")
            lines.append("")

    return "\n".join(lines)


def _format_diagonals(data: dict) -> str:
    """Format check_diagonal_weights results."""
    masters = data.get("masters", {})
    if not masters:
        return str(data)

    lines = ["## Diagonal Weights", ""]

    for mid, md in masters.items():
        mname = md.get("masterName", mid)
        glyphs = md.get("glyphs", [])
        groups = md.get("groups", [])
        summary = md.get("summary", {})

        lines.append(f"### {mname}")
        lines.append("")

        issues = [g for g in glyphs if g.get("verdict") not in ("pass",)]
        group_issues = [g for g in groups if g.get("issues")]

        if not issues and not group_issues:
            lines.append(f"All {len(glyphs)} glyphs passed.")
            lines.append("")
            continue

        if issues:
            lines.append("| Glyph | Stem | Ref | Ratio | Verdict |")
            lines.append("|-------|------|-----|-------|---------|")
            for g in issues[:30]:
                lines.append(f"| {g['glyph']} | {g.get('stem', '')} | {g.get('reference', '')} | {g.get('ratio', '')}% | {g['verdict']} |")
            lines.append("")

        if group_issues:
            for gi in group_issues[:10]:
                lines.append(f"**Group {gi.get('group', '')}:** {', '.join(str(i) for i in gi.get('issues', []))}")
            lines.append("")

    return "\n".join(lines)


def _format_junctions(data: dict) -> str:
    """Format check_junctions results."""
    masters = data.get("masters", {})
    if not masters:
        return str(data)

    lines = ["## Junction Thinning", ""]

    for mid, md in masters.items():
        mname = md.get("masterName", mid)
        glyphs = md.get("glyphs", [])
        groups = md.get("groups", [])

        lines.append(f"### {mname}")
        lines.append("")

        group_issues = [g for g in groups if g.get("issues")]
        if not group_issues:
            lines.append(f"All {len(glyphs)} glyphs passed.")
            lines.append("")
            continue

        lines.append("| Glyph | Thinning % | Mid stem | Junction min |")
        lines.append("|-------|------------|----------|--------------|")
        for g in glyphs:
            if g.get("thinning") is not None:
                lines.append(f"| {g['glyph']} | {g['thinning']}% | {g.get('midStem', '')} | {g.get('junctionMin', '')} |")
        lines.append("")

        for gi in group_issues[:10]:
            lines.append(f"**Group {gi.get('group', '')}:** {', '.join(str(i) for i in gi.get('issues', []))}")
        lines.append("")

    return "\n".join(lines)


def _format_related_forms(data: dict) -> str:
    """Format check_related_forms results."""
    masters = data.get("masters", {})
    if not masters:
        return str(data)

    lines = ["## Related Forms", ""]

    for mid, md in masters.items():
        mname = md.get("masterName", mid)
        pairs = md.get("pairs", [])

        lines.append(f"### {mname}")
        lines.append("")

        issues = [p for p in pairs if p.get("verdict") != "pass"]
        if not issues:
            lines.append(f"All {len(pairs)} pairs passed.")
            lines.append("")
            continue

        lines.append("| Pair | Ratio | Expected | Verdict |")
        lines.append("|------|-------|----------|---------|")
        for p in pairs:
            exp = p.get("expected", [0, 0])
            v = p.get("verdict", "")
            mark = " *" if v != "pass" else ""
            lines.append(f"| {p['pair']} | {p['ratio']}% | {exp[0]}–{exp[1]}% | {v}{mark} |")
        lines.append("")

    return "\n".join(lines)


def _format_punctuation(data: dict) -> str:
    """Format check_punctuation results."""
    masters = data.get("masters", {})
    if not masters:
        return str(data)

    lines = ["## Punctuation Consistency", ""]

    for mid, md in masters.items():
        mname = md.get("masterName", mid)
        width_matches = md.get("widthMatches", [])
        ratio_checks = md.get("ratioChecks", [])

        lines.append(f"### {mname}")
        lines.append("")

        match_issues = [m for m in width_matches if m.get("verdict") != "pass"]
        ratio_issues = [r for r in ratio_checks if r.get("verdict") != "pass"]

        if not match_issues and not ratio_issues:
            total = len(width_matches) + len(ratio_checks)
            lines.append(f"All {total} checks passed.")
            lines.append("")
            continue

        if match_issues:
            lines.append(f"**Width mismatches ({len(match_issues)}):**")
            lines.append("")
            lines.append("| Pair | Width A | Width B | Diff % | Verdict |")
            lines.append("|------|---------|---------|--------|---------|")
            for m in match_issues[:30]:
                lines.append(f"| {m.get('left', '')}/{m.get('right', '')} | {m.get('widthA', '')} | {m.get('widthB', '')} | {m.get('diffPct', '')}% | {m['verdict']} |")
            lines.append("")

        if ratio_issues:
            lines.append(f"**Ratio issues ({len(ratio_issues)}):**")
            lines.append("")
            lines.append("| Pair | Ratio | Expected | Verdict |")
            lines.append("|------|-------|----------|---------|")
            for r in ratio_issues[:30]:
                exp = r.get("expected", [0, 0])
                lines.append(f"| {r.get('pair', '')} | {r.get('ratio', '')}% | {exp[0]}–{exp[1]}% | {r['verdict']} |")
            lines.append("")

    return "\n".join(lines)


# ── Stem Measurement ─────────────────────────────────────────────────────────

@mcp.tool()
def measure_stems(glyph_name: str, master_id: str = "",
                  samples: int = 10) -> dict:
    """Measure stem thicknesses in a glyph using ray-casting.

    Casts horizontal and vertical rays through the glyph outlines to
    measure vertical stems (like the main strokes of n, m, h) and
    horizontal stems (like crossbars of e, A, H).

    Returns:
        - Dominant (most common) vertical and horizontal stem values
        - Min/max range showing consistency within the glyph
        - Detailed per-ray measurements with positions

    Use this BEFORE and AFTER applying RMX filters to verify the effect.

    Examples:
        "How thick are the stems of R?" -> measure_stems("R")
        "What's the crossbar thickness of H?" -> measure_stems("H")
    """
    path = f"/api/font/glyphs/{glyph_name}/stems?samples={samples}"
    if master_id:
        path += f"&master={master_id}"
    return _get(path)


@mcp.tool()
def compare_stems(glyph_names: list[str], master_id: str = "") -> str:
    """Compare stem thicknesses across multiple glyphs to find inconsistencies.

    Evaluates each glyph against industry stem patterns for optical
    compensation. Per-glyph verdicts:
    - pass: stem within expected tolerance (green in GlyphsApp)
    - compensation: known optical compensation like round stems, bowl mass
      (yellow) — only flagged if OUTSIDE the expected range
    - inconsistent: real issue, deviation exceeds industry norms (red)
    - unreliable: glyph shape can't be reliably measured (orange)

    Each glyph is classified into one of 5 groups:
    - straight: pure stems (n,h,m,u,i,j,l,r / H,I,L,T,U,F,E,K,J)
    - round: pure round forms (o,c / O,C,Q)
    - mixed: stem + bowl (b,d,p,q,g,a,e,s / D,B,P,R,G)
    - diagonal: diagonal strokes (v,w,x,y,z,k / V,W,X,Y,Z,A,M,N)
    - optical/figure: special cases (t,f) and numbers (0-9)

    Supports uppercase (ref: H), lowercase (ref: n), and figures (ref: H).

    If master_id is omitted, analyzes ALL masters and returns per-master
    results. Glyph color = worst verdict across all masters.
    If master_id is provided, analyzes that single master only.

    Marks results directly in GlyphsApp with colors:
      Red=inconsistent, Orange=unreliable, Yellow=compensation, Green=pass

    Args:
        glyph_names: List of glyphs to compare
        master_id: Optional master ID (empty = all masters)
    """
    body = {"glyphNames": glyph_names}
    if master_id:
        body["masterId"] = master_id
    return _format_compare_stems(_post("/api/font/stems/compare", body))


@mcp.tool()
def get_stem_targets(master_id: str = "") -> dict:
    """Get the designer's intended stem values from the Dimensions palette.

    Returns stem snap values from custom parameters AND measured values from
    reference glyphs (H, O, n, o). These are the TARGET values that other
    glyphs should match.

    Use this as baseline for compare_stems — to check if glyphs hit the
    designer's intended values, not just if they're consistent with each other.
    """
    path = "/api/font/stems/targets"
    if master_id:
        path += f"?master={master_id}"
    return _get(path)


# ── Typographic Color ────────────────────────────────────────────────────────

@mcp.tool()
def measure_color(glyph_name: str, master_id: str = "") -> dict:
    """Measure the ink density (typographic color) of a single glyph.

    Ink density = ratio of filled area to total bounding box.
    Normalizes by the appropriate zone height (x-height for lowercase,
    cap-height for uppercase) so values are comparable across glyphs.

    Returns a value between 0.0 and 1.0 (typically 0.15-0.50 for text fonts).

    Higher = darker/heavier glyph. Lower = lighter.
    """
    path = f"/api/font/glyphs/{glyph_name}/ink-density"
    if master_id:
        path += f"?master={master_id}"
    return _get(path)


@mcp.tool()
def compare_color(glyph_names: list[str], master_id: str = "") -> str:
    """Compare typographic color (ink density) across multiple glyphs.

    Finds glyphs that are visually too dark or too light compared to the group.
    Uses per-glyph expected density ratios from industry patterns — each glyph
    gets a verdict: pass, compensation, inconsistent, or unreliable.

    Reference glyphs: n (lowercase), H (uppercase).

    If master_id is omitted, analyzes ALL masters and returns per-master
    results. Glyph color in GlyphsApp = worst verdict across all masters.

    Auto-marks glyphs in GlyphsApp:
    - Red (0) = inconsistent density
    - Orange (1) = unreliable measurement
    - Yellow (3) = optical compensation (expected)
    - Green (4) = pass

    Args:
        glyph_names: Glyphs to compare
        master_id: Optional master ID (empty = all masters)

    Examples:
        "Is the color consistent across uppercase?" ->
        compare_color(["H","I","M","N","O","B","D","E","F","K","L","P","R"])
    """
    body = {"glyphNames": glyph_names}
    if master_id:
        body["masterId"] = master_id
    return _format_compare_color(_post("/api/font/color/compare", body))


@mcp.tool()
def audit_font_color(master_id: str = "") -> str:
    """Full font color audit — analyzes ALL letter glyphs grouped by category.

    Groups glyphs into uppercase, lowercase, and figures, then evaluates
    each against expected density ratios from industry patterns.

    If master_id is omitted, analyzes ALL masters with worst-verdict-wins
    for glyph colors in GlyphsApp.

    Auto-marks glyphs: red=inconsistent, orange=unreliable, yellow=compensation, green=pass.

    The lowercase-to-uppercase density ratio is typically 1.10-1.16 in professional fonts.

    This is the comprehensive "is my font's color even?" check.
    Use this before final production to catch any weight inconsistencies.
    """
    body = {}
    if master_id:
        body["masterId"] = master_id
    return _format_audit_color(_post("/api/font/color/audit", body))


@mcp.tool()
def check_overshoots(glyph_names: list[str] = None, master_id: str = "") -> str:
    """Check overshoot values for round and pointed forms.

    Round forms (O, o, C, S, etc.) should overshoot baseline and zone top
    by ~1-2% of zone height. Pointed forms (A, V, W) need MORE overshoot
    than rounds to appear optically aligned.

    If no glyph_names provided, checks all known overshoot-sensitive glyphs
    in the font (O,C,D,G,Q,S,U,A,V,W,M,N,o,c,e,s,b,d,p,q,g,a,u,v,w,y + figures).

    If master_id is omitted, analyzes ALL masters.

    Auto-marks glyphs: red=missing/excessive overshoot, green=pass.

    Args:
        glyph_names: Optional list of glyphs to check (default: all overshoot glyphs)
        master_id: Optional master ID (empty = all masters)
    """
    body = {}
    if glyph_names:
        body["glyphNames"] = glyph_names
    if master_id:
        body["masterId"] = master_id
    return _format_overshoots(_post("/api/font/overshoots/check", body))


@mcp.tool()
def compare_proportions(glyph_names: list[str] = None, master_id: str = "") -> str:
    """Compare width proportions across glyphs within a font.

    Checks three things:
    1. Related-form groups: b≈d≈p≈q (mirrored), h≈n≈u (arch), O≈Q, etc.
    2. Width ordering: m>n>r, W>H>I — flags violations
    3. Industry ranges from 18 professional fonts — flags outliers

    References: n for lowercase, H for uppercase/figures.

    If no glyph_names provided, checks all LC + UC + figures.
    If master_id is omitted, analyzes ALL masters.

    Auto-marks glyphs: red=group inconsistency or ordering violation,
    yellow=outside industry range, green=pass.

    Args:
        glyph_names: Optional list of glyphs to check
        master_id: Optional master ID (empty = all masters)
    """
    body = {}
    if glyph_names:
        body["glyphNames"] = glyph_names
    if master_id:
        body["masterId"] = master_id
    return _format_proportions(_post("/api/font/proportions/compare", body))


@mcp.tool()
def check_diagonal_weights(glyph_names: list[str] = None, master_id: str = "") -> str:
    """Check diagonal stroke weight consistency and ratio to vertical stems.

    Measures perpendicular thickness of diagonal strokes (V, A, W, X, Y, Z,
    v, w, x, y, z, k, M, N) and checks:
    1. Related diagonal groups are consistent (V≈A≈W, v≈w≈y, etc.)
    2. Diagonal/straight ratio within professional range (typically 85-100%)

    If no glyph_names provided, checks all diagonal glyphs.
    If master_id is omitted, analyzes ALL masters.

    Auto-marks glyphs: red=group inconsistency, yellow=ratio outside range, green=pass.

    Args:
        glyph_names: Optional list of glyphs to check
        master_id: Optional master ID (empty = all masters)
    """
    body = {}
    if glyph_names:
        body["glyphNames"] = glyph_names
    if master_id:
        body["masterId"] = master_id
    return _format_diagonals(_post("/api/font/diagonals/check", body))


@mcp.tool()
def check_junctions(glyph_names: list[str] = None, master_id: str = "") -> str:
    """Check junction thinning consistency across related glyphs.

    Measures how stems thin at arch/bowl junctions (n, m, b, d, p, q, etc.)
    by sweeping horizontal rays at multiple heights. Reports thinning %
    (100% = no thinning, 80% = stem thins to 80% at junction).

    Checks consistency within groups (n≈m, b≈p, d≈q). Does NOT flag
    based on absolute values — thinning is design-specific. Only flags
    inconsistencies between related forms.

    If master_id is omitted, analyzes ALL masters.

    Auto-marks glyphs: red=group inconsistency, green=pass.

    Args:
        glyph_names: Optional list of glyphs to check (default: n,h,m,u,a,b,d,p,q)
        master_id: Optional master ID (empty = all masters)
    """
    body = {}
    if glyph_names:
        body["glyphNames"] = glyph_names
    if master_id:
        body["masterId"] = master_id
    return _format_junctions(_post("/api/font/junctions/check", body))


@mcp.tool()
def check_related_forms(master_id: str = "") -> str:
    """Cross-validate related figures and letters (0↔O, 6↔9, 8↔S, 3↔B, etc.).

    Based on industry patterns across professional fonts.
    Checks width ratios between pairs that should be structurally related:

    - six ≈ nine: rotated forms, should match (high severity)
    - zero < O: zero narrower and lighter (medium)
    - three ≈ five: related open-bowl figures (medium)
    - three < B: three narrower than B (medium)
    - eight ~ S: related S-shape (low/informational)
    - one > I: one wider due to flag/crossbar (low/informational)

    If master_id is omitted, analyzes ALL masters.

    Auto-marks glyphs: red=high-severity failure, yellow=medium warning, green=pass.

    Args:
        master_id: Optional master ID (empty = all masters)
    """
    body = {}
    if master_id:
        body["masterId"] = master_id
    return _format_related_forms(_post("/api/font/related-forms/check", body))


@mcp.tool()
def check_punctuation(master_id: str = "") -> str:
    """Check punctuation consistency: mirrored pairs, width matches, and ratio checks.

    Based on industry patterns across professional fonts. Checks:

    Width matches (should be identical/similar):
    - Mirrored pairs: parenleft/parenright, bracketleft/bracketright, braceleft/braceright,
      guillemotleft/guillemotright (high severity — must match)
    - Related pairs: period/comma, colon/semicolon, quotedblleft/quotedblright (medium)

    Width ratios (expected relationships):
    - endash wider than hyphen (traditionally 2x)
    - emdash wider than endash (traditionally 2x)
    - quoteright similar width to comma
    - exclam narrower than question

    Skips any pairs where glyphs are missing. If master_id is omitted, analyzes ALL masters.

    Auto-marks glyphs: red=mirrored pair mismatch, yellow=width warning, green=pass.

    Args:
        master_id: Optional master ID (empty = all masters)
    """
    body = {}
    if master_id:
        body["masterId"] = master_id
    return _format_punctuation(_post("/api/font/punctuation/check", body))


def _format_compatibility_report(data: dict) -> str:
    """Format compatibility check results as a readable markdown report."""
    if not data.get("ok"):
        return data.get("error", "Unknown error")

    masters = data.get("masters", [])
    summary = data.get("summary", {})
    glyphs = data.get("glyphs", [])

    lines = []
    lines.append(f"## Compatibility Report ({data['masterCount']} masters: {', '.join(masters)})")
    lines.append("")

    # Summary
    total = summary.get("compatible", 0) + summary.get("incompatible", 0) + summary.get("emptyOrMissing", 0)
    lines.append(f"**{total} glyphs checked** — "
                 f"{summary.get('compatible', 0)} compatible, "
                 f"{summary.get('incompatible', 0)} incompatible, "
                 f"{summary.get('emptyOrMissing', 0)} empty/missing")
    lines.append("")

    # Incompatible glyphs — detailed table
    incompatible = [g for g in glyphs if g["color"] == 0]
    if incompatible:
        lines.append(f"### Incompatible ({len(incompatible)})")
        lines.append("")
        lines.append("| Glyph | Issues |")
        lines.append("|-------|--------|")
        for g in incompatible:
            issues_short = []
            for iss in g.get("issues", []):
                # Shorten common issue prefixes for readability
                if "Path order mismatch" in iss:
                    issues_short.append("Path order differs")
                elif "start node mismatch" in iss:
                    # Extract path index
                    pi = iss.split("Path ")[1].split(" ")[0]
                    issues_short.append(f"Path {pi} start node differs")
                elif "node count" in iss:
                    issues_short.append(iss)
                elif "node types differ" in iss:
                    pi = iss.split("Path ")[1].split(" ")[0]
                    issues_short.append(f"Path {pi} node types differ")
                elif "direction" in iss.lower() and "Path" in iss:
                    pi = iss.split("Path ")[1].split(" ")[0]
                    issues_short.append(f"Path {pi} direction differs")
                elif "Path count" in iss:
                    issues_short.append(iss)
                elif "Component" in iss:
                    issues_short.append(iss)
                elif "Anchor" in iss:
                    issues_short.append(iss)
                elif "Only drawn" in iss:
                    issues_short.append(iss)
                else:
                    issues_short.append(iss)
            lines.append(f"| {g['glyph']} | {'; '.join(issues_short)} |")
        lines.append("")
    else:
        lines.append("### Incompatible: none")
        lines.append("")

    # Empty/missing — compact list
    empty = [g["glyph"] for g in glyphs if g["color"] == 1]
    if empty:
        # Split into "partially drawn" vs "all empty"
        partial = [g for g in glyphs if g["color"] == 1 and any("Only drawn" in i for i in g.get("issues", []))]
        all_empty = [g["glyph"] for g in glyphs if g["color"] == 1 and any("All layers empty" in i for i in g.get("issues", []))]

        if partial:
            lines.append(f"### Partially drawn ({len(partial)})")
            lines.append("")
            lines.append("| Glyph | Issue |")
            lines.append("|-------|-------|")
            for g in partial:
                iss = next((i for i in g["issues"] if "Only drawn" in i), "")
                lines.append(f"| {g['glyph']} | {iss} |")
            lines.append("")

        if all_empty:
            lines.append(f"### All layers empty ({len(all_empty)})")
            lines.append("")
            lines.append(", ".join(all_empty))
            lines.append("")

    return "\n".join(lines)


@mcp.tool()
def check_compatibility(glyph_names: list[str] = None) -> str:
    """Check master compatibility across all glyphs in the font.

    Compares layers across masters for each glyph, checking:
    - Path count, node count, node types, path directions
    - Path order (spatial position must match across masters)
    - Starting node positions
    - Component count and names
    - Anchor names

    Auto-marks glyphs in GlyphsApp:
    - Red (0) = incompatible (structural mismatch between masters)
    - Orange (1) = empty or missing drawing in one or more masters
    - Green (4) = fully compatible

    Args:
        glyph_names: Optional list of glyphs to check (default: all glyphs)
    """
    body = {}
    if glyph_names:
        body["glyphNames"] = glyph_names
    data = _post("/api/font/compatibility/check", body)
    return _format_compatibility_report(data)


def _format_kerning_report(data: dict) -> str:
    """Format kerning analysis results as a readable markdown report."""
    if not data.get("ok"):
        return data.get("error", "Unknown error")

    masters = data.get("masters", [])
    per_master = data.get("perMaster", {})
    cross = data.get("crossMaster", {})
    orphans = data.get("groupOrphans", {})

    lines = []
    lines.append(f"## Kerning Analysis ({data['masterCount']} masters: {', '.join(masters)})")
    lines.append("")

    # Statistics table
    lines.append("### Statistics")
    lines.append("")
    lines.append("| Master | Pairs | Group | Exceptions | Range |")
    lines.append("|--------|-------|-------|------------|-------|")
    for mname in masters:
        md = per_master.get(mname, {})
        s = md.get("stats", {})
        rng = f"{s.get('minValue', 0)}…{s.get('maxValue', 0)}"
        lines.append(f"| {mname} | {s.get('totalPairs', 0)} | {s.get('groupPairs', 0)} | {s.get('exceptions', 0)} | {rng} |")
    lines.append("")

    # Cross-master: Missing pairs
    missing = cross.get("missingPairs", [])
    missing_count = cross.get("missingPairCount", len(missing))
    if missing:
        lines.append(f"### Cross-master: Missing pairs ({missing_count})")
        lines.append("Pairs present in some masters but not all — interpolates to/from 0.")
        lines.append("")
        lines.append("| Pair | Present in | Missing from |")
        lines.append("|------|-----------|--------------|")
        for m in missing:
            pair = f"{m['left']} / {m['right']}"
            lines.append(f"| {pair} | {', '.join(m['presentIn'])} | {', '.join(m['missingFrom'])} |")
        if missing_count > len(missing):
            lines.append(f"| ... | +{missing_count - len(missing)} more | |")
        lines.append("")

    # Cross-master: Sign changes
    signs = cross.get("signChanges", [])
    sign_count = cross.get("signChangeCount", len(signs))
    if signs:
        lines.append(f"### Cross-master: Sign changes ({sign_count})")
        lines.append("Pairs where kerning flips direction across masters.")
        lines.append("")
        header_cols = ["Pair"] + masters
        lines.append("| " + " | ".join(header_cols) + " |")
        lines.append("|" + "|".join(["------"] * len(header_cols)) + "|")
        for s in signs:
            pair = f"{s['left']} / {s['right']}"
            vals = [str(s['values'].get(m, '—')) for m in masters]
            lines.append(f"| {pair} | {' | '.join(vals)} |")
        lines.append("")

    # Outliers
    all_outliers = []
    for mname in masters:
        md = per_master.get(mname, {})
        for o in md.get("outliers", []):
            all_outliers.append({**o, "master": mname})
    if all_outliers:
        lines.append(f"### Outlier values ({len(all_outliers)})")
        lines.append(f"Pairs with extreme values (>{int(data.get('upm', 1000) * 0.4)}u = 40% of UPM).")
        lines.append("")
        lines.append("| Pair | Master | Value | % UPM |")
        lines.append("|------|--------|-------|-------|")
        for o in all_outliers[:50]:
            pair = f"{o['left']} / {o['right']}"
            lines.append(f"| {pair} | {o['master']} | {o['value']} | {o['percentUpm']}% |")
        lines.append("")

    # Redundant exceptions
    all_redundant = []
    for mname in masters:
        md = per_master.get(mname, {})
        for r in md.get("redundant", []):
            all_redundant.append({**r, "master": mname})
    if all_redundant:
        lines.append(f"### Redundant exceptions ({len(all_redundant)})")
        lines.append("Glyph-level pairs that match their group value — can be removed.")
        lines.append("")
        lines.append("| Left | Right | Master | Value |")
        lines.append("|------|-------|--------|-------|")
        for r in all_redundant[:30]:
            lines.append(f"| {r['left']} | {r['right']} | {r['master']} | {r['value']} |")
        if len(all_redundant) > 30:
            lines.append(f"| ... | ... | | +{len(all_redundant) - 30} more |")
        lines.append("")

    # Group orphans
    missing_both = orphans.get("missingBoth", [])
    missing_left = orphans.get("missingLeft", [])
    missing_right = orphans.get("missingRight", [])
    total_orphans = len(missing_both) + len(missing_left) + len(missing_right)
    if total_orphans > 0:
        lines.append(f"### Group orphans ({total_orphans})")
        lines.append("Letter glyphs missing kerning group assignments.")
        lines.append("")
        if missing_both:
            lines.append(f"**Missing both groups:** {', '.join(missing_both[:30])}")
            if len(missing_both) > 30:
                lines.append(f"  (+{len(missing_both) - 30} more)")
        if missing_left:
            lines.append(f"**Missing left group:** {', '.join(missing_left[:30])}")
        if missing_right:
            lines.append(f"**Missing right group:** {', '.join(missing_right[:30])}")
        lines.append("")

    # Critical pair coverage
    crit = data.get("criticalCoverage", {})
    if crit.get("totalChecked", 0) > 0:
        lines.append(f"### Critical pair coverage ({crit['coveragePct']}%)")
        lines.append(f"{crit['present']}/{crit['totalChecked']} essential pairs have kerning.")
        lines.append("")
        missing_crit = crit.get("missingPairs", {})
        for cat_label, cat_key in [("UC-UC", "uc_uc"), ("UC-lc", "uc_lc"), ("lc-lc", "lc_lc")]:
            pairs = missing_crit.get(cat_key, [])
            if pairs:
                lines.append(f"**Missing {cat_label}:** {', '.join(pairs[:30])}")
                if len(pairs) > 30:
                    lines.append(f"  (+{len(pairs) - 30} more)")
        lines.append("")

    # Exception ratio warnings
    exc_warns = data.get("exceptionWarnings", [])
    if exc_warns:
        lines.append("### Exception ratio warning")
        lines.append("High exception-to-total ratio may indicate sidebearing issues.")
        lines.append("")
        for w in exc_warns:
            lines.append(f"- **{w['master']}**: {w['exceptions']}/{w['totalPairs']} = {w['ratioPct']}% exceptions")
        lines.append("")

    # Summary line
    issue_count = missing_count + sign_count
    warning_count = len(all_outliers) + len(all_redundant)
    crit_missing = crit.get("missing", 0)
    if issue_count == 0 and warning_count == 0 and total_orphans == 0 and crit_missing == 0:
        lines.append("No issues found.")
    else:
        parts = []
        if issue_count > 0:
            parts.append(f"**{issue_count} cross-master issues**")
        if warning_count > 0:
            parts.append(f"**{warning_count} warnings**")
        if total_orphans > 0:
            parts.append(f"**{total_orphans} group orphans**")
        if crit_missing > 0:
            parts.append(f"**{crit_missing} missing critical pairs**")
        if exc_warns:
            parts.append(f"**{len(exc_warns)} exception ratio warnings**")
        lines.append(" | ".join(parts))
    lines.append("")

    return "\n".join(lines)


@mcp.tool()
def analyze_kerning(master_id: str = "") -> str:
    """Analyze kerning quality across all masters.

    Checks for:
    - Cross-master missing pairs (pair in some masters but not all — causes interpolation jumps)
    - Cross-master sign changes (positive in one master, negative in another)
    - Outlier values (extreme kerning > 40% of UPM)
    - Redundant exceptions (glyph-level overrides that match group value — can be removed)
    - Group orphans (Letter glyphs missing kerning group assignments)

    Returns a formatted markdown report. Marks affected glyphs in GlyphsApp:
    red = cross-master issues, yellow = quality warnings.

    Args:
        master_id: Optional master ID (cross-master checks always run across all masters)
    """
    body = {}
    if master_id:
        body["masterId"] = master_id
    data = _post("/api/font/kerning/analyze", body)
    return _format_kerning_report(data)


def _format_spacing_report(data: dict) -> str:
    """Format spacing analysis results as a readable markdown report."""
    if not data.get("ok"):
        return data.get("error", "Unknown error")

    masters = data.get("masters", [])
    per_master = data.get("perMaster", {})
    drift = data.get("crossMasterDrift", [])
    drift_count = data.get("crossMasterDriftCount", 0)

    lines = []
    lines.append(f"## Spacing Analysis ({data['masterCount']} masters: {', '.join(masters)})")
    lines.append("")

    # Reference ratios table
    has_ratios = any(per_master.get(m, {}).get("ratios") for m in masters)
    if has_ratios:
        lines.append("### Reference ratios")
        lines.append("")
        lines.append("| Master | Ratio | Value | Expected | Verdict |")
        lines.append("|--------|-------|-------|----------|---------|")
        for mname in masters:
            md = per_master.get(mname, {})
            for r in md.get("ratios", []):
                exp = f"{r['expectedRange'][0]}–{r['expectedRange'][1]}"
                lines.append(f"| {mname} | {r['label']} ({r['numValue']}/{r['denValue']}) | {r['ratio']} | {exp} | {r['verdict']} |")
        lines.append("")

    # Counter-based validation
    all_counter_checks = []
    for mname in masters:
        md = per_master.get(mname, {})
        for cc in md.get("counterChecks", []):
            all_counter_checks.append({**cc, "master": mname})
    if all_counter_checks:
        lines.append("### Counter-based validation")
        lines.append("LSB as percentage of counter width (expected 25–50%).")
        lines.append("")
        lines.append("| Glyph | Counter | LSB | Ratio | Expected | Verdict | Master |")
        lines.append("|-------|---------|-----|-------|----------|---------|--------|")
        for cc in all_counter_checks:
            lines.append(f"| {cc['glyph']} | {cc['counter']} | {cc['lsb']} | {cc['ratioPct']}% | {cc['expectedRange']} | {cc['verdict']} | {cc['master']} |")
        lines.append("")

    # Word space check
    all_word_space = []
    for mname in masters:
        md = per_master.get(mname, {})
        ws = md.get("wordSpace")
        if ws:
            all_word_space.append({**ws, "master": mname})
    if all_word_space:
        lines.append("### Word space")
        lines.append("")
        lines.append("| Master | Width | % of em | ¼ em | i width | Verdict |")
        lines.append("|--------|-------|---------|------|---------|---------|")
        for ws in all_word_space:
            i_w = str(ws['iWidth']) if ws.get('iWidth') else "—"
            lines.append(f"| {ws['master']} | {ws['width']} | {ws['ratioPctEm']}% | {ws['quarterEm']} | {i_w} | {ws['verdict']} |")
        lines.append("")

    # Sidebearing group inconsistencies
    all_group_issues = []
    for mname in masters:
        md = per_master.get(mname, {})
        for gi in md.get("groupIssues", []):
            all_group_issues.append({**gi, "master": mname})
    if all_group_issues:
        lines.append(f"### Sidebearing group inconsistencies ({len(all_group_issues)})")
        lines.append("")
        lines.append("| Glyph | Side | Value | Group avg (ref) | Deviation | Master |")
        lines.append("|-------|------|-------|-----------------|-----------|--------|")
        for gi in all_group_issues[:50]:
            dev_str = f"+{gi['deviation']}" if gi['deviation'] > 0 else str(gi['deviation'])
            lines.append(f"| {gi['glyph']} | {gi['side']} | {gi['value']} | {gi['groupAvg']} (={gi['ref']}) | {dev_str} | {gi['master']} |")
        if len(all_group_issues) > 50:
            lines.append(f"| ... | | | | +{len(all_group_issues) - 50} more | |")
        lines.append("")

    # Tracy/Smith per-glyph rule issues
    all_rule_issues = []
    for mname in masters:
        md = per_master.get(mname, {})
        for ri in md.get("ruleIssues", []):
            all_rule_issues.append({**ri, "master": mname})
    if all_rule_issues:
        lines.append(f"### Tracy/Smith sidebearing rules ({len(all_rule_issues)})")
        lines.append("Per-glyph expected relationships based on Cheng 'Designing Type'.")
        lines.append("")
        lines.append("| Glyph | Side | Value | Expected | Source | Severity | Master |")
        lines.append("|-------|------|-------|----------|--------|----------|--------|")
        for ri in all_rule_issues[:50]:
            exp = str(ri.get('expected', '—'))
            dev = ri.get('deviation')
            dev_str = f" ({'+' if dev > 0 else ''}{dev})" if dev is not None else ""
            lines.append(f"| {ri['glyph']} | {ri['side']} | {ri['value']}{dev_str} | {exp} | {ri['source']} | {ri['severity']} | {ri['master']} |")
        if len(all_rule_issues) > 50:
            lines.append(f"| ... | | | | | +{len(all_rule_issues) - 50} more | |")
        lines.append("")

    # Side-type ordering issues
    all_ordering = []
    for mname in masters:
        md = per_master.get(mname, {})
        for oi in md.get("orderingIssues", []):
            all_ordering.append({**oi, "master": mname})
    if all_ordering:
        lines.append(f"### Side-type ordering violations ({len(all_ordering)})")
        lines.append("Expected: straight SB > round SB > diagonal SB.")
        lines.append("")
        for oi in all_ordering:
            vals = []
            for k in ("straightAvg", "roundAvg", "diagonalAvg"):
                if k in oi:
                    vals.append(f"{k.replace('Avg', '')}={oi[k]}")
            lines.append(f"- **{oi['case']} {oi['side']}**: {oi['issue']} ({', '.join(vals)}) [{oi['master']}]")
        lines.append("")

    # Symmetry issues
    all_sym_issues = []
    for mname in masters:
        md = per_master.get(mname, {})
        for si in md.get("symmetryIssues", []):
            all_sym_issues.append({**si, "master": mname})
    if all_sym_issues:
        lines.append(f"### Asymmetric glyphs ({len(all_sym_issues)})")
        lines.append("Glyphs that should have LSB ≈ RSB.")
        lines.append("")
        lines.append("| Glyph | LSB | RSB | Difference | Master |")
        lines.append("|-------|-----|-----|------------|--------|")
        for si in all_sym_issues[:30]:
            lines.append(f"| {si['glyph']} | {si['lsb']} | {si['rsb']} | {si['difference']} | {si['master']} |")
        lines.append("")

    # Cross-master drift
    if drift:
        lines.append(f"### Cross-master drift ({drift_count})")
        lines.append("Spacing ratio relative to reference changed significantly between masters.")
        lines.append("")
        lines.append("| Glyph | Side | Master A (ratio) | Master B (ratio) |")
        lines.append("|-------|------|------------------|------------------|")
        for d in drift[:30]:
            lines.append(f"| {d['glyph']} | {d['side']} | {d['masterA']}: {d['valueA']} ({d['ratioA']}) | {d['masterB']}: {d['valueB']} ({d['ratioB']}) |")
        if drift_count > 30:
            lines.append(f"| ... | | | +{drift_count - 30} more |")
        lines.append("")

    # Summary
    total_issues = (len(all_group_issues) + len(all_rule_issues) + len(all_ordering)
                    + len(all_sym_issues) + drift_count)
    if total_issues == 0:
        lines.append("No spacing issues found.")
    else:
        parts = []
        if all_group_issues:
            parts.append(f"**{len(all_group_issues)} group inconsistencies**")
        if all_rule_issues:
            parts.append(f"**{len(all_rule_issues)} rule deviations**")
        if all_ordering:
            parts.append(f"**{len(all_ordering)} ordering violations**")
        if all_sym_issues:
            parts.append(f"**{len(all_sym_issues)} asymmetric glyphs**")
        if drift_count > 0:
            parts.append(f"**{drift_count} cross-master drift**")
        lines.append(" | ".join(parts))
    lines.append("")

    return "\n".join(lines)


@mcp.tool()
def analyze_spacing(master_id: str = "", glyph_names: list[str] = None) -> str:
    """Analyze spacing quality across all masters.

    Measures sidebearings and white space margins using scanline ray-casting,
    then checks for consistency issues:
    - Sidebearing group consistency (n-group: h,i,k,l,m,n,p,r should match; o-group: c,d,e,g,o,q)
    - Tracy/Smith per-glyph sidebearing rules (from "Designing Type" by Karen Cheng)
    - Side-type ordering (straight SB > round SB > diagonal SB)
    - Symmetric glyph check (o, O, H, I should have LSB ≈ RSB)
    - Reference ratios (n LSB / o LSB — optimal ~1.5x, acceptable 1.2–2.0)
    - Counter-based validation (n LSB should be 25–50% of n counter width)
    - Word space check (space width ≈ ¼ em ≈ width of i)
    - Cross-master spacing drift (spacing ratios should be maintained)

    Marks glyphs in GlyphsApp: red = significant inconsistency,
    yellow = minor deviation, green = pass.

    Args:
        master_id: Optional master ID (empty = all masters)
        glyph_names: Optional list of glyphs (empty = all Letter glyphs)
    """
    body = {}
    if master_id:
        body["masterId"] = master_id
    if glyph_names:
        body["glyphNames"] = glyph_names
    data = _post("/api/font/spacing/analyze", body)
    return _format_spacing_report(data)


@mcp.tool()
def get_spacing_strings(glyph_name: str) -> str:
    """Get spacing test strings for visually evaluating a glyph's spacing.

    Generates canonical test strings based on industry-standard methods:
    - Three-at-a-time (OH no Type Co): glyph sandwiched between n/o or H/O
    - Systematic pairs (Jamra): glyph paired with every letter in its case
    - Cross-case integration: glyph in mixed UC/LC context
    - Ruder test: hard vs easy word columns for overall color evaluation
    - Single-stem stress test: words like "millennial", "minimum" (for i, l, r, t)

    Use these strings in GlyphsApp's Edit view to visually assess spacing quality.

    Args:
        glyph_name: Name of the glyph to generate test strings for
    """
    data = _get(f"/api/font/glyphs/{glyph_name}/spacing-strings")
    if not data.get("ok"):
        return data.get("error", "Unknown error")

    lines = []
    lines.append(f"## Spacing test strings for '{glyph_name}' ({data.get('case', '?')})")
    lines.append("")

    tat = data.get("threeAtATime", {})
    if tat:
        lines.append("### Three-at-a-time")
        lines.append("Compare rhythm — middle glyph should have equal visual space on both sides.")
        lines.append("")
        lines.append(f"- Between straight: `{tat.get('betweenStraight', '')}`")
        lines.append(f"- Between round: `{tat.get('betweenRound', '')}`")
        lines.append(f"- Mixed: `{tat.get('mixed', '')}`")
        lines.append(f"- Reference: `{tat.get('reference', '')}`")
        lines.append("")

    ctx = data.get("contextStrings", [])
    if ctx:
        lines.append("### Context strings")
        for s in ctx:
            lines.append(f"- `{s}`")
        lines.append("")

    sp = data.get("systematicPairs", "")
    if sp:
        lines.append("### Systematic pairs")
        lines.append(f"`{sp}`")
        lines.append("")

    cc = data.get("crossCase", "")
    if cc:
        lines.append("### Cross-case")
        lines.append(f"`{cc}`")
        lines.append("")

    rw = data.get("ruderWords")
    if rw:
        lines.append(f"### Ruder words containing '{glyph_name}'")
        lines.append(f"{', '.join(rw)}")
        lines.append("")

    ss = data.get("singleStemStress")
    if ss:
        lines.append("### Single-stem stress test")
        lines.append(f"{', '.join(ss)}")
        lines.append("")

    ruder = data.get("ruderTest", {})
    if ruder:
        lines.append("### Ruder color test")
        lines.append(ruder.get("instruction", ""))
        lines.append("")
        lines.append(f"**Hard:** {', '.join(ruder.get('hard', []))}")
        lines.append(f"**Easy:** {', '.join(ruder.get('easy', []))}")
        lines.append("")

    return "\n".join(lines)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
