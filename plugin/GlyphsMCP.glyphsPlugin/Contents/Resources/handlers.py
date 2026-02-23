# encoding: utf-8
"""
handlers.py — REST API route handlers for GlyphsMCP.

Each handler function:
  - Receives: bridge, query (dict), body (dict or None), **path_params
  - Returns: (status_code, response_dict)
  - Is executed on the HTTP thread, BUT uses bridge.execute_on_main()
    for any Glyphs API access

IMPORTANT: Never access Glyphs, GSFont, GSGlyph, etc. directly in these functions.
Always go through bridge.execute_on_main(some_func_that_touches_glyphs).

See ARCHITECTURE.md §3.5 for the full API spec.
"""

# ── Route registry ────────────────────────────────────────────────────────────
# Maps (METHOD, path_pattern) → handler function
# Path params use {name} syntax: /api/font/glyphs/{name}

ROUTES = {}


def route(method, pattern):
	"""Decorator to register a route handler."""
	def decorator(func):
		ROUTES[(method, pattern)] = func
		return func
	return decorator


def handle_not_found(method, path):
	"""Default 404 response."""
	return {"error": f"Not found: {method} {path}", "available": list(
		f"{m} {p}" for m, p in ROUTES.keys()
	)}


# ── Helpers (run on main thread) ──────────────────────────────────────────────

def _require_font():
	"""Check that a font is open. Call ONLY from main thread."""
	from GlyphsApp import Glyphs
	if Glyphs.font is None:
		raise ValueError("No font open in GlyphsApp")
	return Glyphs.font


def _node_type_to_str(node_type):
	"""Convert GSNode type to string. Handles Glyphs 3 (string) and Glyphs 2 (int)."""
	s = str(node_type).lower()
	# Glyphs 3 returns strings directly: "line", "curve", "offcurve", "qcurve"
	if s in ("line", "curve", "offcurve", "qcurve"):
		return s
	# Glyphs 2 integer constants: LINE=1, CURVE=35, OFFCURVE=65, QCURVE=67
	mapping = {"1": "line", "35": "curve", "65": "offcurve", "67": "qcurve"}
	return mapping.get(s, f"unknown({node_type})")


def _str_to_node_type(type_str):
	"""Convert string to GSNode type constant."""
	mapping = {"line": 1, "curve": 35, "offcurve": 65, "qcurve": 67}
	return mapping.get(type_str.lower(), 1)


def _serialize_node(node):
	"""GSNode → dict. MUST run on main thread."""
	return {
		"x": float(node.position.x),
		"y": float(node.position.y),
		"type": _node_type_to_str(node.type),
		"smooth": bool(node.smooth)
	}


def _serialize_path(path):
	"""GSPath → dict. MUST run on main thread."""
	return {
		"closed": bool(path.closed),
		"direction": int(path.direction),
		"nodes": [_serialize_node(n) for n in path.nodes]
	}


def _serialize_component(comp):
	"""GSComponent → dict. MUST run on main thread."""
	return {
		"name": str(comp.componentName),
		"x": float(comp.position.x),
		"y": float(comp.position.y),
		"scale": [float(comp.scale.x), float(comp.scale.y)] if hasattr(comp, 'scale') else [1.0, 1.0]
	}


def _serialize_anchor(anchor):
	"""GSAnchor → dict. MUST run on main thread."""
	return {
		"name": str(anchor.name),
		"x": float(anchor.position.x),
		"y": float(anchor.position.y)
	}


def _serialize_layer(layer, master_name=""):
	"""GSLayer → dict. MUST run on main thread."""
	return {
		"id": str(layer.layerId),
		"master": master_name,
		"width": float(layer.width),
		"lsb": float(layer.LSB) if layer.LSB is not None else None,
		"rsb": float(layer.RSB) if layer.RSB is not None else None,
		"paths": [_serialize_path(p) for p in layer.paths],
		"components": [_serialize_component(c) for c in layer.components],
		"anchors": [_serialize_anchor(a) for a in layer.anchors]
	}


# ── GET /api/status ───────────────────────────────────────────────────────────

@route("GET", "/api/status")
def handle_status(bridge, **kwargs):
	"""Health check — doesn't need main thread access."""
	def _get_status():
		from GlyphsApp import Glyphs
		return {
			"ok": True,
			"app": "GlyphsApp",
			"version": str(Glyphs.versionString),
			"build": str(Glyphs.buildNumber),
			"fontOpen": Glyphs.font is not None,
			"fontName": str(Glyphs.font.familyName) if Glyphs.font else None
		}

	result = bridge.execute_on_main(_get_status)
	return 200, result


# ── GET /api/font ─────────────────────────────────────────────────────────────

@route("GET", "/api/font")
def handle_get_font(bridge, **kwargs):
	"""Get comprehensive font information."""
	def _get_font_info():
		font = _require_font()
		masters = []
		for m in font.masters:
			master_data = {
				"id": str(m.id),
				"name": str(m.name),
			}
			# Axis values
			if hasattr(m, 'axes'):
				master_data["axes"] = {str(a.name): float(m.axes[i]) for i, a in enumerate(font.axes)}
			masters.append(master_data)

		axes = []
		if hasattr(font, 'axes'):
			for a in font.axes:
				axes.append({
					"name": str(a.name),
					"tag": str(a.axisTag),
				})

		return {
			"familyName": str(font.familyName),
			"upm": int(font.upm),
			"glyphCount": len(font.glyphs),
			"masters": masters,
			"axes": axes,
			"instances": [{"name": str(i.name)} for i in font.instances],
			"ascender": int(font.masters[0].ascender) if font.masters else None,
			"descender": int(font.masters[0].descender) if font.masters else None,
			"xHeight": int(font.masters[0].xHeight) if font.masters else None,
			"capHeight": int(font.masters[0].capHeight) if font.masters else None,
		}

	result = bridge.execute_on_main(_get_font_info)
	return 200, result


# ── GET /api/font/glyphs ──────────────────────────────────────────────────────

@route("GET", "/api/font/glyphs")
def handle_list_glyphs(bridge, **kwargs):
	"""List all glyphs with basic metadata (no path data)."""
	def _list_glyphs():
		font = _require_font()
		glyphs = []
		for g in font.glyphs:
			glyphs.append({
				"name": str(g.name),
				"unicode": str(g.unicode) if g.unicode else None,
				"layers": len(g.layers),
				"script": str(g.script) if g.script else None,
				"category": str(g.category) if g.category else None,
				"subCategory": str(g.subCategory) if g.subCategory else None,
			})
		return {"glyphs": glyphs, "count": len(glyphs)}

	result = bridge.execute_on_main(_list_glyphs)
	return 200, result


# ── GET /api/font/glyphs/{name} ──────────────────────────────────────────────

@route("GET", "/api/font/glyphs/{name}")
def handle_get_glyph(bridge, name, **kwargs):
	"""Get full glyph data including all layers with paths."""
	def _get_glyph():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")

		# Build master ID → name lookup
		master_names = {str(m.id): str(m.name) for m in font.masters}

		layers = []
		for layer in glyph.layers:
			mid = str(layer.associatedMasterId) if hasattr(layer, 'associatedMasterId') else str(layer.layerId)
			mname = master_names.get(mid, str(layer.name))
			layers.append(_serialize_layer(layer, mname))

		return {
			"name": str(glyph.name),
			"unicode": str(glyph.unicode) if glyph.unicode else None,
			"script": str(glyph.script) if glyph.script else None,
			"category": str(glyph.category) if glyph.category else None,
			"subCategory": str(glyph.subCategory) if glyph.subCategory else None,
			"layers": layers
		}

	result = bridge.execute_on_main(_get_glyph)
	return 200, result


# ── GET /api/font/glyphs/{name}/svg ──────────────────────────────────────────

