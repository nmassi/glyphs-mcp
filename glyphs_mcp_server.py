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
def execute_in_glyphs(code: str) -> dict:
    """Execute arbitrary Python code inside GlyphsApp.

    The code runs with access to the Glyphs object and all GlyphsApp API.
    stdout is captured and returned. This is powerful but must be used carefully.

    NOTE: This endpoint is disabled by default. The user must enable it
    in GlyphsApp preferences (com.glyphsmcp.allowExecute = True).

    Example: execute_in_glyphs("print(Glyphs.font.familyName)")
    """
    return _post("/api/execute", {"code": code})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
