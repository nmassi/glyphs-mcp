#!/usr/bin/env python3
"""
glyphs_mcp_server.py — MCP Server for GlyphsApp integration.

Standalone process that MCP clients (Claude Code, Cursor, etc.) spawn via stdio.
Translates MCP tool calls into HTTP requests to the GlyphsMCP plugin
running inside GlyphsApp on http://127.0.0.1:7745.

Install: pip install glyphs-mcp
Usage:   uvx glyphs-mcp
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
def compare_stems(glyph_names: list[str], master_id: str = "") -> dict:
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
    return _post("/api/font/stems/compare", body)


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
def compare_color(glyph_names: list[str], master_id: str = "") -> dict:
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
    return _post("/api/font/color/compare", body)


@mcp.tool()
def audit_font_color(master_id: str = "") -> dict:
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
    return _post("/api/font/color/audit", body)


@mcp.tool()
def check_overshoots(glyph_names: list[str] = None, master_id: str = "") -> dict:
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
    return _post("/api/font/overshoots/check", body)


@mcp.tool()
def compare_proportions(glyph_names: list[str] = None, master_id: str = "") -> dict:
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
    return _post("/api/font/proportions/compare", body)


@mcp.tool()
def check_diagonal_weights(glyph_names: list[str] = None, master_id: str = "") -> dict:
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
    return _post("/api/font/diagonals/check", body)


@mcp.tool()
def check_junctions(glyph_names: list[str] = None, master_id: str = "") -> dict:
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
    return _post("/api/font/junctions/check", body)


@mcp.tool()
def check_related_forms(master_id: str = "") -> dict:
    """Cross-validate related figures and letters (0↔O, 6↔9, 8↔S, 3↔B, etc.).

    Based on Karen Cheng "Designing Type" + measurements across professional fonts.
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
    return _post("/api/font/related-forms/check", body)


@mcp.tool()
def check_punctuation(master_id: str = "") -> dict:
    """Check punctuation consistency: mirrored pairs, width matches, and ratio checks.

    Based on Karen Cheng "Designing Type" ch.7. Checks:

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
    return _post("/api/font/punctuation/check", body)


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    """Entry point for the glyphs-mcp CLI command."""
    mcp.run()


if __name__ == "__main__":
    main()