@route("GET", "/api/font/glyphs/{name}/svg")
def handle_get_glyph_svg(bridge, name, query=None, **kwargs):
	"""Get glyph rendered as SVG string."""
	master_id = (query or {}).get("master", [None])[0]

	def _get_svg():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")

		# Select layer
		if master_id:
			layer = glyph.layers[master_id]
		else:
			layer = glyph.layers[font.masters[0].id]

		# Build SVG from paths
		ascender = font.masters[0].ascender
		descender = font.masters[0].descender
		width = int(layer.width)
		height = ascender - descender

		paths_svg = []
		for path in layer.paths:
			d = _path_to_svg_d(path, ascender)
			if d:
				paths_svg.append(f'  <path d="{d}" fill="black"/>')

		svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <!-- Glyph: {name} | Width: {width} | Ascender: {ascender} | Descender: {descender} -->
{chr(10).join(paths_svg)}
</svg>'''
		return svg

	svg_str = bridge.execute_on_main(_get_svg)
	return 200, {"svg": svg_str}


def _path_to_svg_d(path, ascender):
	"""Convert a GSPath to an SVG path 'd' attribute. MUST run on main thread.

	Y-flip: SVG Y goes down, font Y goes up. transform: y' = ascender - y
	"""
	nodes = list(path.nodes)
	if not nodes:
		return ""

	parts = []
	i = 0
	n = len(nodes)
	first = True

	while i < n:
		node = nodes[i]
		x = float(node.position.x)
		y = float(ascender - node.position.y)  # Y-flip

		nt = _node_type_to_str(node.type)

		if nt == "line":
			if first:
				parts.append(f"M{x:.0f} {y:.0f}")
				first = False
			else:
				parts.append(f"L{x:.0f} {y:.0f}")
			i += 1

		elif nt == "curve":  # CURVE (preceded by 2 offcurves in the nodes list)
			if first:
				parts.append(f"M{x:.0f} {y:.0f}")
				first = False
				i += 1
			else:
				# This on-curve is the endpoint; the 2 preceding offcurves are handles
				# In GlyphsApp node order: ..., offcurve, offcurve, curve, ...
				# But we iterate forward, so we need to look at the previous 2 nodes
				# Actually in GSPath.nodes, the order for a curve segment is:
				# offcurve (handle1), offcurve (handle2), curve (on-curve endpoint)
				# We should have already seen the offcurves
				parts.append(f"L{x:.0f} {y:.0f}")  # TODO: proper curve handling
				i += 1

		elif nt == "offcurve":  # OFFCURVE
			# Collect this and next offcurve, then the curve point
			if i + 2 < n:
				h1x = x
				h1y = y
				h2 = nodes[i + 1]
				h2x = float(h2.position.x)
				h2y = float(ascender - h2.position.y)
				ep = nodes[i + 2]
				epx = float(ep.position.x)
				epy = float(ascender - ep.position.y)

				if first:
					parts.append(f"M{epx:.0f} {epy:.0f}")
					first = False
				else:
					parts.append(f"C{h1x:.0f} {h1y:.0f} {h2x:.0f} {h2y:.0f} {epx:.0f} {epy:.0f}")
				i += 3
			else:
				i += 1
		else:
			i += 1

	if path.closed:
		parts.append("Z")

	return " ".join(parts)


# ── GET /api/font/masters ────────────────────────────────────────────────────

@route("GET", "/api/font/masters")
def handle_get_masters(bridge, **kwargs):
	"""Get all font masters with metrics."""
	def _get_masters():
		font = _require_font()
		masters = []
		for m in font.masters:
			master = {
				"id": str(m.id),
				"name": str(m.name),
				"ascender": int(m.ascender),
				"descender": int(m.descender),
				"xHeight": int(m.xHeight),
				"capHeight": int(m.capHeight),
			}
			if hasattr(font, 'axes') and hasattr(m, 'axes'):
				master["axes"] = {str(a.name): float(m.axes[i]) for i, a in enumerate(font.axes)}
			masters.append(master)
		return {"masters": masters}

	result = bridge.execute_on_main(_get_masters)
	return 200, result


# ── GET /api/font/selection ──────────────────────────────────────────────────

@route("GET", "/api/font/selection")
def handle_get_selection(bridge, **kwargs):
	"""Get the current selection in GlyphsApp editor."""
	def _get_selection():
		font = _require_font()
		tab = font.currentTab
		if tab is None:
			return {"selection": None, "message": "No tab open"}

		layer = tab.activeLayer() if hasattr(tab, 'activeLayer') else None
		if layer is None:
			return {"selection": None, "message": "No active layer"}

		glyph = layer.parent
		selected_paths = []
		selected_nodes = []

		if hasattr(layer, 'selection'):
			for item in layer.selection:
				if hasattr(item, 'nodes'):  # It's a path
					selected_paths.append(_serialize_path(item))
				elif hasattr(item, 'position'):  # It's a node
					selected_nodes.append(_serialize_node(item))

		return {
			"glyphName": str(glyph.name) if glyph else None,
			"layerId": str(layer.layerId),
			"masterName": str(layer.name),
			"selectedPaths": selected_paths,
			"selectedNodes": selected_nodes
		}

	result = bridge.execute_on_main(_get_selection)
	return 200, result


# ── POST /api/font/glyphs/{name}/paths ───────────────────────────────────────

@route("POST", "/api/font/glyphs/{name}/paths")
def handle_set_glyph_paths(bridge, name, body=None, **kwargs):
	"""Replace all paths on a glyph's layer."""
	if not body or "paths" not in body:
		return 400, {"error": "Body must contain 'paths' array"}

	paths_data = body["paths"]
	master_id = body.get("masterId", None)

	def _set_paths():
		from GlyphsApp import Glyphs, GSPath, GSNode
		from Foundation import NSPoint

		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")

		# Select layer
		if master_id:
			layer = glyph.layers[master_id]
		else:
			layer = glyph.layers[font.masters[0].id]

		# Wrap in update block
		font.disableUpdateInterface()
		try:
			# Clear existing paths (layer.paths has no setter in Glyphs 3)
			for p in list(layer.paths):
				layer.removeShape_(p)

			for pdata in paths_data:
				path = GSPath()
				for ndata in pdata.get("nodes", []):
					node = GSNode()
					node.position = NSPoint(float(ndata["x"]), float(ndata["y"]))
					node.type = _str_to_node_type(ndata.get("type", "line"))
					node.smooth = bool(ndata.get("smooth", False))
					path.nodes.append(node)
				path.closed = pdata.get("closed", True)
				layer.paths.append(path)

			# Fix winding direction
			layer.correctPathDirection()

		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"glyphName": name,
			"pathCount": len(layer.paths),
		}

	result = bridge.execute_on_main(_set_paths)
	return 200, result


# ── POST /api/font/glyphs ────────────────────────────────────────────────────

@route("POST", "/api/font/glyphs")
def handle_create_glyph(bridge, body=None, **kwargs):
	"""Create a new glyph with optional paths."""
	if not body or "name" not in body:
		return 400, {"error": "Body must contain 'name'"}

	glyph_name = body["name"]
	unicode_val = body.get("unicode", None)
	width = body.get("width", 600)
	paths_data = body.get("paths", [])

	def _create_glyph():
		from GlyphsApp import Glyphs, GSGlyph, GSPath, GSNode
		from Foundation import NSPoint

		font = _require_font()

		# Check if glyph already exists
		if font.glyphs[glyph_name]:
			raise ValueError(f"Glyph '{glyph_name}' already exists")

		glyph = GSGlyph(glyph_name)
		if unicode_val:
			glyph.unicode = unicode_val

		font.glyphs.append(glyph)

		# Set width and paths for first master
		layer = glyph.layers[font.masters[0].id]
		layer.width = float(width)

		if paths_data:
			font.disableUpdateInterface()
			try:
				for pdata in paths_data:
					path = GSPath()
					for ndata in pdata.get("nodes", []):
						node = GSNode()
						node.position = NSPoint(float(ndata["x"]), float(ndata["y"]))
						node.type = _str_to_node_type(ndata.get("type", "line"))
						node.smooth = bool(ndata.get("smooth", False))
						path.nodes.append(node)
					path.closed = pdata.get("closed", True)
					layer.paths.append(path)
				layer.correctPathDirection()
			finally:
				font.enableUpdateInterface()

		return {
			"ok": True,
			"glyphName": glyph_name,
			"pathCount": len(layer.paths),
		}

	result = bridge.execute_on_main(_create_glyph)
	return 201, result


# ── POST /api/font/glyphs/{name}/width ───────────────────────────────────────

@route("POST", "/api/font/glyphs/{name}/width")
def handle_set_width(bridge, name, body=None, **kwargs):
	"""Set the advance width of a glyph."""
	if not body or "width" not in body:
		return 400, {"error": "Body must contain 'width'"}

	width = body["width"]
	master_id = body.get("masterId", None)

	def _set_width():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")

		if master_id:
			layer = glyph.layers[master_id]
		else:
			layer = glyph.layers[font.masters[0].id]

		layer.width = float(width)
		return {"ok": True, "glyphName": name, "width": float(layer.width)}

	result = bridge.execute_on_main(_set_width)
	return 200, result


# ── POST /api/font/kerning ──────────────────────────────────────────────────

@route("POST", "/api/font/kerning")
def handle_set_kerning(bridge, body=None, **kwargs):
	"""Set a kerning pair."""
	if not body or not all(k in body for k in ("left", "right", "value")):
		return 400, {"error": "Body must contain 'left', 'right', 'value'"}

	left = body["left"]
	right = body["right"]
	value = body["value"]
	master_id = body.get("masterId", None)

	def _set_kerning():
		font = _require_font()
		mid = master_id or str(font.masters[0].id)
		font.setKerningForPair(mid, left, right, float(value))
		return {"ok": True, "left": left, "right": right, "value": float(value)}

	result = bridge.execute_on_main(_set_kerning)
	return 200, result


# ── GET /api/font/kerning ────────────────────────────────────────────────────

@route("GET", "/api/font/kerning")
def handle_get_kerning(bridge, query=None, **kwargs):
	"""Get kerning pairs for a master."""
	master_id = (query or {}).get("master", [None])[0]

	def _get_kerning():
		font = _require_font()
		mid = master_id or str(font.masters[0].id)
		kerning = font.kerning.get(mid, {})

		pairs = []
		for left_key, rights in kerning.items():
			for right_key, value in rights.items():
				pairs.append({
					"left": str(left_key),
					"right": str(right_key),
					"value": float(value)
				})

		return {"masterId": mid, "pairs": pairs, "count": len(pairs)}

	result = bridge.execute_on_main(_get_kerning)
	return 200, result


# ── GET /api/font/features ───────────────────────────────────────────────────

@route("GET", "/api/font/features")
def handle_get_features(bridge, **kwargs):
	"""Get OpenType feature code."""
	def _get_features():
		font = _require_font()
		features = []
		for f in font.features:
			features.append({
				"name": str(f.name),
				"code": str(f.code),
				"active": bool(f.active) if hasattr(f, 'active') else True
			})
		return {"features": features}

	result = bridge.execute_on_main(_get_features)
	return 200, result


# ── POST /api/font/glyphs/{name}/color ───────────────────────────────────────

@route("POST", "/api/font/glyphs/{name}/color")
def handle_set_glyph_color(bridge, name, body=None, **kwargs):
	"""Set the color label of a glyph (0–12). Use None to clear.

	Color index: 0=red, 1=orange, 2=brown, 3=yellow, 4=light green,
	5=dark green, 6=teal, 7=blue, 8=purple, 9=pink, 10=light gray, 11=charcoal.
	"""
	if not body or "color" not in body:
		return 400, {"error": "Body must contain 'color' (int 0-12)"}

	color = int(body["color"])

	def _set_color():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")
		glyph.color = color
		return {"ok": True, "glyphName": name, "color": color}

	result = bridge.execute_on_main(_set_color)
	return 200, result


# ── DELETE /api/font/glyphs/{name} ───────────────────────────────────────────

@route("DELETE", "/api/font/glyphs/{name}")
def handle_delete_glyph(bridge, name, **kwargs):
	"""Delete a glyph from the font."""
	def _delete():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")
		del font.glyphs[name]
		return {"ok": True, "deleted": name}

	result = bridge.execute_on_main(_delete)
	return 200, result


# ── POST /api/font/glyphs/{name}/rename ──────────────────────────────────────

@route("POST", "/api/font/glyphs/{name}/rename")
def handle_rename_glyph(bridge, name, body=None, **kwargs):
	"""Rename a glyph."""
	if not body or "newName" not in body:
		return 400, {"error": "Body must contain 'newName'"}

	new_name = body["newName"]

	def _rename():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")
		if font.glyphs[new_name]:
			raise ValueError(f"Glyph '{new_name}' already exists")
		glyph.name = new_name
		return {"ok": True, "oldName": name, "newName": new_name}

	result = bridge.execute_on_main(_rename)
	return 200, result


# ── POST /api/font/glyphs/{name}/duplicate ───────────────────────────────────

@route("POST", "/api/font/glyphs/{name}/duplicate")
def handle_duplicate_glyph(bridge, name, body=None, **kwargs):
	"""Duplicate a glyph under a new name."""
	if not body or "newName" not in body:
		return 400, {"error": "Body must contain 'newName'"}

	new_name = body["newName"]

	def _duplicate():
		from GlyphsApp import GSGlyph, GSPath, GSNode
		from Foundation import NSPoint

		font = _require_font()
		src = font.glyphs[name]
		if src is None:
			raise KeyError(f"Glyph '{name}' not found")
		if font.glyphs[new_name]:
			raise ValueError(f"Glyph '{new_name}' already exists")

		dst = GSGlyph(new_name)
		dst.color = src.color
		font.glyphs.append(dst)

		font.disableUpdateInterface()
		try:
			for src_layer in src.layers:
				dst_layer = dst.layers[src_layer.layerId]
				if dst_layer is None:
					continue
				dst_layer.width = src_layer.width
				for src_path in src_layer.paths:
					path = GSPath()
					for src_node in src_path.nodes:
						node = GSNode()
						node.position = NSPoint(src_node.position.x, src_node.position.y)
						node.type = src_node.type
						node.smooth = src_node.smooth
						path.nodes.append(node)
					path.closed = src_path.closed
					dst_layer.paths.append(path)
		finally:
			font.enableUpdateInterface()

		return {"ok": True, "source": name, "newName": new_name}

	result = bridge.execute_on_main(_duplicate)
	return 200, result


# ── POST /api/font/glyphs/{name}/unicode ─────────────────────────────────────

@route("POST", "/api/font/glyphs/{name}/unicode")
def handle_set_glyph_unicode(bridge, name, body=None, **kwargs):
	"""Set the unicode value of a glyph (e.g. '0061'). Pass null to clear."""
	if not body or "unicode" not in body:
		return 400, {"error": "Body must contain 'unicode' (hex string or null)"}

	unicode_val = body["unicode"]  # e.g. "0061" or None

	def _set_unicode():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")
		glyph.unicode = unicode_val if unicode_val else None
		return {"ok": True, "glyphName": name, "unicode": unicode_val}

	result = bridge.execute_on_main(_set_unicode)
	return 200, result


# ── DELETE /api/font/kerning ─────────────────────────────────────────────────

@route("DELETE", "/api/font/kerning")
def handle_delete_kerning(bridge, body=None, **kwargs):
	"""Delete a kerning pair."""
	if not body or not all(k in body for k in ("left", "right")):
		return 400, {"error": "Body must contain 'left' and 'right'"}

	left = body["left"]
	right = body["right"]
	master_id = body.get("masterId", None)

	def _delete_kerning():
		font = _require_font()
		mid = master_id or str(font.masters[0].id)
		font.removeKerningForPair(mid, left, right)
		return {"ok": True, "left": left, "right": right}

	result = bridge.execute_on_main(_delete_kerning)
	return 200, result


# ── POST /api/font/features/{name} ───────────────────────────────────────────

@route("POST", "/api/font/features/{name}")
def handle_set_feature(bridge, name, body=None, **kwargs):
	"""Create or update an OpenType feature. Pass 'active': false to disable."""
	if not body or "code" not in body:
		return 400, {"error": "Body must contain 'code'"}

	code = body["code"]
	active = body.get("active", True)

	def _set_feature():
		from GlyphsApp import GSFeature
		font = _require_font()
		for f in font.features:
			if f.name == name:
				f.code = code
				if hasattr(f, 'active'):
					f.active = active
				return {"ok": True, "name": name, "action": "updated"}
		# Not found — create new
		feature = GSFeature()
		feature.name = name
		feature.code = code
		font.features.append(feature)
		return {"ok": True, "name": name, "action": "created"}

	result = bridge.execute_on_main(_set_feature)
	return 200, result


# ── Glyph Classification Helper ──────────────────────────────────────────────

def _classify_glyph(glyph):
	"""Determine if a glyph is uppercase, lowercase, figure, or other.

	Priority: subCategory > glyph.case > name heuristic.
	glyph.case: 0=N/A, 1=Upper, 2=Lower, 3=SC, 4=Minor.
	Returns: "uppercase", "lowercase", "figure", or None
	"""
	category = str(glyph.category) if glyph.category else ""
	sub = str(glyph.subCategory) if glyph.subCategory else ""

	if category == "Number" or sub == "Decimal Digit":
		return "figure"

	if category == "Letter":
		if sub == "Uppercase":
			return "uppercase"
		if sub == "Lowercase":
			return "lowercase"
		# Fallback: glyph.case property (Glyphs 3)
		case = getattr(glyph, "case", 0)
		if case == 1:
			return "uppercase"
		if case == 2:
			return "lowercase"

	return None


# ── Stem Measurement Helpers ─────────────────────────────────────────────────

def _measure_stems_horizontal(layer, y_positions, x_min=None, x_max=None):
	"""Cast horizontal rays at given Y positions, return stem measurements.

	Each ray goes from left edge to right edge of the glyph.
	Returns list of measurements, each with Y position and stem widths found.

	MUST run on main thread (accesses GlyphsApp API).
	"""
	from Foundation import NSPoint

	if x_min is None:
		x_min = -50
	if x_max is None:
		x_max = float(layer.width) + 50

	measurements = []

	for y in y_positions:
		p1 = NSPoint(x_min, y)
		p2 = NSPoint(x_max, y)

		raw = layer.intersectionsBetweenPoints(p1, p2)
		if raw is None:
			continue

		xs = sorted([float(p.x) for p in raw])
		xs = [x for x in xs if x_min < x < x_max]

		stems = []
		for i in range(0, len(xs) - 1, 2):
			thickness = int(round(xs[i + 1] - xs[i]))
			if thickness > 0.5:
				stems.append({
					"thickness": thickness,
					"xStart": round(xs[i], 1),
					"xEnd": round(xs[i + 1], 1),
				})

		if stems:
			measurements.append({
				"y": round(y, 1),
				"stems": stems,
				"count": len(stems),
			})

	return measurements


def _measure_stems_vertical(layer, x_positions, y_min=None, y_max=None):
	"""Cast vertical rays at given X positions, return crossbar/horizontal stem measurements.

	MUST run on main thread.
	"""
	from Foundation import NSPoint

	font = layer.parent.parent
	if y_min is None:
		y_min = int(font.masters[0].descender) - 50
	if y_max is None:
		y_max = int(font.masters[0].ascender) + 50

	measurements = []

	for x in x_positions:
		p1 = NSPoint(x, y_min)
		p2 = NSPoint(x, y_max)

		raw = layer.intersectionsBetweenPoints(p1, p2)
		if raw is None:
			continue

		ys = sorted([float(p.y) for p in raw])
		ys = [y for y in ys if y_min < y < y_max]

		stems = []
		for i in range(0, len(ys) - 1, 2):
			thickness = int(round(ys[i + 1] - ys[i]))
			if thickness > 0.5:
				stems.append({
					"thickness": thickness,
					"yStart": round(ys[i], 1),
					"yEnd": round(ys[i + 1], 1),
				})

		if stems:
			measurements.append({
				"x": round(x, 1),
				"stems": stems,
				"count": len(stems),
			})

	return measurements


def _find_dominant_stem(values, tolerance=3, strategy="frequency", reference=None):
	"""Find the dominant stem thickness from perpendicular measurements.

	Strategies:
	- "frequency": pick the most frequent group (default). Avoids junction
	  inflation from arch-to-stem transitions in n, h, m. Best for comparing
	  stems across glyphs.
	- "thickest": pick the thickest group with 2+ members. Better for
	  isolated glyph measurement where junctions are not an issue.
	- "nearest_ref": pick the group closest to 'reference' value. For mixed
	  glyphs (stem + bowl) where we want to isolate the straight stem.

	Groups values within tolerance of the group start (no chaining).
	Returns median of the chosen group, rounded to integer.
	"""
	if not values:
		return None

	sorted_vals = sorted(values)

	groups = []
	current_group = [sorted_vals[0]]

	for v in sorted_vals[1:]:
		if v - current_group[0] <= tolerance:
			current_group.append(v)
		else:
			groups.append(current_group)
			current_group = [v]
	groups.append(current_group)

	if strategy == "thickest":
		# Prefer groups with 2+ measurements (more reliable)
		multi = [g for g in groups if len(g) >= 2]
		if multi:
			best = max(multi, key=lambda g: g[0])
		else:
			best = max(groups, key=lambda g: g[0])
	elif strategy == "nearest_ref" and reference is not None:
		# Pick group whose median is closest to the reference value
		best = min(groups, key=lambda g: abs(g[len(g)//2] - reference))
	else:
		# "frequency" — most frequent group (avoids junction artifacts)
		best = max(groups, key=lambda g: len(g))

	# Return median of the chosen group
	mid = len(best) // 2
	return int(round(best[mid]))


def _measure_perpendicular(layer, max_thickness=300, y_min=None, y_max=None):
	"""Measure stem thickness at every on-curve node using perpendicular rays.

	Works on a temporary copy with overlaps removed, so open corners and
	overlapping drawings (common in type design) don't produce false readings.

	For each on-curve node:
	1. Get the tangent angle via path.tangentAngleAtNodeAtIndex_direction_
	2. Cast a ray perpendicular to the tangent
	3. Find the nearest intersection = stem thickness

	Only measures nodes within y_min..y_max zone (defaults to descender..ascender).
	This filters out accessory parts like dots on i/j, cedillas, etc.

	Classifies each measurement as vertical (tangent 60-120° or 240-300°)
	or horizontal (tangent 0-30°, 150-210°, 330-360°).

	MUST run on main thread.
	"""
	import math
	from Foundation import NSPoint

	# Determine measurement zone from font metrics
	font = layer.parent.parent if layer.parent else None
	if font:
		master = None
		for m in font.masters:
			if m.id == layer.layerId or m.id == layer.associatedMasterId:
				master = m
				break
		if master is None:
			master = font.masters[0]
		if y_min is None:
			y_min = int(master.descender)
		if y_max is None:
			y_max = int(master.capHeight)
	else:
		if y_min is None:
			y_min = -250
		if y_max is None:
			y_max = 750

	# Work on a clean copy: decompose components + remove overlaps
	# copyDecomposedLayer() preserves parent font reference so flipped/
	# transformed components (d=flipped b, q=flipped p) resolve correctly
	clean = layer.copyDecomposedLayer()
	clean.removeOverlap()

	v_stems = []
	h_stems = []
	all_measurements = []

	for path in clean.paths:
		if len(path.nodes) == 0:
			continue

		for i, node in enumerate(path.nodes):
			if str(node.type) == "offcurve":
				continue

			# Filter by Y zone — skip dots, cedillas, accents, etc.
			ny = float(node.position.y)
			if ny < y_min or ny > y_max:
				continue

			try:
				angle = path.tangentAngleAtNodeAtIndex_direction_(i, 1)
			except Exception:
				continue

			# Perpendicular direction
			perp_rad = math.radians(angle + 90)
			nx, ny = math.cos(perp_rad), math.sin(perp_rad)
			x, y = float(node.position.x), float(node.position.y)

			p1 = NSPoint(x - nx * 1000, y - ny * 1000)
			p2 = NSPoint(x + nx * 1000, y + ny * 1000)

			raw = clean.intersectionsBetweenPoints(p1, p2)
			if raw is None or len(raw) < 2:
				continue

			# Find stem thickness = nearest intersection distance,
			# excluding the node itself (dist ~0) and ray endpoints (~1000).
			# At junctions (e.g. where shoulder meets stem), there can be
			# a very close intersection (<10u) from the adjacent segment —
			# skip those and take the next one, which is the actual stem wall.
			dists = []
			for pt in raw:
				dx = float(pt.x) - x
				dy = float(pt.y) - y
				d = math.sqrt(dx * dx + dy * dy)
				if d > 0.5 and d < 900:  # exclude node itself and ray endpoints
					dists.append(d)

			if not dists:
				continue

			dists.sort()
			# Skip junction artifacts: if closest < 10u and there's another
			# measurement, use the next one (the real stem wall)
			if len(dists) >= 2 and dists[0] < 10:
				thickness = int(round(dists[1]))
			else:
				thickness = int(round(dists[0]))

			if thickness > max_thickness:
				continue

			# Classify by tangent direction (normalized to 0-360)
			norm = angle % 360
			is_vertical = (60 <= norm <= 120) or (240 <= norm <= 300)
			is_horizontal = (norm <= 30) or (150 <= norm <= 210) or (norm >= 330)

			measurement = {
				"node": i,
				"x": int(round(x)),
				"y": int(round(y)),
				"tangent": int(round(angle)),
				"thickness": thickness,
			}
			all_measurements.append(measurement)

			if is_vertical:
				v_stems.append(thickness)
			elif is_horizontal:
				h_stems.append(thickness)

	return v_stems, h_stems, all_measurements


def _auto_measure_glyph(layer, num_samples=10, strategy="frequency", strategy_kwargs=None):
	"""Measure a glyph's stem thicknesses using perpendicular ray-casting.

	Casts rays perpendicular to the contour at each on-curve node.
	This gives accurate measurements for all shapes: straight, round,
	and diagonal — unlike fixed horizontal/vertical rays.

	Automatically constrains measurement zone based on glyph case:
	- Lowercase: baseline to xHeight (excludes dots on i/j, accents)
	- Uppercase/other: descender to ascender

	strategy_kwargs: extra args passed to _find_dominant_stem (e.g. reference for nearest_ref).

	MUST run on main thread.
	"""
	bounds = layer.bounds
	if bounds is None or bounds.size.width == 0:
		return {
			"verticalStems": {"dominant": None, "min": None, "max": None},
			"horizontalStems": {"dominant": None, "min": None, "max": None},
		}

	# Determine Y zone based on glyph case
	y_min = None
	y_max = None
	glyph = layer.parent
	if glyph:
		font = glyph.parent
		if font:
			cls = _classify_glyph(glyph)
			master = None
			for m in font.masters:
				if m.id == layer.layerId or m.id == layer.associatedMasterId:
					master = m
					break
			if master is None:
				master = font.masters[0]
			if cls == "lowercase":
				y_min = int(master.descender)
				y_max = int(master.xHeight)
			# uppercase/other: use full range (defaults in _measure_perpendicular)

	v_stems, h_stems, measurements = _measure_perpendicular(layer, y_min=y_min, y_max=y_max)

	kw = {"strategy": strategy}
	if strategy_kwargs:
		kw.update(strategy_kwargs)

	v_stem_value = _find_dominant_stem(v_stems, **kw) if v_stems else None
	h_stem_value = _find_dominant_stem(h_stems, **kw) if h_stems else None

	return {
		"verticalStems": {
			"dominant": v_stem_value,
			"min": int(round(min(v_stems))) if v_stems else None,
			"max": int(round(max(v_stems))) if v_stems else None,
		},
		"horizontalStems": {
			"dominant": h_stem_value,
			"min": int(round(min(h_stems))) if h_stems else None,
			"max": int(round(max(h_stems))) if h_stems else None,
		},
	}


# ── Industry stem patterns ──────────────────────────────────────────────────
# Expected stem deviations from straight reference (n for LC, H for UC),
# derived from analysis of professional text fonts at Regular/Book weight.
#   maxDev: max acceptable ± deviation (units) — flag if exceeded
#   range: [lo, hi] signed deviation — known optical compensation
#   unreliable: measurement algorithm can't reliably measure this shape

STEM_PATTERNS = {
	# Lowercase — deviation from n
	"h": {"maxDev": 1}, "i": {"maxDev": 1}, "j": {"maxDev": 1},
	"k": {"maxDev": 1}, "m": {"maxDev": 1}, "n": {"maxDev": 1},
	"q": {"maxDev": 1}, "u": {"maxDev": 1},
	"b": {"maxDev": 2}, "g": {"maxDev": 2}, "t": {"maxDev": 2},
	"l": {"maxDev": 3},
	"a": {"range": [-4, 0], "note": "slightly thinner stem"},
	"o": {"range": [0, 7], "note": "round compensation"},
	"c": {"range": [0, 7], "note": "round compensation"},
	"e": {"unreliable": True, "note": "construction-dependent (range=14)"},
	"f": {"unreliable": True, "note": "varies by design (range=14)"},
	"s": {"unreliable": True, "note": "spine inconsistent (range=8)"},
	"v": {"unreliable": True, "note": "diagonal apex artifact"},
	"w": {"unreliable": True, "note": "diagonal apex artifact"},
	"x": {"unreliable": True, "note": "no vertical stems"},
	"y": {"unreliable": True, "note": "diagonal unreliable"},
	"z": {"unreliable": True, "note": "no vertical stems"},
	"d": {"unreliable": True, "note": "mixed stem/bowl (range=6)"},
	"p": {"unreliable": True, "note": "mixed stem/bowl (range=6)"},
	"r": {"unreliable": True, "note": "mixed stem/bowl (range=6)"},
	# Uppercase — deviation from H
	"E": {"maxDev": 1}, "F": {"maxDev": 1}, "H": {"maxDev": 1},
	"J": {"maxDev": 1}, "K": {"maxDev": 1}, "L": {"maxDev": 1},
	"U": {"maxDev": 1},
	"P": {"maxDev": 2}, "T": {"maxDev": 2},
	"A": {"range": [-5, -3], "note": "diagonal thinner"},
	"B": {"range": [0, 4], "note": "double bowl compensation"},
	"C": {"range": [0, 4], "note": "round compensation"},
	"D": {"range": [0, 4], "note": "large bowl compensation"},
	"O": {"range": [0, 4], "note": "round compensation"},
	"Q": {"range": [0, 4], "note": "round compensation"},
	"R": {"range": [0, 3], "note": "bowl + leg compensation"},
	"I": {"range": [0, 3], "note": "mass compensation"},
	"S": {"range": [-1, 4], "note": "spine varies"},
	"G": {"range": [0, 5], "note": "mixed round/straight"},
	"M": {"unreliable": True, "note": "diagonal strokes (range=9)"},
	"N": {"unreliable": True, "note": "diagonal outliers (range=14)"},
	"V": {"unreliable": True, "note": "diagonal apex"},
	"W": {"unreliable": True, "note": "diagonal apex"},
	"X": {"unreliable": True, "note": "insufficient data"},
	"Y": {"unreliable": True, "note": "diagonal (range=7)"},
	"Z": {"unreliable": True, "note": "insufficient data"},
	# Figures — deviation from H (figures match UC stem weight)
	"zero":  {"range": [0, 4], "note": "round compensation (like O)"},
	"one":   {"maxDev": 3},
	"two":   {"unreliable": True, "note": "hook varies (range=31)"},
	"three": {"unreliable": True, "note": "double bowl varies (range=20)"},
	"four":  {"range": [-10, 2], "note": "diagonal thinner (like A)"},
	"five":  {"unreliable": True, "note": "bowl/flag varies (range=11)"},
	"six":   {"range": [0, 5], "note": "round compensation"},
	"seven": {"range": [0, 10], "note": "thick horizontal dominates"},
	"eight": {"range": [0, 5], "note": "spine compensation (like S)"},
	"nine":  {"unreliable": True, "note": "bowl varies (range=22)"},
}


# Glyphs that become unreliable at heavy weights (ref stem > 120u)
# due to junction compression, diagonal interference, or construction changes.
_HEAVY_UNRELIABLE = {
	"m": "3-stem compression at heavy weight",
	"u": "arch widens at heavy weight",
	"k": "diagonal leg interferes at heavy weight",
	"K": "diagonal leg interferes at heavy weight",
	"t": "crossbar junction at heavy weight",
	"A": "diagonal perpendicular unreliable at heavy weight",
	# Figures
	"one": "flag/serif width dominates at heavy weight",
	"three": "double bowl compression at heavy weight",
	"five": "bowl/flag junction at heavy weight",
	"six": "bowl shape changes at heavy weight",
	"seven": "horizontal bar dominates at heavy weight",
	"eight": "spine compression at heavy weight",
}


# ── Industry color (ink density) patterns ─────────────────────────────────────
# Expected density as percentage of reference glyph (n for LC, H for UC).
# Based on analysis of professional fonts at heavy weights.
# Categories: "stable" (range < 6%), "moderate" (6-10%), "unreliable" (> 10%).
COLOR_PATTERNS = {
	# LC — ratio to n (as percentage, 100 = same as n)
	"h": {"expected": 100.2, "maxDev": 3, "reliability": "stable"},
	"m": {"expected": 102.3, "maxDev": 3, "reliability": "stable"},
	"r": {"expected": 86.3, "maxDev": 3, "reliability": "stable"},
	"u": {"expected": 99.3, "maxDev": 5, "reliability": "stable"},
	"k": {"expected": 100.2, "maxDev": 5, "reliability": "stable"},
	"f": {"expected": 86.1, "maxDev": 6, "reliability": "stable"},
	"c": {"expected": 90.2, "maxDev": 6, "reliability": "stable"},
	"v": {"expected": 86.3, "maxDev": 8, "reliability": "moderate"},
	"b": {"expected": 106.3, "maxDev": 9, "reliability": "moderate"},
	"d": {"expected": 106.3, "maxDev": 9, "reliability": "moderate"},
	"p": {"expected": 106.3, "maxDev": 9, "reliability": "moderate"},
	"q": {"expected": 106.4, "maxDev": 9, "reliability": "moderate"},
	"o": {"expected": 100.0, "maxDev": 10, "reliability": "moderate"},
	"w": {"expected": 98.9, "maxDev": 10, "reliability": "moderate"},
	"a": {"unreliable": True, "note": "construction-dependent (range=16%)"},
	"e": {"unreliable": True, "note": "construction-dependent (range=19%)"},
	"g": {"unreliable": True, "note": "descender varies (range=14%)"},
	"i": {"unreliable": True, "note": "dot proportion varies (range=10%)"},
	"j": {"unreliable": True, "note": "dot + descender varies (range=13%)"},
	"l": {"unreliable": True, "note": "width proportion varies (range=10%)"},
	"s": {"unreliable": True, "note": "spine varies (range=15%)"},
	"t": {"unreliable": True, "note": "crossbar proportion varies (range=14%)"},
	"x": {"unreliable": True, "note": "diagonal varies (range=12%)"},
	"y": {"unreliable": True, "note": "descender varies (range=16%)"},
	"z": {"unreliable": True, "note": "bar varies (range=17%)"},
	"n": {"expected": 100.0, "maxDev": 0, "reliability": "reference"},
	# UC — ratio to H (as percentage, 100 = same as H)
	"H": {"expected": 100.0, "maxDev": 0, "reliability": "reference"},
	"I": {"expected": 100.2, "maxDev": 7, "reliability": "stable"},
	"U": {"expected": 95.4, "maxDev": 5, "reliability": "stable"},
	"F": {"expected": 92.9, "maxDev": 5, "reliability": "stable"},
	"T": {"expected": 78.0, "maxDev": 7, "reliability": "stable"},
	"K": {"expected": 99.6, "maxDev": 8, "reliability": "stable"},
	"L": {"expected": 79.2, "maxDev": 8, "reliability": "moderate"},
	"O": {"expected": 97.1, "maxDev": 9, "reliability": "moderate"},
	"C": {"expected": 87.7, "maxDev": 10, "reliability": "moderate"},
	"Y": {"expected": 72.9, "maxDev": 10, "reliability": "moderate"},
	"V": {"expected": 86.4, "maxDev": 10, "reliability": "moderate"},
	"J": {"expected": 79.1, "maxDev": 11, "reliability": "moderate"},
	"D": {"expected": 104.8, "maxDev": 12, "reliability": "moderate"},
	"A": {"unreliable": True, "note": "varies by apex design (range=16%)"},
	"B": {"unreliable": True, "note": "double bowl varies (range=23%)"},
	"E": {"unreliable": True, "note": "bar proportion varies (range=16%)"},
	"G": {"unreliable": True, "note": "mixed round/spur varies (range=17%)"},
	"M": {"unreliable": True, "note": "diagonal proportion varies (range=19%)"},
	"N": {"unreliable": True, "note": "diagonal varies (range=12%)"},
	"P": {"unreliable": True, "note": "bowl varies (range=12%)"},
	"Q": {"unreliable": True, "note": "tail varies (range=15%)"},
	"R": {"unreliable": True, "note": "bowl + leg varies (range=15%)"},
	"S": {"unreliable": True, "note": "spine varies (range=18%)"},
	"W": {"unreliable": True, "note": "diagonal proportion varies (range=13%)"},
	"X": {"unreliable": True, "note": "diagonal varies (range=16%)"},
	"Z": {"unreliable": True, "note": "bar varies (range=17%)"},
	# Figures — ratio to H (figures use capHeight zone)
	"zero":  {"expected": 108.6, "maxDev": 5, "reliability": "stable"},
	"one":   {"expected": 92.4, "maxDev": 7, "reliability": "moderate"},
	"two":   {"expected": 104.3, "maxDev": 9, "reliability": "moderate"},
	"three": {"expected": 104.7, "maxDev": 6, "reliability": "stable"},
	"four":  {"unreliable": True, "note": "open form varies (range=22%)"},
	"five":  {"unreliable": True, "note": "flag/bowl ratio varies (range=19%)"},
	"six":   {"unreliable": True, "note": "open form varies (range=22%)"},
	"seven": {"expected": 84.5, "maxDev": 8, "reliability": "moderate"},
	"eight": {"expected": 124.5, "maxDev": 5, "reliability": "stable"},
	"nine":  {"unreliable": True, "note": "bowl varies (range=20%)"},
}


def _evaluate_color(glyph_name, measured_density, reference_density):
	"""Evaluate a glyph's ink density against industry color patterns.

	Density is compared as a percentage ratio to the reference glyph
	(n for lowercase, H for uppercase). Patterns define expected ratios
	with tolerance ranges based on empirical professional font data.

	Returns dict with: glyph, density, ratio, expectedRatio, verdict, color, note.
	"""
	base = glyph_name.split(".")[0]
	pattern = COLOR_PATTERNS.get(base)

	if reference_density <= 0:
		return {
			"glyph": glyph_name, "density": round(measured_density, 4),
			"verdict": "unreliable", "color": 1, "note": "Reference density is zero",
		}

	ratio_pct = (measured_density / reference_density) * 100.0

	result = {
		"glyph": glyph_name,
		"density": round(measured_density, 4),
		"ratioPct": round(ratio_pct, 1),
		"reference": round(reference_density, 4),
	}

	if pattern is None:
		# Unknown glyph — use generous 12% tolerance
		if abs(ratio_pct - 100.0) <= 12:
			result.update({"verdict": "pass", "color": 4})
		else:
			result.update({"verdict": "inconsistent", "color": 0,
				"note": "Unknown glyph, density ratio %.1f%% (expected ~100%%)" % ratio_pct})
		return result

	if pattern.get("unreliable"):
		result.update({"verdict": "unreliable", "color": 1,
			"note": pattern.get("note", "Measurement unreliable")})
		return result

	expected = pattern["expected"]
	max_dev = pattern["maxDev"]
	deviation = ratio_pct - expected
	result["expectedRatioPct"] = expected

	if abs(deviation) <= max_dev:
		result.update({"verdict": "pass", "color": 4})
	elif abs(deviation) <= max_dev * 1.5:
		result.update({"verdict": "compensation", "color": 3,
			"note": "Density ratio %.1f%% (expected %.1f%% +/-%.0f%%)" % (ratio_pct, expected, max_dev)})
	else:
		result.update({"verdict": "inconsistent", "color": 0,
			"note": "Density ratio %.1f%% far from expected %.1f%% +/-%.0f%%" % (ratio_pct, expected, max_dev)})
	return result


def _evaluate_stem(glyph_name, measured_value, reference_value):
	"""Evaluate a glyph's stem against industry stem patterns.

	All deviations are relative to the straight reference (n for LC, H for UC).
	Tolerances scale proportionally with stem weight:
	  weight_factor = max(1.0, reference / 100.0)
	  scaled_maxDev = max(original, round(original * factor))
	  scaled_range = [round(lo * factor), round(hi * factor)]

	At heavy weights (ref > 120u), m/u/k/t are moved to unreliable
	because their measurements break down due to construction changes.

	Returns dict with: glyph, value, deviation, reference, verdict, color, note.
	"""
	base = glyph_name.split(".")[0]
	pattern = STEM_PATTERNS.get(base)
	deviation = int(round(measured_value - reference_value))
	abs_dev = abs(deviation)
	ref = float(reference_value)

	# Weight factor: scales tolerances for heavier stems
	weight_factor = max(1.0, ref / 100.0)

	result = {
		"glyph": glyph_name,
		"value": int(round(measured_value)),
		"deviation": deviation,
		"reference": int(round(reference_value)),
	}

	# At heavy weights, some glyphs become unreliable
	if ref > 120 and base in _HEAVY_UNRELIABLE:
		result.update({"verdict": "unreliable", "color": 1,
			"note": _HEAVY_UNRELIABLE[base]})
		return result

	if pattern is None:
		scaled_default = max(3, int(round(3 * weight_factor)))
		if abs_dev <= scaled_default:
			result.update({"verdict": "pass", "color": 4})
		else:
			result.update({"verdict": "inconsistent", "color": 0,
				"note": "Unknown glyph, exceeds +/-%du" % scaled_default})
		return result

	if pattern.get("unreliable"):
		result.update({"verdict": "unreliable", "color": 1,
			"note": pattern.get("note", "Measurement unreliable")})
		return result

	if "range" in pattern:
		lo = int(round(pattern["range"][0] * weight_factor))
		hi = int(round(pattern["range"][1] * weight_factor))
		# At heavy weights, compensation can reverse direction
		# Allow up to half the positive range in the negative direction
		if weight_factor > 1.2 and pattern["range"][0] >= 0:
			lo = -max(1, abs(hi) // 2)
		if lo <= deviation <= hi:
			result.update({"verdict": "compensation", "color": 3,
				"note": pattern.get("note", "Expected optical compensation")})
		else:
			result.update({"verdict": "inconsistent", "color": 0,
				"expectedRange": [lo, hi],
				"note": "Outside expected range [%d, %d]u — %s" % (lo, hi, pattern.get("note", ""))})
		return result

	if "maxDev" in pattern:
		scaled_max = max(pattern["maxDev"], int(round(pattern["maxDev"] * weight_factor)))
		if abs_dev <= scaled_max:
			result.update({"verdict": "pass", "color": 4})
		else:
			result.update({"verdict": "inconsistent", "color": 0,
				"maxExpected": scaled_max,
				"note": "Deviation %du exceeds +/-%du expected" % (deviation, scaled_max)})
		return result

	result.update({"verdict": "pass", "color": 4})
	return result


def _resolve_component_base(layer, font, master_id):
	"""For a component-only layer, find the base glyph's layer to measure.

	Follows the first component reference. If the base glyph also has only
	components, follows recursively (max 5 levels).

	Returns (resolved_layer, base_glyph_name) or (None, None) if unresolvable.
	"""
	visited = set()
	current_layer = layer
	current_name = layer.parent.name if layer.parent else None

	for _ in range(5):
		if len(current_layer.paths) > 0:
			return current_layer, current_name

		if len(current_layer.components) == 0:
			return None, None

		comp_name = str(current_layer.components[0].componentName)
		if comp_name in visited:
			return None, None
		visited.add(comp_name)

		base_glyph = font.glyphs[comp_name]
		if base_glyph is None:
			return None, None

		current_layer = base_glyph.layers[master_id]
		current_name = comp_name

	return None, None


# ── GET /api/font/glyphs/{name}/stems ────────────────────────────────────────

@route("GET", "/api/font/glyphs/{name}/stems")
def handle_get_stems(bridge, name, query=None, **kwargs):
	"""Measure stem thicknesses in a glyph using perpendicular ray-casting.

	Automatically decomposes components and removes overlaps for accurate
	measurement of all glyph constructions.
	"""
	master_id = (query or {}).get("master", [None])[0]
	samples = int((query or {}).get("samples", ["10"])[0])

	def _measure():
		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")

		mid = master_id or str(font.masters[0].id)
		layer = glyph.layers[mid]

		if len(layer.paths) == 0 and len(layer.components) == 0:
			return {"glyphName": name, "error": "Glyph has no outlines"}

		result = {"glyphName": name, "width": int(round(layer.width))}
		result["auto"] = _auto_measure_glyph(layer, num_samples=samples)
		return result

	result = bridge.execute_on_main(_measure)
	return 200, result


# ── POST /api/font/stems/compare ─────────────────────────────────────────────

def _classify_stem_group(glyph_name):
	"""Classify a glyph into one of 5 stem measurement groups.

	Groups determine both measurement strategy and how results are reported:
	  straight: pure stems, compare against ref (n/H). Strategy: frequency.
	  round: pure round forms, compare against ref. Strategy: frequency.
	  mixed: stem + bowl, compare stem against ref. Strategy: nearest_ref.
	  diagonal: diagonal strokes, reported only. Strategy: frequency.
	  optical: known optical special cases (t, f). Strategy: frequency.
	  figure: number glyphs, compare against H. Strategy varies.

	Returns group name string.
	"""
	base = glyph_name.split(".")[0]

	# Figures
	_fig_mixed = {"zero", "three", "six", "eight", "nine"}
	_fig_straight = {"one", "four"}
	_fig_unreliable = {"two", "five", "seven"}
	if base in _fig_mixed or base in _fig_straight or base in _fig_unreliable:
		if base in _fig_mixed:
			return "mixed"
		return "figure"

	# LC groups
	_straight_lc = {"n", "h", "m", "u", "i", "j", "l", "r", "dotlessi"}
	_round_lc = {"o", "c"}
	_mixed_lc = {"b", "d", "p", "q", "g", "a", "e", "s"}
	_diagonal_lc = {"v", "w", "x", "y", "z", "k"}
	_optical_lc = {"t", "f"}

	# UC groups
	_straight_uc = {"H", "I", "L", "T", "U", "F", "E", "K", "J"}
	_round_uc = {"O", "C", "Q"}
	_mixed_uc = {"D", "B", "P", "R", "G"}
	_diagonal_uc = {"V", "W", "X", "Y", "Z", "A", "M", "N"}

	if base in _straight_lc or base in _straight_uc:
		return "straight"
	if base in _round_lc or base in _round_uc:
		return "round"
	if base in _mixed_lc or base in _mixed_uc:
		return "mixed"
	if base in _diagonal_lc or base in _diagonal_uc:
		return "diagonal"
	if base in _optical_lc:
		return "optical"
	return "straight"  # default for unknown glyphs


def _analyze_one_master(font, glyph_names, mid, master_name):
	"""Run stem comparison for a single master. Returns (reference, evaluations, summary)."""

	def _measure_ref(ref_name):
		g = font.glyphs[ref_name]
		if g is None:
			return None
		lyr = g.layers[mid]
		if len(lyr.paths) == 0 and len(lyr.components) == 0:
			return None
		auto = _auto_measure_glyph(lyr, strategy="frequency")
		return auto["verticalStems"]["dominant"]

	lc_ref = _measure_ref("n")
	uc_ref = _measure_ref("H")

	ref_info = {}
	if lc_ref is not None:
		ref_info["lowercase"] = {"glyph": "n", "verticalStem": int(round(lc_ref))}
	if uc_ref is not None:
		ref_info["uppercase"] = {"glyph": "H", "verticalStem": int(round(uc_ref))}

	evaluations = []
	summary = {"pass": 0, "compensation": 0, "inconsistent": 0, "unreliable": 0}

	for gname in glyph_names:
		glyph = font.glyphs[gname]
		if glyph is None:
			evaluations.append({"glyph": gname, "error": "not found"})
			continue

		layer = glyph.layers[mid]
		if len(layer.paths) == 0 and len(layer.components) == 0:
			evaluations.append({"glyph": gname, "error": "no outlines"})
			continue

		gclass = _classify_glyph(glyph)
		use_uc_ref = gclass == "uppercase" or gclass == "figure"
		ref_value = uc_ref if use_uc_ref else lc_ref

		if ref_value is None:
			ref_name = "H" if use_uc_ref else "n"
			evaluations.append({"glyph": gname, "error": "no %s reference" % ref_name})
			continue

		stem_group = _classify_stem_group(gname)
		if stem_group == "mixed":
			auto = _auto_measure_glyph(layer, strategy="nearest_ref",
				strategy_kwargs={"reference": ref_value})
		else:
			auto = _auto_measure_glyph(layer, strategy="frequency")

		v_dom = auto["verticalStems"]["dominant"]
		h_dom = auto["horizontalStems"]["dominant"]

		if v_dom is None:
			entry = {"glyph": gname, "group": stem_group, "verdict": "unreliable",
				"note": "No vertical stems measured", "color": 1,
				"width": int(round(layer.width))}
			evaluations.append(entry)
			summary["unreliable"] += 1
			continue

		evaluation = _evaluate_stem(gname, v_dom, ref_value)
		evaluation["group"] = stem_group
		evaluation["width"] = int(round(layer.width))
		if h_dom is not None:
			evaluation["horizontalStem"] = int(round(h_dom))
		evaluations.append(evaluation)
		summary[evaluation["verdict"]] += 1

	return ref_info, evaluations, summary


@route("POST", "/api/font/stems/compare")
def handle_compare_stems(bridge, body=None, **kwargs):
	"""Compare stem thicknesses using industry stem patterns.

	Evaluates each glyph's stem against expected optical compensation
	patterns derived from professional text fonts. Per-glyph verdicts:
	- pass: stem matches reference within expected tolerance
	- compensation: known optical compensation (round stems, mass, bowl)
	- inconsistent: deviation exceeds expected range — likely a real issue
	- unreliable: glyph shape can't be reliably measured by ray-casting

	If masterId is provided, analyzes that master only.
	If omitted, analyzes ALL masters and marks each glyph with the
	worst verdict across masters (red if any master has issues).

	Auto-marks glyphs in GlyphsApp:
	  Red (0) = inconsistent, Orange (1) = unreliable,
	  Yellow (3) = compensation, Light green (4) = passed.
	"""
	if not body or "glyphNames" not in body:
		return 400, {"error": "Body must contain 'glyphNames'"}

	glyph_names = body["glyphNames"]
	master_id = body.get("masterId", None)

	def _compare():
		font = _require_font()

		# Determine which masters to analyze
		if master_id:
			masters_to_check = []
			for m in font.masters:
				if str(m.id) == str(master_id):
					masters_to_check = [(str(m.id), str(m.name))]
					break
			if not masters_to_check:
				return {"error": "Master '%s' not found" % master_id}
		else:
			masters_to_check = [(str(m.id), str(m.name)) for m in font.masters]

		# Analyze each master
		per_master = {}
		for mid, mname in masters_to_check:
			ref_info, evaluations, summary = _analyze_one_master(
				font, glyph_names, mid, mname)
			per_master[mname] = {
				"masterId": mid,
				"reference": ref_info,
				"glyphs": evaluations,
				"summary": summary,
			}

		# Compute worst color per glyph across all masters
		# Lower color number = worse (0=red, 1=orange, 3=yellow, 4=green)
		worst_color = {}
		for gname in glyph_names:
			worst = 4  # start optimistic
			for mname in per_master:
				for ev in per_master[mname]["glyphs"]:
					if ev.get("glyph") == gname and "color" in ev:
						if ev["color"] < worst:
							worst = ev["color"]
			worst_color[gname] = worst

		# Apply colors to glyphs
		for gname in glyph_names:
			glyph = font.glyphs[gname]
			if glyph is not None:
				glyph.color = worst_color.get(gname, 4)

		result = {
			"masters": per_master,
			"mastersAnalyzed": [m[1] for m in masters_to_check],
			"worstPerGlyph": {g: worst_color[g] for g in glyph_names if worst_color.get(g, 4) < 4},
			"industryPatterns": {
				"ucLcRatio": {"range": [1.033, 1.055], "typical": 1.044},
				"roundCompensation": {"lc": [0, 7], "uc": [0, 4]},
			},
			"colorLegend": {
				"red (0)": "Inconsistent — outside expected range",
				"orange (1)": "Unreliable measurement",
				"yellow (3)": "Optical compensation (within expected range)",
				"lightGreen (4)": "Passed",
			},
		}
		return result

	result = bridge.execute_on_main(_compare)
	return 200, result


# ── GET /api/font/stems/targets ──────────────────────────────────────────────

@route("GET", "/api/font/stems/targets")
def handle_get_stem_targets(bridge, query=None, **kwargs):
	"""Get stem target values from font metrics and reference glyphs."""
	master_id = (query or {}).get("master", [None])[0]

	def _get_targets():
		font = _require_font()
		master = None
		if master_id:
			for m in font.masters:
				if str(m.id) == str(master_id):
					master = m
					break
		if master is None:
			master = font.masters[0]

		result = {
			"masterId": str(master.id),
			"masterName": str(master.name),
			"stems": {},
			"reference": {},
		}

		# Try font.stems / master.stems (Glyphs 3 Dimensions palette)
		if len(font.stems) > 0:
			for i, stem in enumerate(font.stems):
				try:
					val = master.stems[i]
					result["stems"][str(stem.name)] = float(val)
				except Exception:
					pass

		# Try custom parameters
		for param_name in ["postscriptStemSnapH", "postscriptStemSnapV"]:
			val = master.customParameters[param_name]
			if val is not None:
				try:
					result["stems"][param_name] = [float(v) for v in val]
				except (TypeError, ValueError):
					pass

		# Measure reference glyphs
		ref_map = {
			"H": "uppercase_straight",
			"O": "uppercase_round",
			"n": "lowercase_straight",
			"o": "lowercase_round",
		}

		for gname, gtype in ref_map.items():
			glyph = font.glyphs[gname]
			if glyph is None:
				continue
			layer = glyph.layers[master.id]
			if len(layer.paths) == 0:
				continue

			auto = _auto_measure_glyph(layer, num_samples=8)
			result["reference"][gname] = {
				"verticalStem": auto["verticalStems"]["dominant"],
				"horizontalStem": auto["horizontalStems"]["dominant"],
				"type": gtype,
			}

		return result

	result = bridge.execute_on_main(_get_targets)
	return 200, result


# ── GET /api/font/glyphs/{name}/ink-density ──────────────────────────────────

@route("GET", "/api/font/glyphs/{name}/ink-density")
def handle_get_glyph_ink_density(bridge, name, query=None, **kwargs):
	"""Get ink density / typographic color analysis for a glyph."""
	master_id = (query or {}).get("master", [None])[0]
	resolution = int((query or {}).get("resolution", ["10"])[0])

	def _get_density():
		from Foundation import NSPoint

		font = _require_font()
		glyph = font.glyphs[name]
		if glyph is None:
			raise KeyError(f"Glyph '{name}' not found")

		master = font.masters[0]
		if master_id:
			for m in font.masters:
				if str(m.id) == str(master_id):
					master = m
					break

		layer = glyph.layers[master.id]

		if len(layer.paths) == 0 and len(layer.components) == 0:
			return {"glyphName": name, "error": "No outlines"}

		# Determine zone
		gclass = _classify_glyph(glyph)

		if gclass == "uppercase":
			zone_height = float(master.capHeight)
			zone_name = "uppercase"
			zone_bottom = 0
		elif gclass == "lowercase":
			zone_height = float(master.xHeight)
			zone_name = "lowercase"
			zone_bottom = 0
		elif gclass == "figure":
			zone_height = float(master.capHeight)
			zone_name = "figures"
			zone_bottom = 0
		else:
			bounds = layer.bounds
			zone_height = float(bounds.size.height) if bounds else 0
			zone_name = "other"
			zone_bottom = float(bounds.origin.y) if bounds else 0

		if zone_height <= 0 or layer.width <= 0:
			return {"glyphName": name, "error": "Invalid dimensions"}

		# Scanline-based ink density
		total_length = 0.0
		filled_length = 0.0

		y = zone_bottom + resolution / 2.0
		while y < zone_bottom + zone_height:
			p1 = NSPoint(-1, y)
			p2 = NSPoint(float(layer.width) + 1, y)

			raw = layer.intersectionsBetweenPoints(p1, p2)
			if raw:
				xs = sorted([float(p.x) for p in raw])
				xs = [x for x in xs if 0 <= x <= float(layer.width)]

				for i in range(0, len(xs) - 1, 2):
					filled_length += (xs[i + 1] - xs[i])

			total_length += float(layer.width)
			y += resolution

		ink_density = filled_length / total_length if total_length > 0 else 0

		return {
			"glyphName": name,
			"width": float(layer.width),
			"inkDensity": round(ink_density, 4),
			"inkArea": round(filled_length * resolution, 0),
			"boxArea": round(total_length * resolution, 0),
			"zone": zone_name,
			"zoneHeight": zone_height,
		}

	result = bridge.execute_on_main(_get_density)
	return 200, result


# ── Shared density measurement helper ────────────────────────────────────────

def _measure_glyph_density(layer, zone_height, zone_bottom, resolution, NSPoint):
	"""Measure ink density on a layer using scanline ray-casting."""
	if zone_height <= 0 or layer.width <= 0:
		return None
	total_len = 0.0
	filled_len = 0.0
	y = zone_bottom + resolution / 2.0
	while y < zone_bottom + zone_height:
		p1 = NSPoint(-1, y)
		p2 = NSPoint(float(layer.width) + 1, y)
		raw = layer.intersectionsBetweenPoints(p1, p2)
		if raw:
			xs = sorted([float(p.x) for p in raw])
			xs = [x for x in xs if 0 <= x <= float(layer.width)]
			for i in range(0, len(xs) - 1, 2):
				filled_len += (xs[i + 1] - xs[i])
		total_len += float(layer.width)
		y += resolution
	return filled_len / total_len if total_len > 0 else 0


# ── POST /api/font/color/compare ─────────────────────────────────────────────

@route("POST", "/api/font/color/compare")
def handle_compare_color(bridge, body=None, **kwargs):
	"""Compare typographic color using industry density patterns.

	Uses per-glyph expected density ratios relative to reference glyphs
	(n for lowercase, H for uppercase). Each glyph gets a verdict:
	pass, compensation, inconsistent, or unreliable.

	When masterId is omitted, analyzes ALL masters. Glyph color in GlyphsApp
	is set to the worst verdict across all masters.
	"""
	if not body or "glyphNames" not in body:
		return 400, {"error": "Body must contain 'glyphNames'"}

	glyph_names = body["glyphNames"]
	master_id = body.get("masterId", None)
	resolution = body.get("resolution", 10)

	def _compare_color():
		from Foundation import NSPoint

		font = _require_font()

		# Determine which masters to analyze
		if master_id:
			masters_to_analyze = []
			for m in font.masters:
				if str(m.id) == str(master_id):
					masters_to_analyze = [m]
					break
			if not masters_to_analyze:
				return {"error": "Master not found: %s" % master_id}
		else:
			masters_to_analyze = list(font.masters)

		all_master_results = {}
		worst_per_glyph = {}  # glyph -> worst color across masters

		for master in masters_to_analyze:
			mid = str(master.id)
			mname = str(master.name)

			# Find reference densities: n for LC, H for UC
			ref_lc = None
			ref_uc = None

			n_glyph = font.glyphs["n"]
			if n_glyph:
				n_layer = n_glyph.layers[master.id]
				if len(n_layer.paths) > 0 or len(n_layer.components) > 0:
					ref_lc = _measure_glyph_density(
						n_layer, float(master.xHeight), 0, resolution, NSPoint)

			h_glyph = font.glyphs["H"]
			if h_glyph:
				h_layer = h_glyph.layers[master.id]
				if len(h_layer.paths) > 0 or len(h_layer.components) > 0:
					ref_uc = _measure_glyph_density(
						h_layer, float(master.capHeight), 0, resolution, NSPoint)

			evaluations = []

			for gname in glyph_names:
				glyph = font.glyphs[gname]
				if glyph is None:
					evaluations.append({"glyph": gname, "verdict": "error", "note": "not found"})
					continue

				layer = glyph.layers[master.id]
				if len(layer.paths) == 0 and len(layer.components) == 0:
					evaluations.append({"glyph": gname, "verdict": "error", "note": "no outlines"})
					continue

				gclass = _classify_glyph(glyph)
				if gclass == "lowercase":
					zone_height = float(master.xHeight)
					ref_density = ref_lc
				else:
					zone_height = float(master.capHeight)
					ref_density = ref_uc

				density = _measure_glyph_density(
					layer, zone_height, 0, resolution, NSPoint)

				if density is None:
					evaluations.append({"glyph": gname, "verdict": "error", "note": "invalid dimensions"})
					continue

				if ref_density and ref_density > 0:
					ev = _evaluate_color(gname, density, ref_density)
				else:
					ev = {
						"glyph": gname, "density": round(density, 4),
						"verdict": "unreliable", "color": 1,
						"note": "No reference glyph available",
					}

				ev["width"] = float(layer.width)
				evaluations.append(ev)

				# Track worst color per glyph (lower color number = worse)
				color = ev.get("color", 4)
				if gname not in worst_per_glyph or color < worst_per_glyph[gname]:
					worst_per_glyph[gname] = color

			# Count verdicts for this master
			verdicts = {}
			for ev in evaluations:
				v = ev.get("verdict", "error")
				verdicts[v] = verdicts.get(v, 0) + 1

			all_master_results[mid] = {
				"masterName": mname,
				"referenceLc": round(ref_lc, 4) if ref_lc else None,
				"referenceUc": round(ref_uc, 4) if ref_uc else None,
				"glyphs": evaluations,
				"verdictCounts": verdicts,
			}

		# Mark glyphs in GlyphsApp with worst color across masters
		for gname, color in worst_per_glyph.items():
			glyph = font.glyphs[gname]
			if glyph:
				glyph.color = color

		# Build response
		response = {
			"masters": all_master_results,
			"mastersAnalyzed": len(masters_to_analyze),
			"worstPerGlyph": worst_per_glyph,
			"industryPatterns": {
				"lcUcRatio": [1.10, 1.16],
				"colorScheme": {
					0: "red = inconsistent",
					1: "orange = unreliable measurement",
					3: "yellow = optical compensation",
					4: "green = pass",
				},
			},
		}

		return response

	result = bridge.execute_on_main(_compare_color)
	return 200, result


# ── POST /api/font/color/audit ───────────────────────────────────────────────

@route("POST", "/api/font/color/audit")
def handle_color_audit(bridge, body=None, **kwargs):
	"""Full font color audit using industry density patterns.

	Groups glyphs into uppercase, lowercase, and figures, then evaluates
	each glyph against expected density ratios. When masterId is omitted,
	analyzes ALL masters with worst-verdict-wins for glyph colors.
	"""
	master_id = (body or {}).get("masterId", None)
	resolution = (body or {}).get("resolution", 15)

	def _audit():
		from Foundation import NSPoint
		import math

		font = _require_font()

		# Determine which masters to analyze
		if master_id:
			masters_to_analyze = []
			for m in font.masters:
				if str(m.id) == str(master_id):
					masters_to_analyze = [m]
					break
			if not masters_to_analyze:
				return {"error": "Master not found: %s" % master_id}
		else:
			masters_to_analyze = list(font.masters)

		all_master_results = {}
		worst_per_glyph = {}

		for master in masters_to_analyze:
			mid = str(master.id)
			mname = str(master.name)

			# Find reference densities
			ref_lc = None
			ref_uc = None

			n_glyph = font.glyphs["n"]
			if n_glyph:
				n_layer = n_glyph.layers[master.id]
				if len(n_layer.paths) > 0 or len(n_layer.components) > 0:
					ref_lc = _measure_glyph_density(
						n_layer, float(master.xHeight), 0, resolution, NSPoint)

			h_glyph = font.glyphs["H"]
			if h_glyph:
				h_layer = h_glyph.layers[master.id]
				if len(h_layer.paths) > 0 or len(h_layer.components) > 0:
					ref_uc = _measure_glyph_density(
						h_layer, float(master.capHeight), 0, resolution, NSPoint)

			groups = {"uppercase": [], "lowercase": [], "figures": []}

			for glyph in font.glyphs:
				gclass = _classify_glyph(glyph)
				if gclass is None:
					continue

				layer = glyph.layers[master.id]
				if len(layer.paths) == 0 and len(layer.components) == 0:
					continue

				if gclass == "uppercase":
					group = "uppercase"
					zone_height = float(master.capHeight)
					ref_density = ref_uc
				elif gclass == "lowercase":
					group = "lowercase"
					zone_height = float(master.xHeight)
					ref_density = ref_lc
				elif gclass == "figure":
					group = "figures"
					zone_height = float(master.capHeight)
					ref_density = ref_uc
				else:
					continue

				density = _measure_glyph_density(
					layer, zone_height, 0, resolution, NSPoint)
				if density is None:
					continue

				gname = str(glyph.name)

				if ref_density and ref_density > 0:
					ev = _evaluate_color(gname, density, ref_density)
				else:
					ev = {
						"glyph": gname, "density": round(density, 4),
						"verdict": "unreliable", "color": 1,
						"note": "No reference glyph available",
					}
				ev["width"] = float(layer.width)

				groups[group].append(ev)

				# Track worst color per glyph
				color = ev.get("color", 4)
				if gname not in worst_per_glyph or color < worst_per_glyph[gname]:
					worst_per_glyph[gname] = color

			# Per-group statistics
			master_result = {}
			group_means = {}

			for group_name, glyphs in groups.items():
				if not glyphs:
					master_result[group_name] = {"glyphs": [], "count": 0}
					continue

				values = [g["density"] for g in glyphs]
				mean = sum(values) / len(values)
				sorted_vals = sorted(values)
				median = sorted_vals[len(sorted_vals) // 2]
				variance = sum((v - mean) ** 2 for v in values) / len(values)
				stddev = math.sqrt(variance)

				verdicts = {}
				for g in glyphs:
					v = g.get("verdict", "error")
					verdicts[v] = verdicts.get(v, 0) + 1

				glyphs.sort(key=lambda g: g["density"])

				master_result[group_name] = {
					"glyphs": glyphs,
					"count": len(glyphs),
					"mean": round(mean, 4),
					"median": round(median, 4),
					"stddev": round(stddev, 4),
					"verdictCounts": verdicts,
				}
				group_means[group_name] = mean

			uc_mean = group_means.get("uppercase", 0)
			lc_mean = group_means.get("lowercase", 0)

			master_result["overall"] = {
				"masterName": mname,
				"referenceLc": round(ref_lc, 4) if ref_lc else None,
				"referenceUc": round(ref_uc, 4) if ref_uc else None,
				"uppercaseMean": round(uc_mean, 4) if uc_mean else None,
				"lowercaseMean": round(lc_mean, 4) if lc_mean else None,
				"figuresMean": round(group_means.get("figures", 0), 4) or None,
				"lcToUcRatio": round(lc_mean / uc_mean, 3) if uc_mean and lc_mean else None,
			}

			all_master_results[mid] = master_result

		# Mark glyphs in GlyphsApp with worst color across masters
		for gname, color in worst_per_glyph.items():
			glyph = font.glyphs[gname]
			if glyph:
				glyph.color = color

		return {
			"masters": all_master_results,
			"mastersAnalyzed": len(masters_to_analyze),
			"worstPerGlyph": worst_per_glyph,
			"industryPatterns": {
				"lcUcRatio": [1.10, 1.16],
				"colorScheme": {
					0: "red = inconsistent",
					1: "orange = unreliable measurement",
					3: "yellow = optical compensation",
					4: "green = pass",
				},
			},
		}

	result = bridge.execute_on_main(_audit)
	return 200, result


# ── POST /api/font/overshoots/check ──────────────────────────────────────────

# Expected overshoot as percentage of zone height (from Designing Type)
# Round forms overshoot ~1-2%, pointed forms need more than rounds.
_OVERSHOOT_GLYPHS = {
	# UC round — overshoot both top (capHeight) and bottom (baseline)
	"O": "round", "C": "round", "G": "round", "Q": "round", "S": "round",
	# UC round-bottom only (stems define flat top at capHeight)
	"U": "round_bottom", "J": "round_bottom",
	# D is flat top AND bottom (stem defines both extremes) — not checked
	# UC pointed — overshoot top and/or bottom
	"A": "pointed", "V": "pointed", "W": "pointed", "M": "pointed", "N": "pointed",
	# LC round — overshoot both top (xHeight) and bottom (baseline)
	"o": "round", "c": "round", "e": "round", "s": "round",
	# LC round-bottom only (top is flat at xHeight or has ascender)
	"b": "round_bottom", "d": "round_bottom", "p": "round_bottom", "q": "round_bottom",
	"g": "round_bottom", "a": "round_bottom", "u": "round_bottom",
	# LC pointed
	"v": "pointed", "w": "pointed", "y": "pointed",
	# Figures
	"zero": "round", "three": "round", "six": "round",
	"eight": "round", "nine": "round",
	"two": "round_bottom", "five": "round_bottom",
}


def _is_pointed_apex(layer, at_top=True, threshold_pct=0.05):
	"""Detect if a glyph has a pointed apex/vertex at the top or bottom.

	Looks at on-curve nodes near the y-extreme and measures their horizontal
	spread. If the x-span is < threshold_pct of glyph width, the apex is
	pointed (needs overshoot). If wider, it's flat/truncated (no overshoot needed).

	Args:
		layer: GSLayer to analyze
		at_top: True for top apex (A, M, N), False for bottom vertex (V, W)
		threshold_pct: max x-span as fraction of width to be considered pointed

	Returns: (is_pointed: bool, x_span: float, node_count: int)
	"""
	bounds = layer.bounds
	if bounds is None or bounds.size.width == 0:
		return False, 0, 0

	if at_top:
		y_target = float(bounds.origin.y + bounds.size.height)
	else:
		y_target = float(bounds.origin.y)

	glyph_w = float(layer.width)
	if glyph_w <= 0:
		return False, 0, 0

	# Tolerance: 20u or 3% of bounds height, whichever is larger
	tol = max(20, bounds.size.height * 0.03)

	near_nodes = []
	for path in layer.paths:
		for node in path.nodes:
			ntype = str(node.type)
			if ntype in ("line", "curve", "qcurve"):
				ny = float(node.position.y)
				if abs(ny - y_target) < tol:
					near_nodes.append(float(node.position.x))

	if not near_nodes:
		return False, 0, 0

	x_span = max(near_nodes) - min(near_nodes)
	is_pointed = x_span < glyph_w * threshold_pct
	return is_pointed, x_span, len(near_nodes)


@route("POST", "/api/font/overshoots/check")
def handle_check_overshoots(bridge, body=None, **kwargs):
	"""Check overshoot values for round and pointed forms.

	Round forms (O, o, etc.) should overshoot baseline and zone top by ~1-2%
	of zone height. Pointed forms (A, V, W) should overshoot more than rounds.

	Returns per-glyph top and bottom overshoot measurements with verdicts.
	"""
	glyph_names = (body or {}).get("glyphNames", None)
	master_id = (body or {}).get("masterId", None)

	def _check():
		font = _require_font()

		# Determine which masters to analyze
		if master_id:
			masters_to_analyze = []
			for m in font.masters:
				if str(m.id) == str(master_id):
					masters_to_analyze = [m]
					break
			if not masters_to_analyze:
				return {"error": "Master not found: %s" % master_id}
		else:
			masters_to_analyze = list(font.masters)

		# If no glyphs specified, use all known overshoot glyphs present in font
		if not glyph_names:
			names_to_check = []
			for gname in _OVERSHOOT_GLYPHS:
				if font.glyphs[gname]:
					names_to_check.append(gname)
		else:
			names_to_check = glyph_names

		all_master_results = {}

		for master in masters_to_analyze:
			mid = str(master.id)
			mname = str(master.name)
			cap_h = float(master.capHeight)
			x_h = float(master.xHeight)
			desc = float(master.descender) if hasattr(master, "descender") else 0

			# Figure zone: use straight figure top as reference (figures may
			# be shorter than capHeight in hybrid/short lining designs).
			# Measure four, seven, one — take the minimum yMax because:
			#   - 'one' can have a flag with overshoot (Kristall)
			#   - 'four' can have a diagonal apex above the flat zone (Supreme)
			#   - 'seven' is the most reliable flat-top figure
			# The minimum of available measurements gives the true flat zone.
			fig_candidates = []
			for fig_ref_name in ("four", "seven", "one"):
				fig_ref = font.glyphs[fig_ref_name]
				if fig_ref:
					fig_ref_layer = fig_ref.layers[master.id]
					if len(fig_ref_layer.paths) > 0 or len(fig_ref_layer.components) > 0:
						fig_ref_bounds = fig_ref_layer.bounds
						if fig_ref_bounds and fig_ref_bounds.size.height > 0:
							fig_candidates.append(float(fig_ref_bounds.origin.y + fig_ref_bounds.size.height))
			fig_zone_top = min(fig_candidates) if fig_candidates else cap_h

			evaluations = []

			for gname in names_to_check:
				glyph = font.glyphs[gname]
				if glyph is None:
					continue

				layer = glyph.layers[master.id]
				if len(layer.paths) == 0 and len(layer.components) == 0:
					continue

				# Get actual bounds — use decomposed layer for components,
				# but fall back to raw layer.bounds (removeOverlap can
				# clear paths on copies in some GlyphsApp versions)
				if len(layer.components) > 0:
					clean = layer.copyDecomposedLayer()
					bounds = clean.bounds
				else:
					bounds = layer.bounds
				if bounds is None or bounds.size.width == 0:
					continue

				y_min = float(bounds.origin.y)
				y_max = float(bounds.origin.y + bounds.size.height)

				# Determine zone based on glyph classification
				gclass = _classify_glyph(glyph)
				base = gname.split(".")[0]
				otype = _OVERSHOOT_GLYPHS.get(base, "round")

				if gclass == "figure":
					zone_top = fig_zone_top
					zone_bottom = 0.0
					zone_height = fig_zone_top
				elif gclass == "uppercase":
					zone_top = cap_h
					zone_bottom = 0.0
					zone_height = cap_h
				elif gclass == "lowercase":
					zone_top = x_h
					zone_bottom = 0.0
					zone_height = x_h
				else:
					zone_top = cap_h
					zone_bottom = 0.0
					zone_height = cap_h

				# Calculate overshoots
				top_overshoot = y_max - zone_top  # positive = overshoots above
				bottom_overshoot = zone_bottom - y_min  # positive = overshoots below

				top_pct = (top_overshoot / zone_height * 100) if zone_height > 0 else 0
				bottom_pct = (bottom_overshoot / zone_height * 100) if zone_height > 0 else 0

				# Evaluate
				entry = {
					"glyph": gname,
					"type": otype,
					"zoneTop": round(zone_top, 1),
					"zoneBottom": round(zone_bottom, 1),
					"yMax": round(y_max, 1),
					"yMin": round(y_min, 1),
					"topOvershoot": round(top_overshoot, 1),
					"bottomOvershoot": round(bottom_overshoot, 1),
					"topPct": round(top_pct, 2),
					"bottomPct": round(bottom_pct, 2),
				}

				# Verdict logic
				issues = []
				# LC uses xHeight which is shorter → same absolute overshoot
				# produces higher %. UC: 1.6-2.3%, LC: 2.5-3.2% is normal.
				max_pct = 4.0 if gclass == "lowercase" else 3.0

				if otype in ("round", "round_bottom"):
					# Bottom should overshoot (all round/round_bottom forms)
					if bottom_overshoot < 0.5:
						issues.append("no bottom overshoot")
					elif bottom_pct > max_pct:
						issues.append("excessive bottom overshoot (%.1f%%)" % bottom_pct)

					# Top should overshoot for full round forms
					if otype == "round":
						if top_overshoot < 0.5:
							issues.append("no top overshoot")
						elif top_pct > max_pct:
							issues.append("excessive top overshoot (%.1f%%)" % top_pct)

				elif otype == "pointed":
					# Detect if apex is actually pointed vs flat/truncated
					if base in ("A", "M", "N"):
						pointed, xspan, ncnt = _is_pointed_apex(layer, at_top=True)
						entry["apexPointed"] = pointed
						entry["apexSpan"] = round(xspan, 1)
						if pointed and top_overshoot < 0.5:
							issues.append("pointed apex has no top overshoot")
						elif not pointed and top_overshoot < 0.5:
							pass  # flat apex, no overshoot needed
					if base in ("V", "W", "v", "w", "y"):
						pointed, xspan, ncnt = _is_pointed_apex(layer, at_top=False)
						entry["vertexPointed"] = pointed
						entry["vertexSpan"] = round(xspan, 1)
						if pointed and bottom_overshoot < 0.5:
							issues.append("pointed vertex has no bottom overshoot")
						elif not pointed and bottom_overshoot < 0.5:
							pass  # flat vertex, no overshoot needed

				if issues:
					entry["verdict"] = "inconsistent"
					entry["color"] = 0
					entry["note"] = "; ".join(issues)
				elif entry.get("note"):
					entry["verdict"] = "compensation"
					entry["color"] = 3
				else:
					entry["verdict"] = "pass"
					entry["color"] = 4

				evaluations.append(entry)

			# Compute round vs pointed overshoot comparison
			round_tops = [e["topOvershoot"] for e in evaluations
				if e.get("type") == "round" and e["topOvershoot"] > 0]
			pointed_tops = [e["topOvershoot"] for e in evaluations
				if e.get("type") == "pointed" and e["topOvershoot"] > 0]
			round_bottoms = [e["bottomOvershoot"] for e in evaluations
				if e.get("type") in ("round", "round_bottom") and e["bottomOvershoot"] > 0]
			pointed_bottoms = [e["bottomOvershoot"] for e in evaluations
				if e.get("type") == "pointed" and e["bottomOvershoot"] > 0]

			avg_round_top = sum(round_tops) / len(round_tops) if round_tops else 0
			avg_pointed_top = sum(pointed_tops) / len(pointed_tops) if pointed_tops else 0
			avg_round_bottom = sum(round_bottoms) / len(round_bottoms) if round_bottoms else 0

			stats = {
				"avgRoundTopOvershoot": round(avg_round_top, 1),
				"avgRoundBottomOvershoot": round(avg_round_bottom, 1),
				"avgPointedTopOvershoot": round(avg_pointed_top, 1),
				"roundTopPct": round(avg_round_top / cap_h * 100, 2) if cap_h else 0,
				"roundBottomPct": round(avg_round_bottom / cap_h * 100, 2) if cap_h else 0,
				"pointedVsRound": "pointed > round" if avg_pointed_top > avg_round_top else
					"WARNING: pointed <= round" if avg_pointed_top > 0 and avg_round_top > 0 else "N/A",
			}

			all_master_results[mname] = {
				"masterId": mid,
				"zoneInfo": {
					"capHeight": round(cap_h, 1),
					"xHeight": round(x_h, 1),
					"figureTop": round(fig_zone_top, 1),
				},
				"glyphs": evaluations,
				"statistics": stats,
			}

		# Mark glyphs with worst color across masters
		worst_color = {}
		for mname in all_master_results:
			for ev in all_master_results[mname]["glyphs"]:
				gname = ev["glyph"]
				c = ev.get("color", 4)
				if gname not in worst_color or c < worst_color[gname]:
					worst_color[gname] = c

		for gname, color in worst_color.items():
			glyph = font.glyphs[gname]
			if glyph:
				glyph.color = color

		return {
			"masters": all_master_results,
			"mastersAnalyzed": len(masters_to_analyze),
			"industryGuidelines": {
				"roundOvershoot": "~1-2% of zone height",
				"pointedOvershoot": "should exceed round overshoot",
				"source": "Designing Type (Karen Cheng)",
			},
			"colorLegend": {
				"red (0)": "Missing or excessive overshoot",
				"lightGreen (4)": "Passed",
			},
		}

	result = bridge.execute_on_main(_check)
	return 200, result


# ── RMX Tools Helpers ─────────────────────────────────────────────────────────

def _get_rmx_class(name):
	"""Get an RMX filter class by name. Returns None if RMX Tools not installed."""
	from objc import lookUpClass
	try:
		return lookUpClass(name)
	except Exception:
		return None


def _require_rmx(name):
	"""Get RMX class or raise clear error."""
	cls = _get_rmx_class(name)
	if cls is None:
		raise RuntimeError(
			f"RMX Tools not installed or '{name}' not found. "
			f"Install RMX Tools from glyphsapp.com/buy"
		)
	return cls


# ── RMX Mock Field ─────────────────────────────────────────────────────────────
#
# RMX filters read parameter values from NSTextField / NSButton ivars set by
# their dialog UI (e.g. width1Field, weightField, slantField).  When running
# headlessly we inject mock objects that return our desired float values.
# process_() (the dialog code path) bypasses the broken has_multiple_weight_masters
# check that fails in runFilterWithLayer_options_error_.

_rmx_mock_storage = {}   # id(obj) → float value
_RMX_MOCK_FIELD_CLASS = None


def _get_rmx_mock_field_class():
	"""Lazily create a PyObjC NSTextField subclass for RMX parameter injection."""
	global _RMX_MOCK_FIELD_CLASS
	if _RMX_MOCK_FIELD_CLASS is not None:
		return _RMX_MOCK_FIELD_CLASS
	import objc
	try:
		_RMX_MOCK_FIELD_CLASS = objc.lookUpClass("GlyphsMCPMockField")
		return _RMX_MOCK_FIELD_CLASS
	except Exception:
		pass
	from AppKit import NSTextField
	storage = _rmx_mock_storage

	class GlyphsMCPMockField(NSTextField):
		def floatValue(self):
			return storage.get(id(self), 0.0)
		def doubleValue(self):
			return float(storage.get(id(self), 0.0))
		def intValue(self):
			return int(storage.get(id(self), 0))
		def stringValue(self):
			v = storage.get(id(self), 0)
			return str(int(v)) if float(v) == int(v) else str(v)
		def tag(self):
			return 0
		def state(self):
			return int(storage.get(id(self), 0))

	_RMX_MOCK_FIELD_CLASS = GlyphsMCPMockField
	return _RMX_MOCK_FIELD_CLASS


def _make_rmx_field(value):
	"""Create a mock NSTextField that returns `value` for float/double/int/stringValue."""
	cls = _get_rmx_mock_field_class()
	f = cls.alloc().init()
	_rmx_mock_storage[id(f)] = float(value)
	return f


def _get_rmx_filter_instance(class_name):
	"""Return a registered Glyphs filter instance by ObjC class name, or None."""
	from GlyphsApp import Glyphs
	for f in Glyphs.filters:
		if type(f).__name__ == class_name:
			return f
	return None


def _rmx_process(filter_cls_name, font, glyph, master_id, params):
	"""
	Drive an RMX filter via its dialog code path (headless).

	Working approach (discovered via reverse-engineering RMX 1.15.53 AM):
	  1. Build RMXHybridGlyph for every master layer — no setupToolSpecific().
	  2. Wire _hybridGlyphs / _mastersCount / _currFont / _activeMaster on a
	     fresh filter instance.
	  3. Attach a proxy controller (redraw no-op, forwards unknown selectors
	     to the real GSWindowController).
	  4. Set each parameter via updateValue_forParameter_forMaster_() for all
	     masters — avoids the mock-NSTextField / KVC approach.
	  5. process_(None) → RMX computes the scaled result internally.
	  6. confirmDialog_(None) with disableUndoRegistration() first → writes
	     the result back to the GSGlyph master layers.

	params : dict { param_name → value }
	    RMXScaler:    "width"(%), "height"(%), "weight", "adjustSpace",
	                  "verticalShift"
	    RMXTuner:     "weight", "width", "height", "slant", "fixedWidth"
	    RMXMonospacer:"monoWidth" (abs), "keepStroke" (%), "useSpacing" (%)

	Returns True on success, raises on unexpected error.
	"""
	import objc
	from Foundation import NSMutableArray, NSNumber
	from AppKit import NSObject

	masters = list(font.masters)
	n = len(masters)

	active_idx = 0
	if master_id:
		for i, m in enumerate(masters):
			if m.id == master_id:
				active_idx = i
				break

	target_layer = glyph.layers[masters[active_idx].id]

	# ── 1. Build RMXHybridGlyph for every master ─────────────────────────────
	RMXHybridGlyph = objc.lookUpClass("RMXHybridGlyph")
	hybrids = NSMutableArray.alloc().init()
	for m in masters:
		layer = glyph.layers[m.id]
		h = RMXHybridGlyph.alloc().initWithGSLayer_(layer)
		h.create_RMXglyph()
		hybrids.addObject_(h)

	# ── 2. Create fresh filter instance and wire internal state ──────────────
	FilterCls = objc.lookUpClass(filter_cls_name)
	filt = FilterCls.alloc().init()
	filt.setValue_forKey_(hybrids, "_hybridGlyphs")
	filt.setValue_forKey_(NSNumber.numberWithInt_(n), "_mastersCount")
	filt.setValue_forKey_(font, "_currFont")
	filt.setActiveMaster_(active_idx)

	mc = filt.valueForKey_("_mastersCount")
	print(f"[GlyphsMCP] {filter_cls_name}: mastersCount={mc}")

	# ── 3. Proxy controller (provides redraw no-op) ───────────────────────────
	wc = font.parent.windowControllers()[0]

	try:
		ProxyCls = objc.lookUpClass("GlyphsMCPRMXProxy")
	except Exception:
		class GlyphsMCPRMXProxy(NSObject):
			def selectedLayers(self):
				return self._rmx_layers if hasattr(self, "_rmx_layers") else []
			def redraw(self):
				pass
			def forwardingTargetForSelector_(self, sel):
				return self._rmx_wc if hasattr(self, "_rmx_wc") else None
		ProxyCls = GlyphsMCPRMXProxy

	proxy = ProxyCls.alloc().init()
	proxy._rmx_wc = wc
	proxy._rmx_layers = [target_layer]
	filt.setController_(proxy)

	# ── 4. Set parameters for all masters ────────────────────────────────────
	for param_name, value in params.items():
		for i in range(n):
			filt.updateValue_forParameter_forMaster_(value, param_name, i)

	# ── 5. Snapshot layers before RMX (for undo) ────────────────────────────
	# confirmDialog_() requires disableUndoRegistration() beforehand (its
	# internal enableUndoRegistration() would underflow the counter and crash
	# otherwise).  This means the actual path changes are NOT recorded by
	# NSUndoManager.  To provide Cmd+Z support we snapshot → apply silently →
	# replay the change inside beginChanges/endChanges.
	masters_for_undo = list(font.masters)
	snapshots = {}
	for m in masters_for_undo:
		layer = glyph.layers[m.id]
		snapshots[m.id] = {
			"paths": [p.copy() for p in layer.paths],
			"width": float(layer.width),
		}

	# ── 6. Compute scaled result ─────────────────────────────────────────────
	filt.process_(None)

	# ── 7. Commit result to font layers (no undo) ───────────────────────────
	gum = glyph.undoManager()
	gum.disableUndoRegistration()
	try:
		filt.confirmDialog_(None)
	except Exception as e:
		err = str(e)
		if "UndoRegistration" not in err and "invalid state" not in err.lower():
			raise
		print(f"[GlyphsMCP] GSUndoManager note (expected in headless): {err[:200]}")

	# ── 8. Re-apply with undo registration ──────────────────────────────────
	# Save new (post-RMX) state, restore old, then re-apply new inside
	# beginChanges/endChanges so NSUndoManager records it.
	for m in masters_for_undo:
		layer = glyph.layers[m.id]
		new_paths = [p.copy() for p in layer.paths]
		new_width = float(layer.width)
		old = snapshots[m.id]

		# Restore original (still no undo)
		gum.disableUndoRegistration()
		for p in list(layer.paths):
			layer.removeShape_(p)
		for p in old["paths"]:
			layer.paths.append(p)
		layer.width = old["width"]
		gum.enableUndoRegistration()

		# Re-apply RMX result with undo
		layer.beginChanges()
		for p in list(layer.paths):
			layer.removeShape_(p)
		for p in new_paths:
			layer.paths.append(p)
		layer.width = new_width
		layer.endChanges()

	return True


# ── POST /api/font/proportions/compare ────────────────────────────────────────

# Related-form groups: members should have similar widths within tolerance (% of ref width)
_WIDTH_GROUPS = {
	"lc_bdpq": {"members": ["b", "d", "p", "q"], "tolerance": 2.0, "note": "mirrored bowl+stem forms"},
	"lc_hn":   {"members": ["h", "n"], "tolerance": 1.0, "note": "arch forms (must match)"},
	"uc_OQ":   {"members": ["O", "Q"], "tolerance": 2.0, "note": "Q based on O"},
	"uc_HU":   {"members": ["H", "U"], "tolerance": 10.0, "note": "wide straight forms"},
}

# Width ordering constraints: (wider, narrower, note)
_WIDTH_ORDER = [
	# LC — wider first
	("m", "n", "m must be wider than n"),
	("w", "n", "w must be wider than n"),
	("b", "n", "b must be wider or equal to n"),
	("n", "r", "r must be narrower than n"),
	("n", "i", "i must be narrower than n"),
	("n", "l", "l must be narrower than n"),
	("n", "f", "f must be narrower than n"),
	("n", "t", "t must be narrower than n"),
	# UC — wider first
	("M", "H", "M must be wider than H"),
	("W", "H", "W must be wider than H"),
	("H", "I", "I must be narrower than H"),
	("H", "J", "J must be narrower than H"),
	("H", "L", "L must be narrower than H"),
	("H", "E", "E must be narrower than H"),
	("H", "F", "F must be narrower than H"),
]

# Industry ranges from 18 Lineto fonts (all weights) — [min, max] as % of ref
_WIDTH_RANGES = {
	# LC / n
	"a": [83, 115], "b": [100, 115], "c": [66, 100], "d": [100, 115],
	"e": [89, 102], "f": [53, 82], "g": [95, 115], "h": [99, 101],
	"i": [39, 52], "j": [39, 60], "k": [80, 110], "l": [39, 54],
	"m": [139, 160], "n": [100, 100], "o": [94, 111], "p": [100, 115],
	"q": [100, 115], "r": [56, 74], "s": [71, 89], "t": [49, 83],
	"u": [90, 101], "v": [82, 101], "w": [126, 155], "x": [79, 110],
	"y": [83, 107], "z": [76, 94],
	# UC / H
	"A": [89, 109], "B": [75, 98], "C": [67, 106], "D": [90, 102],
	"E": [72, 88], "F": [61, 87], "G": [91, 117], "H": [100, 100],
	"I": [34, 51], "J": [51, 77], "K": [80, 99], "L": [53, 78],
	"M": [117, 143], "N": [101, 122], "O": [98, 123], "P": [68, 94],
	"Q": [99, 123], "R": [72, 99], "S": [69, 89], "T": [67, 94],
	"U": [90, 101], "V": [81, 108], "W": [125, 155], "X": [70, 114],
	"Y": [78, 101], "Z": [73, 97],
	# Figures / H
	"zero": [82, 94], "one": [38, 67], "two": [73, 84], "three": [69, 85],
	"four": [72, 100], "five": [72, 86], "six": [78, 93], "seven": [66, 88],
	"eight": [75, 91], "nine": [78, 93],
}


@route("POST", "/api/font/proportions/compare")
def handle_compare_proportions(bridge, body=None, **kwargs):
	"""Compare width proportions across glyphs within a font.

	Checks:
	1. Related-form groups (b≈d≈p≈q, O≈Q, etc.) — internal consistency
	2. Width ordering (m>n>r, W>H>I, etc.)
	3. Industry ranges from professional fonts

	Returns per-glyph proportions, group verdicts, ordering violations.
	Auto-marks glyphs in GlyphsApp: red=inconsistent, yellow=outside range, green=pass.
	"""
	from GlyphsApp import Glyphs

	if not body:
		body = {}

	glyph_names = body.get("glyphNames", None)
	master_id = body.get("masterId", "")

	def _run():
		font = Glyphs.font
		if not font:
			return {"error": "No font open"}

		# Determine which masters to analyze
		if master_id:
			masters_to_check = [m for m in font.masters if m.id == master_id]
			if not masters_to_check:
				return {"error": f"Master '{master_id}' not found"}
		else:
			masters_to_check = list(font.masters)

		# Default glyph list: all LC + UC + figures
		if not glyph_names:
			default_names = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
			default_names += ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
			check_names = [n for n in default_names if font.glyphs[n] is not None]
		else:
			check_names = [n for n in glyph_names if font.glyphs[n] is not None]

		all_masters_results = {}
		worst_colors = {}  # glyph_name -> worst color across masters

		for master in masters_to_check:
			mid = master.id
			mname = master.name

			# Get reference widths
			n_glyph = font.glyphs["n"]
			H_glyph = font.glyphs["H"]
			n_w = n_glyph.layers[mid].width if n_glyph else 0
			H_w = H_glyph.layers[mid].width if H_glyph else 0

			if n_w == 0 and H_w == 0:
				all_masters_results[mid] = {"masterName": mname, "error": "No reference glyphs (n, H) found"}
				continue

			# Measure all glyph widths and compute ratios
			proportions = {}
			for gname in check_names:
				glyph = font.glyphs[gname]
				if not glyph:
					continue
				layer = glyph.layers[mid]
				w = layer.width

				# Determine reference
				gclass = _classify_glyph(glyph)
				if gclass == "uppercase" or gclass == "figure":
					ref = H_w
					ref_name = "H"
				else:
					ref = n_w
					ref_name = "n"

				if ref == 0:
					continue

				ratio = round(w / ref * 100, 1)
				proportions[gname] = {
					"width": round(w),
					"ratio": ratio,
					"ref": ref_name,
					"refWidth": round(ref),
				}

			# Check related-form groups
			group_results = []
			for gid, ginfo in _WIDTH_GROUPS.items():
				members = [m for m in ginfo["members"] if m in proportions]
				if len(members) < 2:
					continue

				ratios = {m: proportions[m]["ratio"] for m in members}
				widths = {m: proportions[m]["width"] for m in members}
				max_r = max(ratios.values())
				min_r = min(ratios.values())
				spread = round(max_r - min_r, 1)

				verdict = "pass" if spread <= ginfo["tolerance"] else "inconsistent"
				group_results.append({
					"group": gid,
					"note": ginfo["note"],
					"members": ratios,
					"widths": widths,
					"spread": spread,
					"tolerance": ginfo["tolerance"],
					"verdict": verdict,
				})

				# Mark inconsistent members
				if verdict == "inconsistent":
					# Find the median ratio and flag outliers
					sorted_ratios = sorted(ratios.items(), key=lambda x: x[1])
					median_r = sorted_ratios[len(sorted_ratios) // 2][1]
					for m, r in ratios.items():
						if abs(r - median_r) > ginfo["tolerance"]:
							color = 0  # red
							old = worst_colors.get(m, 4)
							if color < old:
								worst_colors[m] = color

			# Check width ordering
			order_results = []
			for wider, narrower, note in _WIDTH_ORDER:
				if wider not in proportions or narrower not in proportions:
					continue
				w_w = proportions[wider]["width"]
				n_w_val = proportions[narrower]["width"]
				ok = w_w >= n_w_val
				if not ok:
					order_results.append({
						"wider": wider,
						"narrower": narrower,
						"widerWidth": w_w,
						"narrowerWidth": n_w_val,
						"note": note,
						"verdict": "violation",
					})
					# Mark both glyphs
					for g in [wider, narrower]:
						old = worst_colors.get(g, 4)
						if 0 < old:
							worst_colors[g] = 0

			# Check industry ranges
			range_results = []
			for gname, prop in proportions.items():
				ratio = prop["ratio"]
				if gname in _WIDTH_RANGES:
					lo, hi = _WIDTH_RANGES[gname]
					if ratio < lo or ratio > hi:
						range_results.append({
							"glyph": gname,
							"ratio": ratio,
							"range": [lo, hi],
							"verdict": "outside_range",
						})
						old = worst_colors.get(gname, 4)
						if 3 < old:  # yellow
							worst_colors[gname] = 3
					else:
						# Pass — mark green if no worse verdict
						if gname not in worst_colors:
							worst_colors[gname] = 4

			# Cross-case ratios
			cross = {}
			if n_w and H_w:
				cross["n/H"] = round(n_w / H_w * 100, 1)
			o_glyph = font.glyphs["o"]
			O_glyph = font.glyphs["O"]
			if o_glyph and O_glyph:
				o_w = o_glyph.layers[mid].width
				O_w = O_glyph.layers[mid].width
				if O_w:
					cross["o/O"] = round(o_w / O_w * 100, 1)

			# Summary counts
			group_issues = sum(1 for g in group_results if g["verdict"] == "inconsistent")
			order_issues = len(order_results)
			range_issues = len(range_results)

			all_masters_results[mid] = {
				"masterName": mname,
				"references": {"n": round(n_w), "H": round(H_w)},
				"crossCase": cross,
				"proportions": proportions,
				"groups": group_results,
				"orderViolations": order_results,
				"rangeOutliers": range_results,
				"summary": {
					"glyphsChecked": len(proportions),
					"groupIssues": group_issues,
					"orderViolations": order_issues,
					"rangeOutliers": range_issues,
				},
			}

		# Apply colors in GlyphsApp
		for gname in check_names:
			glyph = font.glyphs[gname]
			if glyph and gname in worst_colors:
				glyph.color = worst_colors[gname]

		if len(masters_to_check) == 1:
			mid = masters_to_check[0].id
			return {"ok": True, **all_masters_results[mid]}
		else:
			return {"ok": True, "masters": all_masters_results}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


# ── POST /api/font/diagonals/check ────────────────────────────────────────────

# Diagonal glyph groups — members should have consistent diagonal stroke weight
_DIAG_GROUPS = {
	"lc_vwy":  {"members": ["v", "w", "y"], "tolerance": 18.0, "note": "LC open diagonals"},
	"uc_VAW":  {"members": ["V", "A", "W"], "tolerance": 10.0, "note": "UC primary diagonals"},
	"uc_XYZ":  {"members": ["X", "Y", "Z"], "tolerance": 12.0, "note": "UC secondary diagonals"},
	"uc_MN":   {"members": ["M", "N"], "tolerance": 10.0, "note": "UC diagonal verticals"},
}

# Diagonal / straight stem ratio ranges from 18 Lineto fonts (Light/Regular weights)
# Diagonals can be thinner (optical) OR thicker (perpendicular compensation) than straight
_DIAG_RATIO_RANGE = {
	# LC diag / n stem
	"v": [85, 101], "w": [82, 101], "y": [83, 101],
	"k": [50, 100],
	# UC diag / H stem — often >100% due to perpendicular measurement of angled strokes
	"V": [87, 110], "W": [87, 108], "Y": [85, 108],
	"A": [90, 112], "K": [87, 115], "M": [87, 111], "N": [84, 113],
}

# Glyphs where diagonal measurement is unreliable (crossing strokes, mostly-horizontal)
_DIAG_UNRELIABLE = {"x", "X", "z", "Z"}

# Minimum absolute spread (units) below which group is always "pass"
# At thin weights (20u stems), 2u rounding = 10% but is meaningless
_DIAG_MIN_SPREAD = 4

# Minimum reference stem (units) for ratio checks to be meaningful
# Below this, ±1u rounding = >5% ratio noise
_DIAG_MIN_REF = 30

_DIAG_LC = ["v", "w", "x", "y", "z", "k"]
_DIAG_UC = ["V", "W", "X", "Y", "Z", "A", "K", "M", "N"]


@route("POST", "/api/font/diagonals/check")
def handle_check_diagonals(bridge, body=None, **kwargs):
	"""Check diagonal stroke weight consistency and ratio to vertical stems.

	Uses perpendicular ray-casting to measure actual stroke thickness
	of diagonal glyphs, then:
	1. Compares related diagonals (V≈A≈W, v≈w≈y, etc.)
	2. Reports diagonal/straight ratio for each glyph
	3. Flags ratios outside professional font range

	Auto-marks glyphs: red=group inconsistency, yellow=ratio outside range, green=pass.
	"""
	from GlyphsApp import Glyphs

	if not body:
		body = {}

	glyph_names = body.get("glyphNames", None)
	master_id = body.get("masterId", "")

	def _run():
		font = Glyphs.font
		if not font:
			return {"error": "No font open"}

		if master_id:
			masters_to_check = [m for m in font.masters if m.id == master_id]
			if not masters_to_check:
				return {"error": f"Master '{master_id}' not found"}
		else:
			masters_to_check = list(font.masters)

		if not glyph_names:
			check_names = [g for g in _DIAG_LC + _DIAG_UC if font.glyphs[g] is not None]
		else:
			check_names = [g for g in glyph_names if font.glyphs[g] is not None]

		all_masters_results = {}
		worst_colors = {}

		for master in masters_to_check:
			mid = master.id

			# Measure reference stems
			n_glyph = font.glyphs["n"]
			H_glyph = font.glyphs["H"]
			n_ref = None
			H_ref = None

			if n_glyph:
				n_data = _auto_measure_glyph(n_glyph.layers[mid], strategy="frequency")
				n_ref = n_data["verticalStems"]["dominant"]
			if H_glyph:
				H_data = _auto_measure_glyph(H_glyph.layers[mid], strategy="frequency")
				H_ref = H_data["verticalStems"]["dominant"]

			if not n_ref and not H_ref:
				all_masters_results[mid] = {"masterName": master.name, "error": "No reference stems"}
				continue

			# Measure each diagonal glyph
			diag_results = {}
			for gname in check_names:
				glyph = font.glyphs[gname]
				if not glyph:
					continue

				layer = glyph.layers[mid]
				data = _auto_measure_glyph(layer, strategy="frequency")

				# For diagonals, the dominant vertical stem IS the diagonal thickness
				# (perpendicular measurement captures this correctly)
				v_dom = data["verticalStems"]["dominant"]
				h_dom = data["horizontalStems"]["dominant"]

				# Use whichever is available — diagonals may register as vertical or horizontal
				# depending on angle. Pick the one that exists, prefer vertical.
				stem = v_dom or h_dom
				if not stem:
					diag_results[gname] = {"stem": None, "error": "no measurement"}
					continue

				# Determine reference
				gclass = _classify_glyph(glyph)
				if gclass == "uppercase":
					ref = H_ref
					ref_name = "H"
				else:
					ref = n_ref
					ref_name = "n"

				if not ref:
					diag_results[gname] = {"stem": stem, "ratio": None, "ref": ref_name}
					continue

				ratio = round(stem / ref * 100, 1)
				result = {
					"stem": stem,
					"ref": ref_name,
					"refStem": ref,
					"ratio": ratio,
				}

				# Check ratio range
				if gname in _DIAG_UNRELIABLE:
					result["verdict"] = "unreliable"
					result["note"] = "crossing strokes — measurement unreliable"
					if gname not in worst_colors:
						worst_colors[gname] = 1  # orange
				elif ref < _DIAG_MIN_REF:
					result["verdict"] = "pass"
					result["note"] = "ref too thin for ratio check"
					if gname not in worst_colors:
						worst_colors[gname] = 4
				elif gname in _DIAG_RATIO_RANGE:
					lo, hi = _DIAG_RATIO_RANGE[gname]
					if ratio < lo or ratio > hi:
						result["verdict"] = "outside_range"
						result["range"] = [lo, hi]
						old = worst_colors.get(gname, 4)
						if 3 < old:
							worst_colors[gname] = 3
					else:
						result["verdict"] = "pass"
						if gname not in worst_colors:
							worst_colors[gname] = 4
				else:
					result["verdict"] = "no_pattern"

				diag_results[gname] = result

			# Check diagonal groups
			group_results = []
			for gid, ginfo in _DIAG_GROUPS.items():
				members = [m for m in ginfo["members"] if m in diag_results and diag_results[m].get("stem") and m not in _DIAG_UNRELIABLE]
				if len(members) < 2:
					continue

				stems = {m: diag_results[m]["stem"] for m in members}
				max_s = max(stems.values())
				min_s = min(stems.values())
				abs_spread = max_s - min_s

				# Express spread as % of average
				avg = sum(stems.values()) / len(stems)
				spread_pct = round(abs_spread / avg * 100, 1) if avg else 0

				# Pass if absolute spread below minimum threshold (rounding noise at thin weights)
				if abs_spread <= _DIAG_MIN_SPREAD:
					verdict = "pass"
				else:
					verdict = "pass" if spread_pct <= ginfo["tolerance"] else "inconsistent"
				group_results.append({
					"group": gid,
					"note": ginfo["note"],
					"members": stems,
					"spreadPct": spread_pct,
					"tolerance": ginfo["tolerance"],
					"verdict": verdict,
				})

				if verdict == "inconsistent":
					for m in members:
						old = worst_colors.get(m, 4)
						if 0 < old:
							worst_colors[m] = 0

			# Summary
			ratio_issues = sum(1 for r in diag_results.values() if r.get("verdict") == "outside_range")
			group_issues = sum(1 for g in group_results if g["verdict"] == "inconsistent")

			all_masters_results[mid] = {
				"masterName": master.name,
				"references": {"n": n_ref, "H": H_ref},
				"diagonals": diag_results,
				"groups": group_results,
				"summary": {
					"glyphsChecked": len(diag_results),
					"ratioIssues": ratio_issues,
					"groupIssues": group_issues,
				},
			}

		# Apply colors
		for gname in check_names:
			glyph = font.glyphs[gname]
			if glyph and gname in worst_colors:
				glyph.color = worst_colors[gname]

		if len(masters_to_check) == 1:
			mid = masters_to_check[0].id
			return {"ok": True, **all_masters_results[mid]}
		else:
			return {"ok": True, "masters": all_masters_results}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


# ── POST /api/font/junctions/check ────────────────────────────────────────────

# Junction measurement config: glyph -> (x_pct, zone for junction sweep)
# x_pct: horizontal position of stem to track (% of width)
_JUNCTION_GLYPHS = {
	"n": 0.15, "h": 0.15, "m": 0.10, "u": 0.15,
	"a": 0.85, "b": 0.15, "d": 0.85, "p": 0.15, "q": 0.85,
}

# Consistency groups — only arch group is reliable for automated checking.
# Bowl groups (b/d, p/q) report values but don't auto-flag — horizontal ray
# measurement at right-stem forms (d, q) is unreliable at light weights.
_JUNCTION_GROUPS = {
	"arch": {"members": ["n", "m"], "tolerance": 5.0, "note": "arch junction thinning"},
}


def _measure_junction_thinning(layer, x_pct, zone_top, steps=30):
	"""Sweep horizontal rays to find stem thinning at junction.

	Returns dict with midStem, junctionMin, thinning% or None.
	"""
	from Foundation import NSPoint

	clean = layer.copyDecomposedLayer()
	clean.removeOverlap()

	w = layer.width
	x_target = w * x_pct
	step_size = zone_top / steps

	profile = []
	for i in range(steps + 1):
		y = i * step_size
		p1 = NSPoint(-10, y)
		p2 = NSPoint(w + 10, y)
		pts = clean.intersectionsBetweenPoints(p1, p2)
		if not pts or len(pts) < 2:
			continue

		xs = sorted(set(round(p.x, 1) for p in pts))
		xs = [x for x in xs if -5 <= x <= w + 5]

		# Find stem segment closest to x_target
		best_stem = None
		best_dist = 999999
		for j in range(0, len(xs) - 1, 2):
			seg_left = xs[j]
			seg_right = xs[j + 1]
			seg_mid = (seg_left + seg_right) / 2
			seg_w = seg_right - seg_left
			if seg_w < 3:
				continue
			dist = abs(seg_mid - x_target)
			if dist < best_dist:
				best_dist = dist
				best_stem = seg_w

		if best_stem:
			profile.append((round(y), round(best_stem, 1)))

	if len(profile) < 5:
		return None

	# Mid-stem: average in 20-60% zone
	mid_zone = [(y, sw) for y, sw in profile if zone_top * 0.2 <= y <= zone_top * 0.6]
	if not mid_zone:
		return None
	mid_stem = sum(sw for _, sw in mid_zone) / len(mid_zone)

	# Junction: minimum in 65-95% zone
	upper_zone = [(y, sw) for y, sw in profile if zone_top * 0.65 <= y <= zone_top * 0.95]
	if not upper_zone:
		return None
	min_at_jct = min(sw for _, sw in upper_zone)
	min_y = [y for y, sw in upper_zone if sw == min_at_jct][0]

	if mid_stem < 5:
		return None

	return {
		"midStem": round(mid_stem, 1),
		"junctionMin": round(min_at_jct, 1),
		"junctionY": min_y,
		"thinning": round(min_at_jct / mid_stem * 100, 1),
	}


@route("POST", "/api/font/junctions/check")
def handle_check_junctions(bridge, body=None, **kwargs):
	"""Check junction thinning consistency across related glyphs.

	Measures how much stems thin at arch/bowl junctions by sweeping
	horizontal rays at multiple heights. Reports thinning % (100% = no
	thinning, 80% = stem thins to 80% at junction).

	Checks consistency within groups:
	- n ≈ m (arch junction)
	- b ≈ p and d ≈ q (bowl junction)

	Does NOT flag based on absolute thinning values — these are highly
	design-specific. Only flags inconsistencies within related forms.

	Auto-marks glyphs: red=group inconsistency, green=pass.
	"""
	from GlyphsApp import Glyphs

	if not body:
		body = {}

	glyph_names = body.get("glyphNames", None)
	master_id = body.get("masterId", "")

	def _run():
		font = Glyphs.font
		if not font:
			return {"error": "No font open"}

		if master_id:
			masters_to_check = [m for m in font.masters if m.id == master_id]
			if not masters_to_check:
				return {"error": f"Master '{master_id}' not found"}
		else:
			masters_to_check = list(font.masters)

		if not glyph_names:
			check_names = [g for g in _JUNCTION_GLYPHS if font.glyphs[g] is not None]
		else:
			check_names = [g for g in glyph_names if font.glyphs[g] is not None and g in _JUNCTION_GLYPHS]

		all_masters_results = {}
		worst_colors = {}

		for master in masters_to_check:
			mid = master.id
			xH = master.xHeight

			jct_results = {}
			for gname in check_names:
				glyph = font.glyphs[gname]
				if not glyph:
					continue
				layer = glyph.layers[mid]
				x_pct = _JUNCTION_GLYPHS[gname]
				data = _measure_junction_thinning(layer, x_pct, xH)
				if data:
					jct_results[gname] = data
				else:
					jct_results[gname] = {"error": "no measurement"}

			# Check groups
			group_results = []
			for gid, ginfo in _JUNCTION_GROUPS.items():
				members = [m for m in ginfo["members"] if m in jct_results and "thinning" in jct_results[m]]
				if len(members) < 2:
					continue

				thinnings = {m: jct_results[m]["thinning"] for m in members}
				spread = round(max(thinnings.values()) - min(thinnings.values()), 1)

				verdict = "pass" if spread <= ginfo["tolerance"] else "inconsistent"
				group_results.append({
					"group": gid,
					"note": ginfo["note"],
					"members": thinnings,
					"spread": spread,
					"tolerance": ginfo["tolerance"],
					"verdict": verdict,
				})

				if verdict == "inconsistent":
					for m in members:
						old = worst_colors.get(m, 4)
						if 0 < old:
							worst_colors[m] = 0
				else:
					for m in members:
						if m not in worst_colors:
							worst_colors[m] = 4

			# Mark non-grouped glyphs green
			for gname in check_names:
				if gname not in worst_colors and gname in jct_results and "thinning" in jct_results[gname]:
					worst_colors[gname] = 4

			group_issues = sum(1 for g in group_results if g["verdict"] == "inconsistent")

			all_masters_results[mid] = {
				"masterName": master.name,
				"junctions": jct_results,
				"groups": group_results,
				"summary": {
					"glyphsChecked": len(jct_results),
					"groupIssues": group_issues,
				},
			}

		# Apply colors
		for gname in check_names:
			glyph = font.glyphs[gname]
			if glyph and gname in worst_colors:
				glyph.color = worst_colors[gname]

		if len(masters_to_check) == 1:
			mid = masters_to_check[0].id
			return {"ok": True, **all_masters_results[mid]}
		else:
			return {"ok": True, "masters": all_masters_results}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


# ── Related forms: cross-validation between figures and letters ───────────────
# Derived from Karen Cheng "Designing Type" + measurements across 18 Lineto fonts
# (6 families x 3 weights: Circular, Supreme, Riforma, Moderne, Medium, Kristall)

# Each pair: (glyph_a, glyph_b) -> range of width_a / width_b * 100
# severity: "high" = likely error, "medium" = worth checking, "low" = informational
_RELATED_FORM_PAIRS = [
	# Near-identical rotated forms — tightest check
	{"a": "six", "b": "nine", "range": [97, 104], "severity": "high",
	 "note": "rotated forms — should match width"},
	# Figure narrower than letter
	{"a": "zero", "b": "O", "range": [65, 93], "severity": "medium",
	 "note": "zero narrower and lighter than O"},
	# Related open-bowl figures
	{"a": "three", "b": "five", "range": [92, 106], "severity": "medium",
	 "note": "related open-bowl figures"},
	# Double-bowl relationships
	{"a": "three", "b": "B", "range": [78, 99], "severity": "medium",
	 "note": "three narrower than B (double bowls)"},
	# S-shape relationship
	{"a": "eight", "b": "S", "range": [92, 119], "severity": "low",
	 "note": "8 related to S-shape"},
	# One always wider than I (flag/crossbar)
	{"a": "one", "b": "I", "range": [106, 185], "severity": "low",
	 "note": "one wider than I (flag and crossbar add width)"},
]


@route("POST", "/api/font/related-forms/check")
def handle_check_related_forms(bridge, body=None, **kwargs):
	"""Check consistency between related figures and letters (0↔O, 6↔9, 8↔S, etc.)."""
	body = body or {}
	master_id = body.get("masterId", "")

	def _run():
		from GlyphsApp import Glyphs
		font = _require_font()

		masters_to_check = [m for m in font.masters if m.id == master_id] if master_id else list(font.masters)
		if not masters_to_check:
			return {"error": f"Master '{master_id}' not found"}

		all_masters_results = {}
		worst_colors = {}  # glyph_name -> worst color across masters

		for master in masters_to_check:
			mid = master.id
			pair_results = []

			for pair in _RELATED_FORM_PAIRS:
				ga_name, gb_name = pair["a"], pair["b"]
				gl_a = font.glyphs[ga_name]
				gl_b = font.glyphs[gb_name]
				if not gl_a or not gl_b:
					continue

				w_a = gl_a.layers[mid].width
				w_b = gl_b.layers[mid].width
				if w_b == 0:
					continue

				ratio = w_a / w_b * 100
				lo, hi = pair["range"]
				severity = pair["severity"]

				if lo <= ratio <= hi:
					verdict = "pass"
					color = 4  # green
				else:
					if severity == "high":
						verdict = "inconsistent"
						color = 0  # red
					elif severity == "medium":
						verdict = "warning"
						color = 3  # yellow
					else:
						verdict = "info"
						color = -1  # don't mark

				pair_results.append({
					"pair": f"{ga_name}/{gb_name}",
					"widthA": round(w_a, 1),
					"widthB": round(w_b, 1),
					"ratio": round(ratio, 1),
					"expected": pair["range"],
					"verdict": verdict,
					"severity": severity,
					"note": pair["note"],
				})

				# Track worst color per glyph
				if color >= 0:
					for gn in (ga_name, gb_name):
						if gn not in worst_colors or color < worst_colors[gn]:
							worst_colors[gn] = color

			# Summary counts
			verdicts = [p["verdict"] for p in pair_results]
			summary = {
				"pass": verdicts.count("pass"),
				"inconsistent": verdicts.count("inconsistent"),
				"warning": verdicts.count("warning"),
				"info": verdicts.count("info"),
			}

			all_masters_results[mid] = {
				"masterName": master.name,
				"pairs": pair_results,
				"summary": summary,
			}

		# Mark glyphs in GlyphsApp
		for gname, color in worst_colors.items():
			glyph = font.glyphs[gname]
			if glyph:
				glyph.color = color

		if len(masters_to_check) == 1:
			mid = masters_to_check[0].id
			return {"ok": True, **all_masters_results[mid]}
		else:
			return {"ok": True, "masters": all_masters_results}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


# ── Punctuation consistency ──────────────────────────────────────────────────
# Derived from Karen Cheng "Designing Type" ch.7 + measurements across Lineto fonts

# Width-match checks: pairs that should have identical or near-identical widths
# "tolerance" is max allowed % deviation from 100%
_PUNCT_WIDTH_MATCH = [
	# Mirrored pairs — must be identical
	{"a": "parenleft", "b": "parenright", "tolerance": 0.5, "severity": "high",
	 "note": "mirrored pair — width must match"},
	{"a": "bracketleft", "b": "bracketright", "tolerance": 0.5, "severity": "high",
	 "note": "mirrored pair — width must match"},
	{"a": "braceleft", "b": "braceright", "tolerance": 0.5, "severity": "high",
	 "note": "mirrored pair — width must match"},
	# Punctuation with shared structure
	{"a": "colon", "b": "semicolon", "tolerance": 15, "severity": "medium",
	 "note": "colon and semicolon — similar set width"},
	{"a": "period", "b": "comma", "tolerance": 15, "severity": "medium",
	 "note": "period and comma — similar set width"},
	{"a": "quotedblleft", "b": "quotedblright", "tolerance": 8, "severity": "medium",
	 "note": "double quotes — similar set width"},
	{"a": "quoteleft", "b": "quoteright", "tolerance": 8, "severity": "medium",
	 "note": "single quotes — similar set width"},
	{"a": "guillemotleft", "b": "guillemotright", "tolerance": 1, "severity": "high",
	 "note": "guillemets — mirrored, width must match"},
	{"a": "guilsinglleft", "b": "guilsinglright", "tolerance": 1, "severity": "high",
	 "note": "single guillemets — mirrored, width must match"},
]

# Width-ratio checks: expected ratio range of width_a / width_b * 100
_PUNCT_WIDTH_RATIO = [
	{"a": "endash", "b": "hyphen", "range": [140, 280], "severity": "low",
	 "note": "endash wider than hyphen (traditionally 2×)"},
	{"a": "emdash", "b": "endash", "range": [140, 230], "severity": "low",
	 "note": "emdash wider than endash (traditionally 2×)"},
	{"a": "quoteright", "b": "comma", "range": [70, 115], "severity": "low",
	 "note": "quoteright similar form to comma"},
	{"a": "exclam", "b": "question", "range": [40, 95], "severity": "low",
	 "note": "exclamation narrower than question mark"},
]


@route("POST", "/api/font/punctuation/check")
def handle_check_punctuation(bridge, body=None, **kwargs):
	"""Check punctuation consistency: mirrored pairs, width matches, and ratio checks."""
	body = body or {}
	master_id = body.get("masterId", "")

	def _run():
		from GlyphsApp import Glyphs
		font = _require_font()

		masters_to_check = [m for m in font.masters if m.id == master_id] if master_id else list(font.masters)
		if not masters_to_check:
			return {"error": f"Master '{master_id}' not found"}

		all_masters_results = {}
		worst_colors = {}

		for master in masters_to_check:
			mid = master.id
			check_results = []

			# Width-match checks
			for check in _PUNCT_WIDTH_MATCH:
				ga_name, gb_name = check["a"], check["b"]
				gl_a = font.glyphs[ga_name]
				gl_b = font.glyphs[gb_name]
				if not gl_a or not gl_b:
					continue

				w_a = gl_a.layers[mid].width
				w_b = gl_b.layers[mid].width
				if w_b == 0:
					continue

				ratio = w_a / w_b * 100
				deviation = abs(ratio - 100)
				tolerance = check["tolerance"]

				if deviation <= tolerance:
					verdict = "pass"
					color = 4
				else:
					if check["severity"] == "high":
						verdict = "inconsistent"
						color = 0
					else:
						verdict = "warning"
						color = 3

				check_results.append({
					"check": "width_match",
					"pair": f"{ga_name}/{gb_name}",
					"widthA": round(w_a, 1),
					"widthB": round(w_b, 1),
					"ratio": round(ratio, 1),
					"tolerance": tolerance,
					"verdict": verdict,
					"severity": check["severity"],
					"note": check["note"],
				})

				if color >= 0:
					for gn in (ga_name, gb_name):
						if gn not in worst_colors or color < worst_colors[gn]:
							worst_colors[gn] = color

			# Width-ratio checks
			for check in _PUNCT_WIDTH_RATIO:
				ga_name, gb_name = check["a"], check["b"]
				gl_a = font.glyphs[ga_name]
				gl_b = font.glyphs[gb_name]
				if not gl_a or not gl_b:
					continue

				w_a = gl_a.layers[mid].width
				w_b = gl_b.layers[mid].width
				if w_b == 0:
					continue

				ratio = w_a / w_b * 100
				lo, hi = check["range"]

				if lo <= ratio <= hi:
					verdict = "pass"
					color = 4
				else:
					verdict = "info"
					color = -1  # low severity = don't mark

				check_results.append({
					"check": "width_ratio",
					"pair": f"{ga_name}/{gb_name}",
					"widthA": round(w_a, 1),
					"widthB": round(w_b, 1),
					"ratio": round(ratio, 1),
					"expected": check["range"],
					"verdict": verdict,
					"severity": check["severity"],
					"note": check["note"],
				})

				if color >= 0:
					for gn in (ga_name, gb_name):
						if gn not in worst_colors or color < worst_colors[gn]:
							worst_colors[gn] = color

			verdicts = [c["verdict"] for c in check_results]
			summary = {
				"pass": verdicts.count("pass"),
				"inconsistent": verdicts.count("inconsistent"),
				"warning": verdicts.count("warning"),
				"info": verdicts.count("info"),
				"checkedPairs": len(check_results),
			}

			all_masters_results[mid] = {
				"masterName": master.name,
				"checks": check_results,
				"summary": summary,
			}

		# Mark glyphs
		for gname, color in worst_colors.items():
			glyph = font.glyphs[gname]
			if glyph:
				glyph.color = color

		if len(masters_to_check) == 1:
			mid = masters_to_check[0].id
			return {"ok": True, **all_masters_results[mid]}
		else:
			return {"ok": True, "masters": all_masters_results}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


def _get_layer(font, glyph_name, master_id=None):
	"""Get a specific layer from a glyph."""
	glyph = font.glyphs[glyph_name]
	if glyph is None:
		raise KeyError(f"Glyph '{glyph_name}' not found")
	if master_id:
		return glyph.layers[master_id]
	return glyph.layers[font.masters[0].id]


def _to_ns_array(val, n_masters):
	"""Convert a scalar or list to NSMutableArray of NSNumber for RMX filter setters.

	NSArray.arrayWithArray_(python_list) returns OC_BuiltinPythonArray (a bridged
	Python list), not a true NSArray. RMX ObjC setters call stringValue on the
	elements and crash. NSMutableArray.alloc().init() + addObject_() creates a
	genuine NSMutableArray that ObjC code can introspect correctly.
	"""
	from Foundation import NSMutableArray, NSNumber
	values = val if isinstance(val, list) else [val] * n_masters
	arr = NSMutableArray.alloc().init()
	for v in values:
		arr.addObject_(NSNumber.numberWithInt_(int(v)))
	return arr


# ── POST /api/filters/rmx/harmonize ──────────────────────────────────────────

@route("POST", "/api/filters/rmx/harmonize")
def handle_rmx_harmonize(bridge, body=None, **kwargs):
	"""Apply RMX Harmonizer to a glyph layer."""
	if not body or "glyphName" not in body:
		return 400, {"error": "Body must contain 'glyphName'"}

	glyph_name = body["glyphName"]
	mode = body.get("mode", "harmonize")
	master_id = body.get("masterId", None)

	valid_modes = ["extract handles", "dekink", "harmonize",
	               "supersmooth diagonals", "supersmooth all"]
	if mode not in valid_modes:
		return 400, {"error": f"Invalid mode '{mode}'. Must be one of: {valid_modes}"}

	def _harmonize():
		font = _require_font()
		HarmonizerClass = _require_rmx("RMXHarmonizer")
		layer = _get_layer(font, glyph_name, master_id)

		harmonizer = HarmonizerClass.alloc().init()

		font.disableUpdateInterface()
		try:
			if mode == "extract handles":
				harmonizer.extractHandles_(layer)
			elif mode == "dekink":
				harmonizer.dekinkOnly_(layer)
			elif mode == "harmonize":
				harmonizer.harmonize_(layer)
			elif mode == "supersmooth diagonals":
				harmonizer.superDiagonals_(layer)
			elif mode == "supersmooth all":
				harmonizer.superAll_(layer)
		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"glyphName": glyph_name,
			"mode": mode,
			"pathCount": len(layer.paths),
		}

	result = bridge.execute_on_main(_harmonize)
	return 200, result


# ── POST /api/filters/rmx/tune ───────────────────────────────────────────────

@route("POST", "/api/filters/rmx/tune")
def handle_rmx_tune(bridge, body=None, **kwargs):
	"""Apply RMX Tuner to a glyph layer."""
	if not body or "glyphName" not in body:
		return 400, {"error": "Body must contain 'glyphName'"}

	glyph_name = body["glyphName"]
	weight = body.get("weight", 0)
	width = body.get("width", 0)
	height = body.get("height", 0)
	slant = body.get("slant", 0)
	fixed_width = body.get("fixedWidth", False)
	master_id = body.get("masterId", None)

	def _tune():
		import math
		from Foundation import NSAffineTransform, NSPoint
		from GlyphsApp import GSPath, GSNode

		font = _require_font()
		masters = list(font.masters)
		if len(masters) < 2:
			return 400, {"error": "Tuner requires at least 2 masters"}

		# Determine which master to modify
		active_idx = 0
		if master_id:
			for i, m in enumerate(masters):
				if m.id == master_id:
					active_idx = i
					break
		target_master = masters[active_idx]

		glyph = font.glyphs[glyph_name]
		if glyph is None:
			raise KeyError(f"Glyph '{glyph_name}' not found")
		target_layer = glyph.layers[target_master.id]
		width_before = float(target_layer.width)
		lsb_before = float(target_layer.LSB)
		rsb_before = float(target_layer.RSB)

		# Find the weight axis range across masters
		axis_name = "Weight"
		axis_values = []
		for m in masters:
			for ax in font.axes:
				if ax.name == axis_name:
					axis_values.append(float(m.axes[font.axes.index(ax)]))
					break
		if len(axis_values) < 2:
			return 400, {"error": "No Weight axis found with 2+ masters"}

		axis_range = max(axis_values) - min(axis_values)
		if axis_range == 0:
			return 400, {"error": "Weight axis range is zero"}

		# Find the "other" master to interpolate toward/away from
		current_axis_val = axis_values[active_idx]
		other_idx = 0
		max_dist = 0
		for i, v in enumerate(axis_values):
			if i != active_idx and abs(v - current_axis_val) > max_dist:
				max_dist = abs(v - current_axis_val)
				other_idx = i
		other_layer = glyph.layers[masters[other_idx].id]
		sign = 1 if axis_values[other_idx] > current_axis_val else -1

		# ── Helper: decompose displacement into tangential/normal ────
		def _decomposed_pos(tx, ty, dx, dy, factor, nodes_t, j, nn,
		                    mode="normal"):
			"""Move a node by factor along one component of the displacement.

			mode="normal"     → apply only the normal component (perpendicular
			                    to contour = changes stroke weight). Use for
			                    weight adjustments.
			mode="tangential" → apply only the tangential component (along
			                    contour = changes proportions). Use for
			                    width/height scaling with stroke preservation.

			For corner nodes, picks incoming or outgoing tangent based on
			which gives the largest normal component of the displacement.
			"""
			if abs(dx) < 0.01 and abs(dy) < 0.01:
				return tx, ty

			prev_n = nodes_t[(j - 1) % nn]
			next_n = nodes_t[(j + 1) % nn]
			ppx = float(prev_n.position.x)
			ppy = float(prev_n.position.y)
			nnx = float(next_n.position.x)
			nny = float(next_n.position.y)

			in_dx, in_dy = tx - ppx, ty - ppy
			in_len = math.sqrt(in_dx**2 + in_dy**2) or 1
			in_tx, in_ty = in_dx / in_len, in_dy / in_len

			out_dx, out_dy = nnx - tx, nny - ty
			out_len = math.sqrt(out_dx**2 + out_dy**2) or 1
			out_tx, out_ty = out_dx / out_len, out_dy / out_len

			in_norm = abs(dx * (-in_ty) + dy * in_tx)
			out_norm = abs(dx * (-out_ty) + dy * out_tx)

			if in_norm >= out_norm:
				tng_x, tng_y = in_tx, in_ty
			else:
				tng_x, tng_y = out_tx, out_ty

			# Normal vector (perpendicular to tangent)
			nrm_x, nrm_y = -tng_y, tng_x

			if mode == "tangential":
				# Project displacement onto tangent direction
				d_tang = dx * tng_x + dy * tng_y
				return (tx + factor * d_tang * tng_x,
				        ty + factor * d_tang * tng_y)
			else:
				# Project displacement onto normal direction (stroke weight)
				d_norm = dx * nrm_x + dy * nrm_y
				return (tx + factor * d_norm * nrm_x,
				        ty + factor * d_norm * nrm_y)

		font.disableUpdateInterface()
		try:
			has_weight = bool(weight)
			has_wh = bool(width) or bool(height)
			has_any = has_weight or has_wh or bool(slant)

			if not has_any:
				font.enableUpdateInterface()
				return {
					"ok": True, "glyphName": glyph_name, "method": "no_change",
					"params": {"weight": 0, "width": 0, "height": 0, "slant": 0},
					"widthBefore": width_before, "widthAfter": width_before,
				}

			# ── Step 1: Weight interpolation (between masters) ───────
			if has_weight:
				w_factor = (weight * sign) / axis_range

				# Verify path compatibility
				if len(target_layer.paths) != len(other_layer.paths):
					raise RuntimeError(
						f"Path count mismatch: {len(target_layer.paths)} "
						f"vs {len(other_layer.paths)}")
				for i, (tp, op) in enumerate(
						zip(target_layer.paths, other_layer.paths)):
					if len(tp.nodes) != len(op.nodes):
						raise RuntimeError(
							f"Path {i} node count mismatch: "
							f"{len(tp.nodes)} vs {len(op.nodes)}")

				new_paths = []
				for tp, op in zip(target_layer.paths, other_layer.paths):
					nodes_t = list(tp.nodes)
					nodes_o = list(op.nodes)
					nn = len(nodes_t)
					path = GSPath()
					for j in range(nn):
						tn = nodes_t[j]
						on = nodes_o[j]
						tx = float(tn.position.x)
						ty = float(tn.position.y)
						ox = float(on.position.x)
						oy = float(on.position.y)
						# Linear interpolation: move toward other master
						nx = round(tx + w_factor * (ox - tx))
						ny = round(ty + w_factor * (oy - ty))
						node = GSNode(NSPoint(nx, ny), tn.type)
						node.smooth = tn.smooth
						path.nodes.append(node)
					path.closed = tp.closed
					new_paths.append(path)

				target_layer.beginChanges()
				for p in list(target_layer.paths):
					target_layer.removeShape_(p)
				for p in new_paths:
					target_layer.paths.append(p)
				target_layer.endChanges()
				# Restore sidebearings AFTER endChanges to avoid recalc
				target_layer.LSB = lsb_before
				target_layer.RSB = rsb_before

			# ── Step 2: Width / Height scaling (compensated) ─────────
			if has_wh:
				# Width/Height are deltas in arbitrary units.
				# Map to scale factors: delta → percentage-like scaling.
				# A delta of +50 on a range of 800 ≈ 6% wider.
				sx = 1.0 + (width / axis_range) if width else 1.0
				sy = 1.0 + (height / axis_range) if height else 1.0

				scaled_paths = []
				for tp in target_layer.paths:
					nodes_t = list(tp.nodes)
					nn = len(nodes_t)
					path = GSPath()
					for j in range(nn):
						tn = nodes_t[j]
						tx = float(tn.position.x)
						ty = float(tn.position.y)
						# Displacement = scaled pos - original pos
						dx = tx * (sx - 1.0)
						dy = ty * (sy - 1.0)
						# Apply only tangential component (preserve stroke weight)
						nx, ny = _decomposed_pos(
							tx, ty, dx, dy, 1.0, nodes_t, j, nn,
							mode="tangential")
						node = GSNode(NSPoint(round(nx), round(ny)), tn.type)
						node.smooth = tn.smooth
						path.nodes.append(node)
					path.closed = tp.closed
					scaled_paths.append(path)

				target_layer.beginChanges()
				for p in list(target_layer.paths):
					target_layer.removeShape_(p)
				for p in scaled_paths:
					target_layer.paths.append(p)
				target_layer.endChanges()
				# Restore sidebearings AFTER endChanges to avoid recalc
				target_layer.LSB = lsb_before
				target_layer.RSB = rsb_before

			# ── Step 3: Slant via native affine shear ────────────────
			if slant != 0:
				w_pre_slant = float(target_layer.width)
				tan_slant = math.tan(math.radians(slant))
				xform = NSAffineTransform.transform()
				struct = xform.transformStruct()
				struct.m21 = tan_slant
				xform.setTransformStruct_(struct)
				target_layer.beginChanges()
				target_layer.transform_(xform)
				target_layer.endChanges()

			# ── Step 4: Fixed advance width ──────────────────────────
			if fixed_width:
				target_layer.width = width_before

			method = "tuner"
		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"glyphName": glyph_name,
			"method": method,
			"params": {"weight": weight, "width": width,
			           "height": height, "slant": slant},
			"widthBefore": width_before,
			"widthAfter": float(target_layer.width),
			"lsb": float(target_layer.LSB),
			"rsb": float(target_layer.RSB),
		}

	result = bridge.execute_on_main(_tune)
	return 200, result


# ── POST /api/filters/rmx/scale ──────────────────────────────────────────────

def _scale_native(layer, width_pct, height_pct, adjust_space=0, vertical_shift=0):
	"""Native geometric scaling fallback using NSAffineTransform.

	Note: unlike RMX Scaler, this does NOT compensate stroke weight via
	interpolation. It applies a straightforward affine scale to all paths.
	"""
	from Foundation import NSAffineTransform
	width_factor = width_pct / 100.0
	height_factor = height_pct / 100.0
	old_width = float(layer.width)

	xform = NSAffineTransform.transform()
	xform.scaleXBy_yBy_(width_factor, height_factor)
	layer.transform_(xform)

	# Scale advance width + optional spacing delta
	layer.width = round(old_width * width_factor + adjust_space)

	# Optional vertical shift
	if vertical_shift:
		shift = NSAffineTransform.transform()
		shift.translateXBy_yBy_(0.0, float(vertical_shift))
		layer.transform_(shift)


@route("POST", "/api/filters/rmx/scale")
def handle_rmx_scale(bridge, body=None, **kwargs):
	"""Scale a glyph layer by percentage using RMX Scaler.

	Uses process_() (the dialog code path) with mock NSTextField ivars so that
	RMX's weight-compensated interpolation is applied across all masters.
	Falls back to native affine transform if RMX is unavailable or fails.

	Width/Height are percentages (100 = no change, 130 = 30% wider).
	Weight/adjustSpace/verticalShift are RMX-specific deltas.
	"""
	if not body or "glyphName" not in body:
		return 400, {"error": "Body must contain 'glyphName'"}

	glyph_name = body["glyphName"]
	width_pct = body.get("width", 100)
	height_pct = body.get("height", 100)
	weight = body.get("weight", 0)
	adjust_space = body.get("adjustSpace", 0)
	vertical_shift = body.get("verticalShift", 0)
	master_id = body.get("masterId", None)

	def _scale():
		font = _require_font()
		layer = _get_layer(font, glyph_name, master_id)
		glyph = layer.parent
		width_before = float(layer.width)
		method = "rmx"

		font.disableUpdateInterface()
		try:
			rmx_ok = False
			if _get_rmx_class("RMXScaler") is not None:
				try:
					_rmx_process(
						"RMXScaler", font, glyph, master_id,
						params={
							"width":         width_pct,
							"height":        height_pct,
							"weight":        weight,
							"adjustSpace":   adjust_space,
							"verticalShift": vertical_shift,
						},
					)
					if float(layer.width) != width_before or (
						width_pct == 100 and height_pct == 100
					):
						rmx_ok = True
						method = "rmx"
				except Exception as e:
					print(f"[GlyphsMCP] RMXScaler failed: {e}")

			if not rmx_ok:
				method = "native_transform"
				_scale_native(layer, width_pct, height_pct, adjust_space, vertical_shift)
		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"glyphName": glyph_name,
			"method": method,
			"params": {"width": width_pct, "height": height_pct, "weight": weight},
			"widthBefore": width_before,
			"widthAfter": float(layer.width),
		}

	result = bridge.execute_on_main(_scale)
	return 200, result


# ── POST /api/filters/rmx/monospace ──────────────────────────────────────────

@route("POST", "/api/filters/rmx/monospace")
def handle_rmx_monospace(bridge, body=None, **kwargs):
	"""Adjust a glyph to a fixed advance width using RMX Monospacer.

	Uses process_() with mock NSTextField ivars so that RMX's stroke-aware
	algorithm is applied across all masters.
	Falls back to native proportional scale + advance-width set if RMX fails.
	"""
	if not body or "glyphName" not in body:
		return 400, {"error": "Body must contain 'glyphName'"}

	glyph_name = body["glyphName"]
	mono_width = body.get("monoWidth", None)
	keep_stroke = body.get("keepStroke", 100)
	use_spacing = body.get("useSpacing", 40)
	master_id = body.get("masterId", None)

	def _monospace():
		from Foundation import NSAffineTransform
		font = _require_font()
		layer = _get_layer(font, glyph_name, master_id)
		glyph = layer.parent

		width_before = float(layer.width)
		target = int(mono_width) if mono_width is not None else int(width_before)
		method = "rmx"

		font.disableUpdateInterface()
		try:
			rmx_ok = False
			if _get_rmx_class("RMXMonospacer") is not None and target != int(width_before):
				try:
					_rmx_process(
						"RMXMonospacer", font, glyph, master_id,
						params={
							"monoWidth":  target,
							"keepStroke": keep_stroke,
							"useSpacing": use_spacing,
						},
					)
					if float(layer.width) != width_before:
						rmx_ok = True
						method = "rmx"
				except Exception as e:
					print(f"[GlyphsMCP] RMXMonospacer failed: {e}")

			if not rmx_ok:
				method = "native_transform"
				if width_before > 0 and target != int(width_before):
					outline_ratio = (100.0 - use_spacing) / 100.0
					width_delta = target - width_before
					outline_scale_x = 1.0 + (width_delta * outline_ratio / width_before)
					xform = NSAffineTransform.transform()
					xform.scaleXBy_yBy_(outline_scale_x, 1.0)
					layer.transform_(xform)
				layer.width = target
		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"glyphName": glyph_name,
			"method": method,
			"monoWidth": target,
			"widthBefore": width_before,
			"widthAfter": float(layer.width),
		}

	result = bridge.execute_on_main(_monospace)
	return 200, result


# ── POST /api/filters/rmx/batch ──────────────────────────────────────────────

@route("POST", "/api/filters/rmx/batch")
def handle_rmx_batch(bridge, body=None, **kwargs):
	"""Apply an RMX filter to multiple glyphs at once."""
	if not body or "filter" not in body or "glyphNames" not in body:
		return 400, {"error": "Body must contain 'filter' and 'glyphNames'"}

	filter_name = body["filter"]
	glyph_names = body["glyphNames"]
	params = body.get("params", {})
	master_id = body.get("masterId", None)

	valid_filters = ["harmonize", "tune", "scale", "monospace"]
	if filter_name not in valid_filters:
		return 400, {"error": f"Invalid filter '{filter_name}'. Must be one of: {valid_filters}"}

	handler_map = {
		"harmonize": handle_rmx_harmonize,
		"tune": handle_rmx_tune,
		"scale": handle_rmx_scale,
		"monospace": handle_rmx_monospace,
	}
	handler = handler_map[filter_name]

	results = []
	for gname in glyph_names:
		req_body = {"glyphName": gname, "masterId": master_id, **params}
		try:
			_status, result = handler(bridge=bridge, body=req_body)
			results.append(result)
		except Exception as e:
			results.append({"glyphName": gname, "error": str(e)})

	return 200, {
		"ok": True,
		"filter": filter_name,
		"processed": len(results),
		"results": results,
	}


# ── POST /api/execute ─────────────────────────────────────────────────────────

@route("POST", "/api/execute")
def handle_execute(bridge, body=None, **kwargs):
	"""Execute arbitrary Python code in GlyphsApp context.

	⚠️ DANGEROUS — disabled by default. Enable via preferences.
	"""
	from GlyphsApp import Glyphs
	PREF_ALLOW_EXECUTE = "com.glyphsmcp.allowExecute"

	if not body or "code" not in body:
		return 400, {"error": "Body must contain 'code'"}

	def _check_allowed():
		return bool(Glyphs.defaults[PREF_ALLOW_EXECUTE])

	if not bridge.execute_on_main(_check_allowed):
		return 403, {"error": "Execute endpoint disabled. Set com.glyphsmcp.allowExecute = True in GlyphsApp preferences."}

	code = body["code"]

	def _execute():
		import io
		import sys

		# Capture stdout
		old_stdout = sys.stdout
		sys.stdout = capture = io.StringIO()

		error = None
		try:
			exec(code, {"Glyphs": Glyphs, "__builtins__": __builtins__})
		except Exception as e:
			error = f"{type(e).__name__}: {e}"
		finally:
			sys.stdout = old_stdout

		output = capture.getvalue()
		print(f"[GlyphsMCP Execute] code={code[:80]}... output={output[:200]}")

		return {"ok": error is None, "output": output, "error": error}

	result = bridge.execute_on_main(_execute)
	return 200, result
