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
	"""Convert string to GSNode type. Glyphs 3 uses strings directly."""
	s = type_str.lower()
	if s in ("line", "curve", "offcurve", "qcurve"):
		return s
	return "line"


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
def handle_list_glyphs(bridge, query=None, **kwargs):
	"""List glyphs with basic metadata (no path data).

	Query params:
	  category  — filter by category (Letter, Number, Punctuation, Symbol, etc.)
	  limit     — max number of glyphs to return (default: all)
	"""
	q = query or {}
	cat_filter = q.get("category", [None])[0]
	limit = q.get("limit", [None])[0]
	limit = int(limit) if limit else None

	def _list_glyphs():
		font = _require_font()
		glyphs = []
		for g in font.glyphs:
			if cat_filter and (str(g.category) if g.category else None) != cat_filter:
				continue
			glyphs.append({
				"name": str(g.name),
				"unicode": str(g.unicode) if g.unicode else None,
				"category": str(g.category) if g.category else None,
				"subCategory": str(g.subCategory) if g.subCategory else None,
			})
			if limit and len(glyphs) >= limit:
				break
		total = len(font.glyphs)
		return {"glyphs": glyphs, "count": len(glyphs), "total": total}

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

		# Wrap in update block with undo support
		font.disableUpdateInterface()
		try:
			layer.beginChanges()
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
			layer.endChanges()

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
				layer.beginChanges()
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
				layer.endChanges()
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

		layer.beginChanges()
		layer.width = float(width)
		layer.endChanges()
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
	"""Get kerning pairs for a master.

	Query params:
	  master — master ID (default: first master)
	  left   — filter by left glyph/group name
	  limit  — max number of pairs to return (default: all)
	"""
	q = query or {}
	master_id = q.get("master", [None])[0]
	left_filter = q.get("left", [None])[0]
	limit = q.get("limit", [None])[0]
	limit = int(limit) if limit else None

	def _get_kerning():
		font = _require_font()
		mid = master_id or str(font.masters[0].id)
		kerning = font.kerning.get(mid, {})

		total = sum(len(rights) for rights in kerning.values())
		pairs = []
		for left_key, rights in kerning.items():
			if left_filter and str(left_key) != left_filter:
				continue
			for right_key, value in rights.items():
				pairs.append({
					"left": str(left_key),
					"right": str(right_key),
					"value": float(value)
				})
				if limit and len(pairs) >= limit:
					return {"masterId": mid, "pairs": pairs, "count": len(pairs), "total": total}

		return {"masterId": mid, "pairs": pairs, "count": len(pairs), "total": total}

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
		glyph.beginUndo()
		glyph.color = color
		glyph.endUndo()
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
		glyph.beginUndo()
		glyph.name = new_name
		glyph.endUndo()
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
				dst_layer.beginChanges()
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
				dst_layer.endChanges()
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
		glyph.beginUndo()
		glyph.unicode = unicode_val if unicode_val else None
		glyph.endUndo()
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

		# Apply colors to glyphs (with undo support)
		for gname in glyph_names:
			glyph = font.glyphs[gname]
			if glyph is not None:
				glyph.beginUndo()
				glyph.color = worst_color.get(gname, 4)
				glyph.endUndo()

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
				glyph.beginUndo()
				glyph.color = color
				glyph.endUndo()

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
				glyph.beginUndo()
				glyph.color = color
				glyph.endUndo()

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

# Expected overshoot as percentage of zone height
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
				glyph.beginUndo()
				glyph.color = color
				glyph.endUndo()

		return {
			"masters": all_master_results,
			"mastersAnalyzed": len(masters_to_analyze),
			"industryGuidelines": {
				"roundOvershoot": "~1-2% of zone height",
				"pointedOvershoot": "should exceed round overshoot",
				"source": "industry standard",
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


def _rmx_parameter_for_master(value, master, index, masters_count):
	"""Resolve a scalar, per-master sequence, or master-keyed RMX parameter."""
	if isinstance(value, (list, tuple)):
		if len(value) == 1:
			return value[0]
		if len(value) != masters_count:
			raise ValueError(
				f"RMX parameter has {len(value)} values for {masters_count} masters"
			)
		return value[index]
	if isinstance(value, dict):
		for key in (str(master.id), str(master.name), str(index), "default"):
			if key in value:
				return value[key]
		raise ValueError(f"RMX parameter has no value for master '{master.name}'")
	return value


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
		if hasattr(RMXHybridGlyph, "initWithGSLayer_gsglyph_"):
			h = RMXHybridGlyph.alloc().initWithGSLayer_gsglyph_(layer, glyph)
		else:
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
		for i, master in enumerate(masters):
			master_value = _rmx_parameter_for_master(value, master, i, n)
			filt.updateValue_forParameter_forMaster_(master_value, param_name, i)

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

# Industry width ranges from professional fonts — [min, max] as % of ref
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

		# Apply colors in GlyphsApp (with undo support)
		for gname in check_names:
			glyph = font.glyphs[gname]
			if glyph and gname in worst_colors:
				glyph.beginUndo()
				glyph.color = worst_colors[gname]
				glyph.endUndo()

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

# Diagonal / straight stem ratio ranges from professional fonts
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

		# Apply colors (with undo support)
		for gname in check_names:
			glyph = font.glyphs[gname]
			if glyph and gname in worst_colors:
				glyph.beginUndo()
				glyph.color = worst_colors[gname]
				glyph.endUndo()

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

		# Apply colors (with undo support)
		for gname in check_names:
			glyph = font.glyphs[gname]
			if glyph and gname in worst_colors:
				glyph.beginUndo()
				glyph.color = worst_colors[gname]
				glyph.endUndo()

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
# Derived from industry patterns across professional fonts

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

		# Mark glyphs in GlyphsApp (with undo support)
		for gname, color in worst_colors.items():
			glyph = font.glyphs[gname]
			if glyph:
				glyph.beginUndo()
				glyph.color = color
				glyph.endUndo()

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
# Derived from industry patterns across professional fonts

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

		# Mark glyphs (with undo support)
		for gname, color in worst_colors.items():
			glyph = font.glyphs[gname]
			if glyph:
				glyph.beginUndo()
				glyph.color = color
				glyph.endUndo()

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


# ── POST /api/font/compatibility/check ────────────────────────────────────────

def _clean_compat_details(layer_info):
	"""Strip internal fields (types, directions) from compatibility details."""
	clean = {}
	for mname, info in layer_info.items():
		entry = {
			"paths": info["paths"],
			"nodes": info["nodes"],
			"components": info["components"],
			"anchors": info["anchors"],
			"width": info["width"],
		}
		if "pathCenters" in info:
			entry["pathCenters"] = info["pathCenters"]
		if "startNodes" in info:
			entry["startNodes"] = info["startNodes"]
		clean[mname] = entry
	return clean


def _path_center(path):
	"""Return (cx, cy) center of a path's bounding box."""
	b = path.bounds
	if b is None:
		return (0, 0)
	return (round(b.origin.x + b.size.width / 2), round(b.origin.y + b.size.height / 2))


def _start_node_pos(path):
	"""Return (x, y) of the first on-curve node of a path."""
	for n in path.nodes:
		if str(n.type) in ("line", "curve", "qcurve"):
			return (round(n.position.x), round(n.position.y))
	# fallback: first node regardless of type
	if path.nodes:
		n = path.nodes[0]
		return (round(n.position.x), round(n.position.y))
	return (0, 0)


@route("POST", "/api/font/compatibility/check")
def handle_check_compatibility(bridge, body=None, **kwargs):
	"""Check master compatibility for all (or specified) glyphs.

	Compares layers across masters for: path count, node count, node types,
	path direction, components, and anchors. Reports incompatibilities.
	Marks glyphs: red=incompatible, orange=empty/missing, green=compatible.
	"""
	glyph_names = (body or {}).get("glyphNames", None)

	def _run():
		from GlyphsApp import Glyphs
		font = _require_font()

		if len(font.masters) < 2:
			return {"ok": True, "error": "Font has only 1 master, nothing to compare"}

		masters = list(font.masters)
		master_ids = [str(m.id) for m in masters]
		master_names = [str(m.name) for m in masters]

		# Determine glyphs to check
		if glyph_names:
			check_glyphs = [font.glyphs[n] for n in glyph_names if font.glyphs[n]]
		else:
			check_glyphs = list(font.glyphs)

		results = []
		counts = {"compatible": 0, "incompatible": 0, "emptyOrMissing": 0}

		for glyph in check_glyphs:
			gname = glyph.name
			issues = []
			layer_info = {}

			# Gather layer data for each master
			for mid, mname in zip(master_ids, master_names):
				layer = glyph.layers[mid]
				npaths = len(layer.paths) if layer else 0
				ncomps = len(layer.components) if layer else 0
				nanchors = len(layer.anchors) if layer else 0
				nodes_per_path = [len(p.nodes) for p in layer.paths] if layer and npaths > 0 else []
				types_per_path = [
					[str(n.type) for n in p.nodes] for p in layer.paths
				] if layer and npaths > 0 else []
				dirs_per_path = [int(p.direction) for p in layer.paths] if layer and npaths > 0 else []
				centers = [_path_center(p) for p in layer.paths] if layer and npaths > 0 else []
				starts = [_start_node_pos(p) for p in layer.paths] if layer and npaths > 0 else []
				comp_names = [str(c.componentName) for c in layer.components] if layer and ncomps > 0 else []
				anchor_names = sorted([str(a.name) for a in layer.anchors]) if layer and nanchors > 0 else []

				layer_info[mname] = {
					"paths": npaths,
					"nodes": nodes_per_path,
					"types": types_per_path,
					"directions": dirs_per_path,
					"pathCenters": centers,
					"startNodes": starts,
					"components": comp_names,
					"anchors": anchor_names,
					"width": float(layer.width) if layer else 0,
				}

			# Compare across masters
			infos = list(layer_info.values())
			names = list(layer_info.keys())

			# Check if all layers are empty
			all_empty = all(v["paths"] == 0 and len(v["components"]) == 0 for v in infos)
			if all_empty:
				color = 1  # orange — nothing drawn
				issues.append("All layers empty")
				counts["emptyOrMissing"] += 1
				results.append({
					"glyph": gname,
					"compatible": False,
					"color": color,
					"issues": issues,
					"details": _clean_compat_details(layer_info),
				})
				glyph.beginUndo()
				glyph.color = color
				glyph.endUndo()
				continue

			# Check if some layers are empty while others have content
			drawn = [i for i, v in enumerate(infos) if v["paths"] > 0 or len(v["components"]) > 0]
			if len(drawn) < len(infos):
				drawn_names = [names[i] for i in drawn]
				missing_names = [names[i] for i in range(len(infos)) if i not in drawn]
				issues.append(f"Only drawn in {len(drawn)} of {len(infos)} masters (missing: {', '.join(missing_names)})")

			# Use first drawn master as reference
			ref_idx = drawn[0]
			ref = infos[ref_idx]
			ref_name = names[ref_idx]

			for i in drawn[1:]:
				other = infos[i]
				other_name = names[i]

				# Path count
				if other["paths"] != ref["paths"]:
					issues.append(f"Path count: {ref_name}={ref['paths']}, {other_name}={other['paths']}")
					continue  # can't compare further if path count differs

				# Per-path checks
				for pi in range(ref["paths"]):
					# Node count
					if ref["nodes"][pi] != other["nodes"][pi]:
						issues.append(f"Path {pi} node count: {ref_name}={ref['nodes'][pi]}, {other_name}={other['nodes'][pi]}")
						continue

					# Node types
					if ref["types"][pi] != other["types"][pi]:
						diffs = []
						for ni, (rt, ot) in enumerate(zip(ref["types"][pi], other["types"][pi])):
							if rt != ot:
								diffs.append(f"node {ni}: {rt}→{ot}")
						issues.append(f"Path {pi} node types differ ({other_name}): {', '.join(diffs[:5])}")

					# Path direction
					if ref["directions"][pi] != other["directions"][pi]:
						issues.append(f"Path {pi} direction: {ref_name}={ref['directions'][pi]}, {other_name}={other['directions'][pi]}")

				# Path order check — compare bounding box centers
				# If paths have same structure but different spatial order, interpolation breaks
				if ref["paths"] > 1 and ref["pathCenters"] and other["pathCenters"]:
					ref_centers = ref["pathCenters"]
					other_centers = other["pathCenters"]
					# Check if the spatial ordering of paths differs
					# Sort both by (x, y) center and see if the index mapping matches
					ref_order = sorted(range(len(ref_centers)), key=lambda k: (ref_centers[k][0], ref_centers[k][1]))
					other_order = sorted(range(len(other_centers)), key=lambda k: (other_centers[k][0], other_centers[k][1]))
					if ref_order != other_order:
						# Build a readable description of the mismatch
						ref_desc = [f"path {idx} at ({ref_centers[idx][0]},{ref_centers[idx][1]})" for idx in ref_order]
						other_desc = [f"path {idx} at ({other_centers[idx][0]},{other_centers[idx][1]})" for idx in other_order]
						issues.append(
							f"Path order mismatch: {ref_name} spatial order [{', '.join(str(x) for x in ref_order)}], "
							f"{other_name} spatial order [{', '.join(str(x) for x in other_order)}]"
						)

				# Starting node check — corresponding paths should start at similar positions
				if ref["paths"] > 0 and ref["startNodes"] and other["startNodes"]:
					for pi in range(min(len(ref["startNodes"]), len(other["startNodes"]))):
						rs = ref["startNodes"][pi]
						os_ = other["startNodes"][pi]
						# Allow generous tolerance — we just want to catch completely wrong starts
						# Use relative threshold: 30% of glyph width or 100u, whichever is larger
						threshold = max(100, ref["width"] * 0.3) if ref["width"] > 0 else 100
						dx = abs(rs[0] - os_[0])
						dy = abs(rs[1] - os_[1])
						if dx > threshold or dy > threshold:
							issues.append(
								f"Path {pi} start node mismatch: {ref_name}=({rs[0]},{rs[1]}), "
								f"{other_name}=({os_[0]},{os_[1]})"
							)

				# Component count and names
				if len(other["components"]) != len(ref["components"]):
					issues.append(f"Component count: {ref_name}={len(ref['components'])}, {other_name}={len(other['components'])}")
				elif other["components"] != ref["components"]:
					issues.append(f"Component names differ: {ref_name}={ref['components']}, {other_name}={other['components']}")

				# Anchor count and names
				if other["anchors"] != ref["anchors"]:
					issues.append(f"Anchors differ: {ref_name}={ref['anchors']}, {other_name}={other['anchors']}")

			# Determine verdict
			if not issues:
				color = 4  # green
				compatible = True
				counts["compatible"] += 1
			elif any("Only drawn" in iss for iss in issues) and len(issues) == 1:
				color = 1  # orange — missing drawing
				compatible = False
				counts["emptyOrMissing"] += 1
			else:
				color = 0  # red — real incompatibility
				compatible = False
				counts["incompatible"] += 1

			# Strip verbose internal fields from details for response
			clean_details = _clean_compat_details(layer_info)

			results.append({
				"glyph": gname,
				"compatible": compatible,
				"color": color,
				"issues": issues,
				"details": clean_details,
			})

			glyph.beginUndo()
			glyph.color = color
			glyph.endUndo()

		return {
			"ok": True,
			"masterCount": len(masters),
			"masters": master_names,
			"glyphCount": len(check_glyphs),
			"summary": counts,
			"glyphs": results,
			"colorLegend": {
				"red (0)": "incompatible masters",
				"orange (1)": "empty or missing drawing",
				"light green (4)": "compatible",
			},
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


# ── POST /api/font/kerning/analyze ────────────────────────────────────────────

# Critical kern pairs that should have kerning (from Cheng "Designing Type" §8)
# Each pair string = left+right glyph names
_CRITICAL_KERN_PAIRS = {
	"uc_uc": [
		"AV", "AW", "AT", "AY", "AC", "AG", "AO", "AQ",
		"FA", "FO",
		"LT", "LV", "LY",
		"OA",
		"PA",
		"TA", "TO",
		"VA", "VO",
		"WA", "WO",
		"YA", "YO",
	],
	"uc_lc": [
		"Av", "Aw", "Ay",
		"Fa", "Fe", "Fi", "Fo", "Fr", "Fu", "Fy",
		"He", "Ho", "Hu", "Hy",
		"Ke", "Ko", "Ku", "Kv", "Kw", "Ky",
		"Pa", "Pe", "Po",
		"Ta", "Te", "Ti", "To", "Tr", "Tu", "Tw", "Ty",
		"Va", "Ve", "Vi", "Vo", "Vu", "Vy",
		"Wa", "We", "Wi", "Wo", "Wu", "Wy",
		"Xa", "Xe", "Xo",
		"Ya", "Ye", "Yi", "Yo", "Yu", "Yv",
	],
	"lc_lc": [
		"av", "aw", "ay",
		"ev", "ew", "ey",
		"fa", "fe", "fi", "fl", "fo",
		"ov", "ow", "ox", "oy",
		"rv", "ry",
		"va", "vb", "vc", "vd", "ve", "vg", "vo",
		"wa", "wd", "we", "wg", "wo",
		"xa", "xe", "xo",
		"ya", "yc", "yd", "ye", "yo",
	],
}

# Exception ratio threshold — above this, sidebearings may need rework
_KERN_EXCEPTION_RATIO_WARN = 0.40  # 40%


def _resolve_kern_key(key, glyph_id_map):
	"""Resolve a kerning key to a human-readable name.

	Returns (resolved_name, is_group).
	- @MMK_ keys are group names, returned as-is.
	- Other keys are glyph IDs, resolved via glyph_id_map.
	"""
	key_str = str(key)
	if key_str.startswith("@MMK_"):
		return key_str, True
	# It's a glyph ID
	name = glyph_id_map.get(key_str, key_str)
	return name, False


@route("POST", "/api/font/kerning/analyze")
def handle_analyze_kerning(bridge, body=None, **kwargs):
	"""Analyze kerning quality across masters.

	Checks: cross-master missing pairs, sign changes, outlier values,
	redundant exceptions, group orphans. Marks glyphs in GlyphsApp.
	"""
	def _run():
		from GlyphsApp import Glyphs
		font = _require_font()

		if len(font.masters) < 2:
			# Single master — skip cross-master checks, still do quality checks
			pass

		masters = list(font.masters)
		master_ids = [str(m.id) for m in masters]
		master_names = [str(m.name) for m in masters]
		upm = int(font.upm)

		# Build glyph ID → name map
		glyph_id_map = {}
		for g in font.glyphs:
			glyph_id_map[str(g.id)] = str(g.name)

		# Build group membership map
		group_members = {}  # {"@MMK_L_V": ["V", "Vacute", ...]}
		for g in font.glyphs:
			lg = g.leftKerningGroup
			rg = g.rightKerningGroup
			if lg:
				gk = "@MMK_L_" + str(lg)
				group_members.setdefault(gk, []).append(str(g.name))
			if rg:
				gk = "@MMK_R_" + str(rg)
				group_members.setdefault(gk, []).append(str(g.name))

		# ── Per-master analysis ──
		per_master = {}
		# normalized_pairs[master_name] = {(left_resolved, right_resolved): value}
		normalized_pairs = {}

		for mid, mname in zip(master_ids, master_names):
			kerning = font.kerning.get(mid, {})
			pairs_resolved = []
			n_group = 0
			n_exception = 0
			values = []
			norm = {}

			for left_key, rights in kerning.items():
				left_name, left_is_group = _resolve_kern_key(left_key, glyph_id_map)
				for right_key, val in rights.items():
					right_name, right_is_group = _resolve_kern_key(right_key, glyph_id_map)
					v = float(val)
					values.append(v)
					is_group_pair = left_is_group and right_is_group
					if is_group_pair:
						n_group += 1
					else:
						n_exception += 1

					pairs_resolved.append({
						"left": left_name,
						"right": right_name,
						"leftKey": str(left_key),
						"rightKey": str(right_key),
						"value": v,
						"isGroup": is_group_pair,
					})
					norm[(left_name, right_name)] = v

			normalized_pairs[mname] = norm

			# Outlier check
			outlier_threshold = upm * 0.4
			outliers = []
			for p in pairs_resolved:
				if abs(p["value"]) > outlier_threshold:
					outliers.append({
						"left": p["left"],
						"right": p["right"],
						"value": p["value"],
						"percentUpm": round(abs(p["value"]) / upm * 100, 1),
					})

			# Exception analysis — check if glyph-level pairs are redundant
			redundant = []
			for p in pairs_resolved:
				if p["isGroup"]:
					continue
				# Find what group pair would cover this
				left_key_str = str(p["leftKey"])
				right_key_str = str(p["rightKey"])
				# Determine the group keys
				left_group_key = left_key_str if left_key_str.startswith("@MMK_") else None
				right_group_key = right_key_str if right_key_str.startswith("@MMK_") else None

				if not left_group_key:
					# Look up glyph's group
					glyph = font.glyphs[glyph_id_map.get(left_key_str, "")]
					if glyph and glyph.leftKerningGroup:
						left_group_key = "@MMK_L_" + str(glyph.leftKerningGroup)
				if not right_group_key:
					glyph = font.glyphs[glyph_id_map.get(right_key_str, "")]
					if glyph and glyph.rightKerningGroup:
						right_group_key = "@MMK_R_" + str(glyph.rightKerningGroup)

				if left_group_key and right_group_key:
					# Check if the group pair exists
					group_val = kerning.get(left_group_key, {}).get(right_group_key)
					if group_val is not None and float(group_val) == p["value"]:
						redundant.append({
							"left": p["left"],
							"right": p["right"],
							"value": p["value"],
							"groupValue": float(group_val),
						})

			stats = {
				"totalPairs": len(pairs_resolved),
				"groupPairs": n_group,
				"exceptions": n_exception,
				"minValue": round(min(values), 1) if values else 0,
				"maxValue": round(max(values), 1) if values else 0,
			}

			per_master[mname] = {
				"masterId": mid,
				"stats": stats,
				"outliers": outliers[:50],
				"outlierCount": len(outliers),
				"redundant": redundant[:50],
				"redundantCount": len(redundant),
			}

		# ── Cross-master checks ──
		cross_master = {"missingPairs": [], "signChanges": []}

		if len(masters) >= 2:
			# Collect all unique pairs across masters
			all_pairs = set()
			for norm in normalized_pairs.values():
				all_pairs.update(norm.keys())

			for pair in sorted(all_pairs):
				present_in = []
				missing_from = []
				vals = {}
				for mname in master_names:
					if pair in normalized_pairs[mname]:
						present_in.append(mname)
						vals[mname] = normalized_pairs[mname][pair]
					else:
						missing_from.append(mname)

				# Missing pair check
				if missing_from and present_in:
					cross_master["missingPairs"].append({
						"left": pair[0],
						"right": pair[1],
						"presentIn": present_in,
						"missingFrom": missing_from,
					})

				# Sign change check
				if len(vals) >= 2:
					v_list = list(vals.values())
					has_pos = any(v > 0 for v in v_list)
					has_neg = any(v < 0 for v in v_list)
					if has_pos and has_neg:
						cross_master["signChanges"].append({
							"left": pair[0],
							"right": pair[1],
							"values": vals,
						})

			# Cap output
			cross_master["missingPairCount"] = len(cross_master["missingPairs"])
			cross_master["missingPairs"] = cross_master["missingPairs"][:50]
			cross_master["signChangeCount"] = len(cross_master["signChanges"])
			cross_master["signChanges"] = cross_master["signChanges"][:50]

		# ── Group orphans ──
		orphans = {"missingLeft": [], "missingRight": [], "missingBoth": []}
		for g in font.glyphs:
			if g.category != "Letter":
				continue
			has_left = bool(g.leftKerningGroup)
			has_right = bool(g.rightKerningGroup)
			gname = str(g.name)
			if not has_left and not has_right:
				orphans["missingBoth"].append(gname)
			elif not has_left:
				orphans["missingLeft"].append(gname)
			elif not has_right:
				orphans["missingRight"].append(gname)

		# ── Critical pair coverage check ──
		# Check if essential kern pairs exist (directly or via groups)
		missing_critical = {"uc_uc": [], "uc_lc": [], "lc_lc": []}
		# Build effective kerning lookup for first master
		first_mid = master_ids[0]
		first_kerning = font.kerning.get(first_mid, {})

		# Build glyph→group map
		glyph_left_group = {}  # glyph_name → @MMK_L_...
		glyph_right_group = {}  # glyph_name → @MMK_R_...
		for g in font.glyphs:
			if g.leftKerningGroup:
				glyph_left_group[str(g.name)] = "@MMK_L_" + str(g.leftKerningGroup)
			if g.rightKerningGroup:
				glyph_right_group[str(g.name)] = "@MMK_R_" + str(g.rightKerningGroup)

		# Build glyph_name → glyph_id map
		glyph_name_to_id = {}
		for g in font.glyphs:
			glyph_name_to_id[str(g.name)] = str(g.id)

		def _has_kerning(left_name, right_name):
			"""Check if a kern pair exists (glyph-level or group-level)."""
			# Try glyph-level (by ID)
			left_id = glyph_name_to_id.get(left_name)
			right_id = glyph_name_to_id.get(right_name)
			if left_id and right_id:
				if left_id in first_kerning and right_id in first_kerning[left_id]:
					return True

			# Try group-level
			left_grp = glyph_left_group.get(left_name)
			right_grp = glyph_right_group.get(right_name)
			if left_grp and right_grp:
				if left_grp in first_kerning and right_grp in first_kerning[left_grp]:
					return True

			# Try mixed: glyph-left + group-right, group-left + glyph-right
			if left_id and right_grp:
				if left_id in first_kerning and right_grp in first_kerning[left_id]:
					return True
			if left_grp and right_id:
				if left_grp in first_kerning and right_id in first_kerning[left_grp]:
					return True

			return False

		total_critical = 0
		total_critical_present = 0
		for category, pairs in _CRITICAL_KERN_PAIRS.items():
			for pair_str in pairs:
				left_name = pair_str[0]
				right_name = pair_str[1:]
				# Handle multi-char left names (all are single char in our data)
				if len(pair_str) >= 2:
					left_name = pair_str[0]
					right_name = pair_str[1:]

				# Skip if glyphs don't exist
				if not font.glyphs[left_name] or not font.glyphs[right_name]:
					continue

				total_critical += 1
				if _has_kerning(left_name, right_name):
					total_critical_present += 1
				else:
					missing_critical[category].append(pair_str)

		critical_coverage = {
			"totalChecked": total_critical,
			"present": total_critical_present,
			"missing": total_critical - total_critical_present,
			"coveragePct": round(total_critical_present / total_critical * 100, 1) if total_critical > 0 else 0,
			"missingPairs": missing_critical,
		}

		# ── Exception ratio check ──
		exception_warnings = []
		for mname in master_names:
			md = per_master.get(mname, {})
			stats = md.get("stats", {})
			total = stats.get("totalPairs", 0)
			exceptions = stats.get("exceptions", 0)
			if total > 0:
				ratio = exceptions / total
				if ratio > _KERN_EXCEPTION_RATIO_WARN:
					exception_warnings.append({
						"master": mname,
						"exceptions": exceptions,
						"totalPairs": total,
						"ratioPct": round(ratio * 100, 1),
						"message": "High exception ratio may indicate sidebearing issues",
					})

		# ── Color marking ──
		# Collect glyphs to mark
		glyphs_red = set()    # cross-master issues
		glyphs_yellow = set() # outliers, redundant

		# Cross-master: mark glyphs involved in missing/sign-change pairs
		for issue in cross_master["missingPairs"] + cross_master["signChanges"]:
			for side in ("left", "right"):
				name = issue[side]
				if name.startswith("@MMK_"):
					# Mark the primary glyph of the group
					members = group_members.get(name, [])
					# Try to find glyph whose name matches group suffix
					group_suffix = name.split("_", 2)[-1] if "_" in name else ""
					if group_suffix and font.glyphs[group_suffix]:
						glyphs_red.add(group_suffix)
					elif members:
						glyphs_red.add(members[0])
				else:
					if font.glyphs[name]:
						glyphs_red.add(name)

		# Outliers
		for mdata in per_master.values():
			for o in mdata["outliers"]:
				for side in ("left", "right"):
					name = o[side]
					if not name.startswith("@MMK_") and font.glyphs[name]:
						glyphs_yellow.add(name)

		# Apply colors (red overrides yellow)
		for gname in glyphs_red:
			g = font.glyphs[gname]
			if g:
				g.beginUndo()
				g.color = 0
				g.endUndo()
		for gname in glyphs_yellow - glyphs_red:
			g = font.glyphs[gname]
			if g:
				g.beginUndo()
				g.color = 3
				g.endUndo()

		return {
			"ok": True,
			"masterCount": len(masters),
			"masters": master_names,
			"upm": upm,
			"perMaster": per_master,
			"crossMaster": cross_master,
			"groupOrphans": orphans,
			"criticalCoverage": critical_coverage,
			"exceptionWarnings": exception_warnings,
			"colorLegend": {
				"red (0)": "cross-master issue (missing pair or sign change)",
				"yellow (3)": "outlier value or quality warning",
			},
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


# ── POST /api/font/spacing/analyze ────────────────────────────────────────────

# Sidebearing groups based on standard spacing conventions
_SB_GROUPS = {
	# Lowercase LSB groups
	"lc_lsb_straight": {
		"ref": "n", "side": "LSB", "case": "lowercase",
		"members": ["h", "i", "k", "l", "m", "n", "p", "r"],
		"tolerance": 10,  # percentage of group average
	},
	"lc_lsb_round": {
		"ref": "o", "side": "LSB", "case": "lowercase",
		"members": ["c", "d", "e", "g", "o", "q"],
		"tolerance": 15,
	},
	# Lowercase RSB groups
	"lc_rsb_straight": {
		"ref": "n", "side": "RSB", "case": "lowercase",
		"members": ["a", "h", "m", "n", "u"],
		"tolerance": 10,
	},
	"lc_rsb_round": {
		"ref": "o", "side": "RSB", "case": "lowercase",
		"members": ["b", "e", "o", "p"],
		"tolerance": 15,
	},
	# Uppercase LSB groups
	"uc_lsb_straight": {
		"ref": "H", "side": "LSB", "case": "uppercase",
		"members": ["B", "D", "E", "F", "H", "I", "K", "L", "M", "N", "P", "R"],
		"tolerance": 10,
	},
	"uc_lsb_round": {
		"ref": "O", "side": "LSB", "case": "uppercase",
		"members": ["C", "G", "O", "Q"],
		"tolerance": 15,
	},
	# Uppercase RSB groups
	"uc_rsb_straight": {
		"ref": "H", "side": "RSB", "case": "uppercase",
		"members": ["H", "I", "U"],
		"tolerance": 10,
	},
	"uc_rsb_round": {
		"ref": "O", "side": "RSB", "case": "uppercase",
		"members": ["D", "O", "Q"],
		"tolerance": 15,
	},
}

# Glyphs that should have LSB ≈ RSB
_SYMMETRIC_GLYPHS = [
	"o", "O", "H", "I", "X", "x", "zero",
]

# Expected reference ratios
_SPACING_RATIOS = {
	"n_over_o_lsb": {"num": "n", "den": "o", "side": "LSB", "range": [1.2, 2.0], "optimal": [1.4, 1.6], "label": "n/o LSB"},
	"H_over_O_lsb": {"num": "H", "den": "O", "side": "LSB", "range": [1.2, 2.0], "optimal": [1.4, 1.6], "label": "H/O LSB"},
}

# ── Tracy/Smith per-glyph sidebearing rules (Cheng "Designing Type" §4.3) ────
# Sources: "n_lsb", "n_rsb", "o_lsb", "o_rsb", "H_lsb", "H_rsb", "O_lsb", "O_rsb"
#   "_less" = slightly less than ref, "_more" = slightly more, "_half" = ~half of ref
#   "minimum" = should be the tightest sidebearing in its case
#   None = visual/irregular, skip check
# Tolerance is % of reference value
_SB_RULES = {
	# ── Lowercase ──
	# Straight-sided (stems)
	"h": {"lsb": ("n_lsb", 5), "rsb": ("n_rsb", 10)},
	"i": {"lsb": ("n_lsb_more", 20), "rsb": ("n_rsb", 15)},
	"j": {"lsb": ("n_lsb", 10), "rsb": ("n_rsb", 15)},
	"k": {"lsb": ("n_lsb_more", 15), "rsb": ("minimum", 0)},
	"l": {"lsb": ("n_lsb_more", 15), "rsb": ("n_rsb", 15)},
	"m": {"lsb": ("n_lsb", 5), "rsb": ("n_rsb", 10)},
	"r": {"lsb": ("n_lsb", 5), "rsb": ("minimum", 0)},
	"u": {"lsb": ("n_rsb", 15), "rsb": ("n_rsb", 10)},
	# Round-sided
	"b": {"lsb": ("n_lsb_more", 15), "rsb": ("o_rsb", 10)},
	"c": {"lsb": ("o_lsb", 10), "rsb": None},
	"d": {"lsb": ("o_lsb", 10), "rsb": ("n_rsb", 10)},
	"e": {"lsb": ("o_lsb", 10), "rsb": None},
	"g": {"lsb": ("o_lsb", 15), "rsb": None},
	"p": {"lsb": ("n_lsb_more", 15), "rsb": ("o_rsb", 10)},
	"q": {"lsb": ("o_lsb", 10), "rsb": ("n_rsb", 10)},
	# Diagonal (minimum space)
	"v": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"w": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"x": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"y": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	# Irregular (visual only — skip)
	# a, f, g, s, t, z: no formula

	# ── Uppercase ──
	# Straight-sided (heavy verticals)
	"B": {"lsb": ("H_lsb", 5), "rsb": None},
	"D": {"lsb": ("H_lsb", 5), "rsb": ("O_rsb", 15)},
	"E": {"lsb": ("H_lsb", 5), "rsb": None},
	"F": {"lsb": ("H_lsb", 5), "rsb": None},
	"I": {"lsb": ("H_lsb", 10), "rsb": ("H_rsb", 10)},
	"K": {"lsb": ("H_lsb", 5), "rsb": ("minimum", 0)},
	"L": {"lsb": ("H_lsb", 5), "rsb": ("minimum", 0)},
	"P": {"lsb": ("H_lsb", 5), "rsb": None},
	"R": {"lsb": ("H_lsb", 5), "rsb": None},
	# Straight-sided (light verticals — slightly less than H)
	"M": {"lsb": ("H_lsb_less", 15), "rsb": ("H_rsb_less", 15)},
	"N": {"lsb": ("H_lsb_less", 15), "rsb": ("H_rsb_less", 15)},
	# Round-sided
	"C": {"lsb": ("O_lsb", 10), "rsb": None},
	"G": {"lsb": ("O_lsb", 10), "rsb": None},
	"Q": {"lsb": ("O_lsb", 10), "rsb": ("O_rsb", 10)},
	# Straight + round mix
	"J": {"lsb": ("minimum", 0), "rsb": ("H_rsb", 20)},
	"U": {"lsb": ("H_lsb", 10), "rsb": ("H_rsb", 10)},
	# Diagonal/open (minimum space)
	"A": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"T": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"V": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"W": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"X": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	"Y": {"lsb": ("minimum", 0), "rsb": ("minimum", 0)},
	# Central spine (half of H)
	"S": {"lsb": ("H_half", 25), "rsb": ("H_half", 25)},
	"Z": {"lsb": ("H_half", 25), "rsb": ("H_half", 25)},
}

# Side type classification for ordering check (straight > round > diagonal)
_SIDE_TYPE_LC = {
	"straight": ["h", "i", "k", "l", "m", "n", "r", "u"],
	"round": ["b", "c", "d", "e", "g", "o", "p", "q"],
	"diagonal": ["v", "w", "x", "y"],
}
_SIDE_TYPE_UC = {
	"straight": ["B", "D", "E", "F", "H", "I", "K", "L", "M", "N", "P", "R", "U"],
	"round": ["C", "G", "O", "Q"],
	"diagonal": ["A", "T", "V", "W", "X", "Y"],
}


def _measure_margin_areas(layer, zone_top, zone_bottom, resolution, NSPoint):
	"""Measure left and right white space areas using scanlines.

	Returns dict with leftArea, rightArea, lsb, rsb, width, or None if empty.
	"""
	if zone_top <= zone_bottom or layer.width <= 0:
		return None

	width = float(layer.width)
	left_area = 0.0
	right_area = 0.0
	y = zone_bottom + resolution / 2.0
	scanlines = 0

	while y < zone_top:
		p1 = NSPoint(-1, y)
		p2 = NSPoint(width + 1, y)
		raw = layer.intersectionsBetweenPoints(p1, p2)
		if raw:
			xs = sorted([float(p.x) for p in raw])
			xs = [x for x in xs if -0.5 <= x <= width + 0.5]
			if len(xs) >= 2:
				leftmost = max(0, xs[0])
				rightmost = min(width, xs[-1])
				left_area += leftmost
				right_area += (width - rightmost)
				scanlines += 1
		y += resolution

	if scanlines == 0:
		return None

	return {
		"leftArea": round(left_area * resolution, 1),
		"rightArea": round(right_area * resolution, 1),
		"lsb": round(float(layer.LSB), 1) if layer.LSB is not None else 0,
		"rsb": round(float(layer.RSB), 1) if layer.RSB is not None else 0,
		"width": round(width, 1),
	}


def _measure_counter_width(layer, zone_top, zone_bottom, resolution, NSPoint):
	"""Measure the internal counter width of a glyph using scanlines.

	Returns the median counter width (distance between innermost stem walls),
	or None if measurement fails.
	"""
	if zone_top <= zone_bottom or layer.width <= 0:
		return None

	width = float(layer.width)
	counter_widths = []
	y = zone_bottom + resolution / 2.0

	while y < zone_top:
		p1 = NSPoint(-1, y)
		p2 = NSPoint(width + 1, y)
		raw = layer.intersectionsBetweenPoints(p1, p2)
		if raw:
			xs = sorted(set(round(float(p.x), 1) for p in raw))
			xs = [x for x in xs if -0.5 <= x <= width + 0.5]
			if len(xs) >= 4:
				# Counter = gap between 2nd and 3rd intersection (inner edges of stems)
				counter = xs[2] - xs[1]
				if counter > 5:
					counter_widths.append(counter)
		y += resolution

	if not counter_widths:
		return None

	counter_widths.sort()
	return round(counter_widths[len(counter_widths) // 2], 1)


def _resolve_sb_rule(source, refs):
	"""Resolve a sidebearing rule source to a target value.

	Args:
		source: rule string like "n_lsb", "n_lsb_more", "H_half", "minimum"
		refs: dict of reference values {"n_lsb": 50, "n_rsb": 45, "o_lsb": 35, ...}

	Returns (target_value, is_minimum) or (None, False) if unresolvable.
	"""
	if source == "minimum":
		return None, True

	# Parse source string
	parts = source.split("_")
	if len(parts) < 2:
		return None, False

	# Build base key (e.g. "n_lsb", "H_rsb", "o_lsb")
	base_key = parts[0] + "_" + parts[1]
	base_val = refs.get(base_key)
	if base_val is None:
		return None, False

	# Apply modifier
	modifier = parts[2] if len(parts) > 2 else None
	if modifier == "less":
		return base_val * 0.8, False
	elif modifier == "more":
		return base_val * 1.2, False
	elif modifier == "half":
		# "H_half" means source is "H_lsb" but key structure is "H_half"
		# Rebuild: take H_lsb value and halve it
		half_key = parts[0] + "_lsb"
		half_val = refs.get(half_key, base_val)
		return half_val * 0.5, False
	else:
		return base_val, False


@route("POST", "/api/font/spacing/analyze")
def handle_analyze_spacing(bridge, body=None, **kwargs):
	"""Analyze spacing quality across masters.

	Checks: sidebearing group consistency, Tracy/Smith per-glyph rules,
	side-type ordering, symmetry, reference ratios, counter-based validation,
	word space, and cross-master drift. Marks glyphs in GlyphsApp.
	"""
	master_id = (body or {}).get("masterId", "")
	glyph_names = (body or {}).get("glyphNames", None)

	def _run():
		from GlyphsApp import Glyphs
		from Foundation import NSPoint
		font = _require_font()

		masters = list(font.masters)
		if master_id:
			masters = [m for m in masters if str(m.id) == master_id]
			if not masters:
				return {"error": f"Master '{master_id}' not found"}

		master_ids = [str(m.id) for m in masters]
		master_names = [str(m.name) for m in masters]
		upm = int(font.upm)

		# Determine glyphs to analyze
		if glyph_names:
			check_glyphs = [font.glyphs[n] for n in glyph_names if font.glyphs[n]]
		else:
			check_glyphs = [g for g in font.glyphs if g.category == "Letter"]

		resolution = 5  # 5u scanline resolution

		per_master = {}
		all_measurements = {}

		for mid, mname in zip(master_ids, master_names):
			master_obj = next(m for m in font.masters if str(m.id) == mid)
			x_height = float(master_obj.xHeight) if master_obj.xHeight else 500
			cap_height = float(master_obj.capHeight) if master_obj.capHeight else 700

			measurements = {}
			for glyph in check_glyphs:
				gname = str(glyph.name)
				layer = glyph.layers[mid]
				if not layer or not layer.paths:
					if layer and layer.components:
						clean = layer.copyDecomposedLayer()
						clean.removeOverlap()
					else:
						continue
				else:
					clean = layer.copyDecomposedLayer()
					clean.removeOverlap()

				cls = _classify_glyph(glyph)
				if cls == "lowercase":
					zone_top = x_height
					zone_bottom = 0
				elif cls in ("uppercase", "figure"):
					zone_top = cap_height
					zone_bottom = 0
				else:
					continue

				result = _measure_margin_areas(clean, zone_top, zone_bottom, resolution, NSPoint)
				if result:
					measurements[gname] = result

			all_measurements[mname] = measurements

			# ── Build reference values for Tracy/Smith rules ──
			refs = {}
			for ref_name, side_key in [("n", "lsb"), ("n", "rsb"), ("o", "lsb"), ("o", "rsb"),
										("H", "lsb"), ("H", "rsb"), ("O", "lsb"), ("O", "rsb")]:
				if ref_name in measurements:
					refs[ref_name + "_" + side_key] = measurements[ref_name][side_key]

			# ── Group consistency checks (existing) ──
			group_issues = []
			for gid, ginfo in _SB_GROUPS.items():
				side = ginfo["side"]
				members_present = [m for m in ginfo["members"] if m in measurements]
				if len(members_present) < 2:
					continue

				values = {m: measurements[m][side.lower()] for m in members_present}
				avg = sum(values.values()) / len(values)
				if avg == 0:
					continue

				tol_pct = ginfo["tolerance"]
				abs_tol = max(5, abs(avg) * tol_pct / 100.0)

				for m, v in values.items():
					dev = v - avg
					if abs(dev) > abs_tol:
						group_issues.append({
							"glyph": m,
							"side": side,
							"value": v,
							"groupAvg": round(avg, 1),
							"deviation": round(dev, 1),
							"group": gid,
							"ref": ginfo["ref"],
						})

			# ── Tracy/Smith per-glyph rule checks ──
			rule_issues = []
			for gname, rules in _SB_RULES.items():
				if gname not in measurements:
					continue
				meas = measurements[gname]
				for side_key, rule in [("lsb", rules.get("lsb")), ("rsb", rules.get("rsb"))]:
					if rule is None:
						continue
					source, tol_pct = rule
					target, is_minimum = _resolve_sb_rule(source, refs)
					actual = meas[side_key]

					if is_minimum:
						# "minimum" — check it's smaller than round SBs and straight SBs
						# We just flag if it's larger than any round glyph's same-side SB
						cls = _classify_glyph(font.glyphs[gname]) if font.glyphs[gname] else None
						if cls == "lowercase":
							round_ref = refs.get("o_" + side_key)
						else:
							round_ref = refs.get("O_" + side_key)
						if round_ref is not None and actual > round_ref * 1.1:
							rule_issues.append({
								"glyph": gname,
								"side": side_key.upper(),
								"value": round(actual, 1),
								"expected": "< " + str(round(round_ref, 1)) + " (minimum/diagonal)",
								"source": source,
								"severity": "warning",
							})
					elif target is not None:
						abs_tol = max(5, abs(target) * tol_pct / 100.0)
						dev = actual - target
						if abs(dev) > abs_tol:
							rule_issues.append({
								"glyph": gname,
								"side": side_key.upper(),
								"value": round(actual, 1),
								"expected": round(target, 1),
								"deviation": round(dev, 1),
								"source": source,
								"severity": "inconsistent" if abs(dev) > abs_tol * 2 else "warning",
							})

			# ── Side-type ordering check ──
			ordering_issues = []
			for case_label, side_types in [("lowercase", _SIDE_TYPE_LC), ("uppercase", _SIDE_TYPE_UC)]:
				for side_key in ("lsb", "rsb"):
					type_avgs = {}
					for stype, members in side_types.items():
						vals = [measurements[g][side_key] for g in members if g in measurements]
						if vals:
							type_avgs[stype] = sum(vals) / len(vals)

					if "straight" in type_avgs and "round" in type_avgs:
						if type_avgs["straight"] < type_avgs["round"] * 0.9:
							ordering_issues.append({
								"case": case_label,
								"side": side_key.upper(),
								"issue": "straight < round",
								"straightAvg": round(type_avgs["straight"], 1),
								"roundAvg": round(type_avgs["round"], 1),
							})
					if "round" in type_avgs and "diagonal" in type_avgs:
						if type_avgs["round"] < type_avgs["diagonal"] * 0.9:
							ordering_issues.append({
								"case": case_label,
								"side": side_key.upper(),
								"issue": "round < diagonal",
								"roundAvg": round(type_avgs["round"], 1),
								"diagonalAvg": round(type_avgs["diagonal"], 1),
							})
					if "straight" in type_avgs and "diagonal" in type_avgs:
						if type_avgs["straight"] < type_avgs["diagonal"] * 0.9:
							ordering_issues.append({
								"case": case_label,
								"side": side_key.upper(),
								"issue": "straight < diagonal",
								"straightAvg": round(type_avgs["straight"], 1),
								"diagonalAvg": round(type_avgs["diagonal"], 1),
							})

			# ── Symmetry checks ──
			symmetry_issues = []
			for gname in _SYMMETRIC_GLYPHS:
				if gname not in measurements:
					continue
				m = measurements[gname]
				diff = abs(m["lsb"] - m["rsb"])
				tol = max(5, m["width"] * 0.05)
				if diff > tol:
					symmetry_issues.append({
						"glyph": gname,
						"lsb": m["lsb"],
						"rsb": m["rsb"],
						"difference": round(diff, 1),
					})

			# ── Reference ratios ──
			ratios = []
			for rid, rinfo in _SPACING_RATIOS.items():
				num_name = rinfo["num"]
				den_name = rinfo["den"]
				side_key = rinfo["side"].lower()
				if num_name not in measurements or den_name not in measurements:
					continue
				num_val = measurements[num_name][side_key]
				den_val = measurements[den_name][side_key]
				if den_val == 0:
					continue
				ratio = round(num_val / den_val, 2)
				lo, hi = rinfo["range"]
				opt_lo, opt_hi = rinfo.get("optimal", rinfo["range"])
				if opt_lo <= ratio <= opt_hi:
					verdict = "pass"
				elif lo <= ratio <= hi:
					verdict = "acceptable"
				else:
					verdict = "warning"
				ratios.append({
					"label": rinfo["label"],
					"numGlyph": num_name,
					"denGlyph": den_name,
					"numValue": num_val,
					"denValue": den_val,
					"ratio": ratio,
					"expectedRange": rinfo["range"],
					"optimalRange": rinfo.get("optimal", rinfo["range"]),
					"verdict": verdict,
				})

			# ── Counter-based validation ──
			counter_checks = []
			for ref_name in ("n", "H"):
				if ref_name not in measurements:
					continue
				glyph = font.glyphs[ref_name]
				if not glyph:
					continue
				layer = glyph.layers[mid]
				if not layer:
					continue
				clean = layer.copyDecomposedLayer()
				clean.removeOverlap()
				cls = _classify_glyph(glyph)
				zt = x_height if cls == "lowercase" else cap_height
				counter = _measure_counter_width(clean, zt, 0, resolution, NSPoint)
				if counter and counter > 0:
					lsb = measurements[ref_name]["lsb"]
					ratio_pct = round(lsb / counter * 100, 1) if counter > 0 else 0
					# Guide: LSB should be 25–50% of counter width
					verdict = "pass" if 20 <= ratio_pct <= 55 else "warning"
					counter_checks.append({
						"glyph": ref_name,
						"counter": counter,
						"lsb": lsb,
						"ratioPct": ratio_pct,
						"expectedRange": "25–50%",
						"verdict": verdict,
					})

			# ── Word space check ──
			word_space = None
			space_glyph = font.glyphs["space"]
			if space_glyph:
				space_layer = space_glyph.layers[mid]
				if space_layer:
					space_w = float(space_layer.width)
					quarter_em = upm / 4.0
					fifth_em = upm / 5.0
					half_em = upm / 2.0
					i_width = None
					if "i" in measurements:
						i_width = measurements["i"]["width"]

					if fifth_em <= space_w <= half_em:
						verdict = "pass"
					else:
						verdict = "warning"

					word_space = {
						"width": round(space_w, 1),
						"quarterEm": round(quarter_em, 1),
						"iWidth": round(i_width, 1) if i_width else None,
						"ratioPctEm": round(space_w / upm * 100, 1),
						"verdict": verdict,
					}

			per_master[mname] = {
				"masterId": mid,
				"glyphCount": len(measurements),
				"groupIssues": group_issues,
				"groupIssueCount": len(group_issues),
				"ruleIssues": rule_issues,
				"ruleIssueCount": len(rule_issues),
				"orderingIssues": ordering_issues,
				"orderingIssueCount": len(ordering_issues),
				"symmetryIssues": symmetry_issues,
				"symmetryIssueCount": len(symmetry_issues),
				"ratios": ratios,
				"counterChecks": counter_checks,
				"wordSpace": word_space,
			}

		# ── Cross-master spacing drift ──
		cross_master_drift = []
		if len(masters) >= 2:
			ref_mname = master_names[0]
			ref_data = all_measurements.get(ref_mname, {})
			for mname in master_names[1:]:
				other_data = all_measurements.get(mname, {})
				for glyph in check_glyphs:
					gname = str(glyph.name)
					if gname not in ref_data or gname not in other_data:
						continue
					cls = _classify_glyph(glyph)
					ref_glyph = "n" if cls == "lowercase" else "H"
					if ref_glyph == gname:
						continue
					if ref_glyph not in ref_data or ref_glyph not in other_data:
						continue

					for side_key in ("lsb", "rsb"):
						ref_ref_val = ref_data[ref_glyph][side_key]
						ref_glyph_val = ref_data[gname][side_key]
						other_ref_val = other_data[ref_glyph][side_key]
						other_glyph_val = other_data[gname][side_key]

						if ref_ref_val == 0 or other_ref_val == 0:
							continue

						ref_ratio = ref_glyph_val / ref_ref_val
						other_ratio = other_glyph_val / other_ref_val
						ratio_diff = abs(ref_ratio - other_ratio)

						if ratio_diff > 0.25:
							cross_master_drift.append({
								"glyph": gname,
								"side": side_key.upper(),
								"masterA": ref_mname,
								"masterB": mname,
								"valueA": ref_glyph_val,
								"valueB": other_glyph_val,
								"ratioA": round(ref_ratio, 2),
								"ratioB": round(other_ratio, 2),
							})

		# ── Color marking ──
		glyphs_red = set()
		glyphs_yellow = set()

		for mdata in per_master.values():
			for gi in mdata["groupIssues"]:
				if abs(gi["deviation"]) > max(10, abs(gi["groupAvg"]) * 0.2):
					glyphs_red.add(gi["glyph"])
				else:
					glyphs_yellow.add(gi["glyph"])
			for ri in mdata["ruleIssues"]:
				if ri.get("severity") == "inconsistent":
					glyphs_red.add(ri["glyph"])
				else:
					glyphs_yellow.add(ri["glyph"])
			for oi in mdata["orderingIssues"]:
				glyphs_yellow.add("*")  # structural issue, not per-glyph
			for si in mdata["symmetryIssues"]:
				glyphs_yellow.add(si["glyph"])

		for d in cross_master_drift:
			glyphs_red.add(d["glyph"])

		glyphs_red.discard("*")
		glyphs_yellow.discard("*")

		for gname in glyphs_red:
			g = font.glyphs[gname]
			if g:
				g.beginUndo()
				g.color = 0
				g.endUndo()
		for gname in glyphs_yellow - glyphs_red:
			g = font.glyphs[gname]
			if g:
				g.beginUndo()
				g.color = 3
				g.endUndo()
		all_flagged = glyphs_red | glyphs_yellow
		for glyph in check_glyphs:
			gname = str(glyph.name)
			if gname not in all_flagged and gname in all_measurements.get(master_names[0], {}):
				glyph.beginUndo()
				glyph.color = 4
				glyph.endUndo()

		return {
			"ok": True,
			"masterCount": len(masters),
			"masters": master_names,
			"upm": upm,
			"perMaster": per_master,
			"crossMasterDrift": cross_master_drift[:50],
			"crossMasterDriftCount": len(cross_master_drift),
			"colorLegend": {
				"red (0)": "significant spacing inconsistency",
				"yellow (3)": "minor deviation, ordering issue, or asymmetry",
				"light green (4)": "passed",
			},
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


# ── GET /api/font/glyphs/<name>/spacing-strings ──────────────────────────────

# Ruder test words (hard combinations with diagonals/open forms)
_RUDER_WORDS = {
	"hard": [
		"vertrag", "crainte", "screw", "fratricide", "instruction",
		"zwetschge", "waverly", "yachting", "kayak", "affixed",
	],
	"easy": [
		"bibel", "malhabile", "modo", "blind", "china",
		"schaden", "minimum", "illuminate", "million", "hidden",
	],
}

# Single-stem stress test words (from guide §6)
_SINGLE_STEM_WORDS = [
	"millennial", "initial", "minimum", "illuminator", "illicit",
	"titular", "militant", "trivial", "filial", "utilitarian",
]


@route("GET", "/api/font/glyphs/{glyph_name}/spacing-strings")
def handle_spacing_strings(bridge, glyph_name=None, **kwargs):
	"""Generate spacing test strings for a glyph.

	Returns canonical test strings for visual spacing evaluation:
	- Three-at-a-time (OH no Type Co method)
	- Systematic pairs (Jamra method)
	- Ruder test words
	- Single-stem stress test
	"""
	def _run():
		from GlyphsApp import Glyphs
		font = _require_font()

		glyph = font.glyphs[glyph_name]
		if not glyph:
			return {"error": f"Glyph '{glyph_name}' not found"}

		cls = _classify_glyph(glyph)
		g = glyph_name

		# Determine sandwich characters based on case
		if cls == "lowercase":
			straight_ref = "n"
			round_ref = "o"
			alphabet = "abcdefghijklmnopqrstuvwxyz"
		else:
			straight_ref = "H"
			round_ref = "O"
			alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

		s = straight_ref
		r = round_ref

		# ── Three-at-a-time (OH no Type Co) ──
		three_at_a_time = {
			"betweenStraight": f"{s}{s}{g}{s}{g}{s}{s}",
			"betweenRound": f"{r}{r}{g}{r}{g}{r}{r}",
			"mixed": f"{s}{g}{r}{g}{s}{g}{r}",
			"reference": f"{s}{s}{s}{s}  {r}{r}{r}{r}  {s}{r}{s}{r}{s}",
		}

		# ── Systematic pairs (Jamra) — glyph vs all others in its case ──
		systematic = ""
		available = [c for c in alphabet if font.glyphs[c]]
		for c in available:
			systematic += g + c
		systematic_pairs = systematic[:200]  # Cap length

		# ── Cross-case integration ──
		if cls == "lowercase":
			cross_case = f"HH{g}Hnn{g}nHHn{g}{g}n"
		else:
			cross_case = f"HH{g}HnnHn{g}{g}nOOn{g}On"

		# ── Find Ruder words containing this glyph ──
		ruder_matches = []
		for word in _RUDER_WORDS["hard"] + _RUDER_WORDS["easy"]:
			if glyph_name.lower() in word:
				ruder_matches.append(word)

		# ── Single-stem stress test (for i, l, t, r, etc.) ──
		single_stem = []
		if glyph_name in ("i", "l", "r", "t", "I", "one"):
			single_stem = _SINGLE_STEM_WORDS

		# ── Context strings with common problematic neighbors ──
		if cls == "lowercase":
			context_strings = [
				f"nn{g}nn",
				f"oo{g}oo",
				f"no{g}on",
				f"nn{g}{g}nn",
				f"oo{g}{g}oo",
			]
		else:
			context_strings = [
				f"HH{g}HH",
				f"OO{g}OO",
				f"HO{g}OH",
				f"HH{g}{g}HH",
				f"OO{g}{g}OO",
			]

		return {
			"ok": True,
			"glyph": glyph_name,
			"case": cls,
			"threeAtATime": three_at_a_time,
			"systematicPairs": systematic_pairs,
			"crossCase": cross_case,
			"contextStrings": context_strings,
			"ruderWords": ruder_matches if ruder_matches else None,
			"singleStemStress": single_stem if single_stem else None,
			"ruderTest": {
				"hard": _RUDER_WORDS["hard"],
				"easy": _RUDER_WORDS["easy"],
				"instruction": "Set both columns side by side. If hard column looks darker, spacing is too tight.",
			},
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


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
			layer.beginChanges()
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
			layer.endChanges()
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
	"""Apply the real RMX Tuner to a glyph layer or master layers.

	This intentionally delegates to the installed/licensed RMXTuner instance in
	Glyphs.filters. It does not approximate RMX with native interpolation.
	"""
	if not body or "glyphName" not in body:
		return 400, {"error": "Body must contain 'glyphName'"}

	glyph_name = body["glyphName"]
	weight = body.get("weight", 0)
	width = body.get("width", 0)
	height = body.get("height", 0)
	slant = body.get("slant", 0)
	blend = body.get("blend", 0)
	fixed_width = body.get("fixedWidth", False)
	master_id = body.get("masterId", None)
	all_masters = bool(body.get("allMasters", False))
	preserve_defaults = bool(body.get("preserveDefaults", True))

	def _tune():
		from GlyphsApp import Glyphs
		from Foundation import NSMutableArray, NSNumber, NSUserDefaults
		from AppKit import NSButton
		try:
			from AppKit import NSOnState, NSOffState
		except Exception:
			NSOnState = 1
			NSOffState = 0

		font = _require_font()
		glyph = font.glyphs[glyph_name]
		if glyph is None:
			raise KeyError(f"Glyph '{glyph_name}' not found")

		def _find_tuner():
			filters = getattr(Glyphs, "filters", None)
			if filters is None:
				raise RuntimeError("Glyphs.filters is not available; RMX may not be loaded")
			for candidate in filters:
				if type(candidate).__name__ == "RMXTuner":
					return candidate
				try:
					title = candidate.title()
				except Exception:
					title = None
				if title and "RMX" in str(title) and "Tuner" in str(title):
					return candidate
			raise RuntimeError(
				"RMXTuner was not found in Glyphs.filters. "
				"Install/load RMX Tools and restart Glyphs."
			)

		def _default_controller():
			for attr in ("currentTab", "fontView"):
				try:
					controller = getattr(font, attr)
				except Exception:
					controller = None
				if controller is not None:
					return controller
			try:
				controllers = font.parent.windowControllers()
				if controllers and len(controllers):
					return controllers[0]
			except Exception:
				pass
			return None

		def _checkbox(enabled):
			button = NSButton.alloc().init()
			button.setState_(NSOnState if bool(enabled) else NSOffState)
			return button

		def _summary(layer):
			bounds = layer.bounds
			return {
				"width": round(float(layer.width), 3),
				"lsb": round(float(layer.LSB), 3) if layer.LSB is not None else None,
				"rsb": round(float(layer.RSB), 3) if layer.RSB is not None else None,
				"bounds": {
					"x": round(float(bounds.origin.x), 3),
					"y": round(float(bounds.origin.y), 3),
					"width": round(float(bounds.size.width), 3),
					"height": round(float(bounds.size.height), 3),
				},
				"pathCount": len(layer.paths),
				"componentCount": len(layer.components),
			}

		def _changed(before, after):
			return before != after

		def _save_defaults(defaults):
			keys = ("GSRMX_preview", "GSRMX_fixedWidth")
			return {key: defaults.objectForKey_(key) for key in keys}

		def _restore_defaults(defaults, saved):
			for key, value in saved.items():
				if value is None:
					defaults.removeObjectForKey_(key)
				else:
					defaults.setObject_forKey_(value, key)
			try:
				defaults.synchronize()
			except Exception:
				pass

		if all_masters:
			layers = [glyph.layers[m.id] for m in font.masters if glyph.layers[m.id] is not None]
		else:
			target_master_id = master_id or font.masters[0].id
			layer = glyph.layers[target_master_id]
			if layer is None:
				raise KeyError(f"Layer/master '{target_master_id}' not found for glyph '{glyph_name}'")
			layers = [layer]

		if not layers:
			raise RuntimeError(f"No layers to tune for glyph '{glyph_name}'")

		tuner = _find_tuner()
		controller = _default_controller()
		if controller is None:
			raise RuntimeError("Could not resolve a Glyphs controller for RMXTuner")

		before = {str(layer.layerId): _summary(layer) for layer in layers}
		defaults = NSUserDefaults.standardUserDefaults()
		saved_defaults = _save_defaults(defaults) if preserve_defaults else None
		warnings = []

		try:
			tuner.setController_(controller)
			tuner.setValue_forKey_(NSMutableArray.arrayWithArray_(layers), "layers")
			setup_error = tuner.setup()
			if setup_error:
				raise RuntimeError(f"RMXTuner.setup() returned error: {setup_error}")

			tuner.setPreview_(_checkbox(False))
			tuner.setFixedWidth_(_checkbox(fixed_width))

			for selector, value, label in (
				("setWidth_", width, "width"),
				("setHeight_", height, "height"),
				("setWeight_", weight, "weight"),
				("setSlant_", slant, "slant"),
				("setBlend_", blend, "blend"),
			):
				if hasattr(tuner, selector):
					getattr(tuner, selector)(NSNumber.numberWithDouble_(float(value)))
				elif value:
					warnings.append(f"RMXTuner does not respond to {selector}; skipped {label}")

			# Use the same code path as RMX Tuner's UI Apply/OK action.
			tuner.process_(None)
		finally:
			if preserve_defaults and saved_defaults is not None:
				_restore_defaults(defaults, saved_defaults)

		after = {str(layer.layerId): _summary(layer) for layer in layers}
		layer_results = []
		for layer in layers:
			lid = str(layer.layerId)
			master_name = ""
			try:
				master_name = layer.associatedFontMaster().name
			except Exception:
				pass
			layer_results.append({
				"layerId": lid,
				"master": master_name,
				"before": before[lid],
				"after": after[lid],
				"changed": _changed(before[lid], after[lid]),
			})

		return {
			"ok": True,
			"glyphName": glyph_name,
			"method": "rmx_tuner",
			"params": {
				"width": width,
				"height": height,
				"weight": weight,
				"slant": slant,
				"blend": blend,
				"fixedWidth": fixed_width,
				"allMasters": all_masters,
				"masterId": master_id,
			},
			"processedLayers": len(layers),
			"changedLayers": sum(1 for item in layer_results if item["changed"]),
			"layers": layer_results,
			"warnings": warnings,
		}

	result = bridge.execute_on_main(_tune)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
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

	Uses process_() (the dialog code path) so RMX's weight-compensated
	interpolation is applied across all masters. Native affine fallback is
	disabled by default because it cannot preserve stem weight.

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
	allow_fallback = bool(body.get("allowFallback", False))

	def _scale():
		font = _require_font()
		layer = _get_layer(font, glyph_name, master_id)
		glyph = layer.parent
		width_before = float(layer.width)
		method = "rmx"

		font.disableUpdateInterface()
		try:
			layer.beginChanges()
			rmx_ok = False
			rmx_error = None
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
					rmx_error = str(e)
					print(f"[GlyphsMCP] RMXScaler failed: {e}")
			else:
				rmx_error = "RMXScaler class is not loaded"

			if not rmx_ok:
				if allow_fallback:
					method = "native_transform"
					_scale_native(layer, width_pct, height_pct, adjust_space, vertical_shift)
				else:
					method = "failed"
			layer.endChanges()
		finally:
			font.enableUpdateInterface()

		result = {
			"ok": rmx_ok or method == "native_transform",
			"glyphName": glyph_name,
			"method": method,
			"params": {"width": width_pct, "height": height_pct, "weight": weight},
			"widthBefore": width_before,
			"widthAfter": float(layer.width),
		}
		if not result["ok"]:
			result["error"] = rmx_error or "RMX Scaler did not modify the glyph"
		return result

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
			layer.beginChanges()
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
			layer.endChanges()
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
	PREF_ALLOW_EXECUTE = "com.nico.glyphs-mcp.allowExecute"

	if not body or "code" not in body:
		return 400, {"error": "Body must contain 'code'"}

	def _check_allowed():
		return bool(Glyphs.defaults[PREF_ALLOW_EXECUTE])

	if not bridge.execute_on_main(_check_allowed):
		return 403, {"error": "Execute endpoint disabled. Set com.nico.glyphs-mcp.allowExecute = True in GlyphsApp preferences."}

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

def _glyphs_string(value):
	"""Convert Cocoa/Python strings to a plain Python str."""
	if value is None:
		return None
	try:
		return value.stringByExpandingTildeInPath()
	except AttributeError:
		return str(value)


_BD_STYLE_MAP = {
	"LIGHT": "light",
	"HEAVY": "heavy",
	"DOUBLE": "double",
	"SINGLE": "light",
}


def _bd_rect_path(x1, y1, x2, y2):
	"""Return a rectangle path dict if it has positive area."""
	x1, x2 = sorted((float(x1), float(x2)))
	y1, y2 = sorted((float(y1), float(y2)))
	if x2 - x1 <= 0 or y2 - y1 <= 0:
		return None
	return {
		"closed": True,
		"nodes": [
			{"x": x1, "y": y1, "type": "line", "smooth": False},
			{"x": x2, "y": y1, "type": "line", "smooth": False},
			{"x": x2, "y": y2, "type": "line", "smooth": False},
			{"x": x1, "y": y2, "type": "line", "smooth": False},
		],
	}


def _bd_poly_path(points):
	"""Return a closed polygon path dict from point tuples."""
	if len(points) < 3:
		return None
	return {
		"closed": True,
		"nodes": [
			{"x": float(x), "y": float(y), "type": "line", "smooth": False}
			for x, y in points
		],
	}


def _bd_existing_glyph_has_drawing(glyph):
	"""Whether any layer already contains outlines/components."""
	if glyph is None:
		return False
	for layer in glyph.layers:
		if len(layer.paths) > 0 or len(layer.components) > 0:
			return True
	return False


def _bd_default_width(font, master_id):
	"""Pick a reasonable width for generated box glyphs."""
	for ref_name in ("zero.tf", "zero", "space", "A"):
		glyph = font.glyphs[ref_name]
		if glyph is not None:
			layer = glyph.layers[master_id]
			if layer is not None and float(layer.width) > 0:
				return float(layer.width)
	return float(font.upm)


def _bd_default_strokes(font, master_id, width):
	"""Infer light/heavy strokes and double-line gap from the font."""
	light = None
	for ref_name in ("H", "n"):
		glyph = font.glyphs[ref_name]
		if glyph is None:
			continue
		layer = glyph.layers[master_id]
		if layer is None:
			continue
		measured = _auto_measure_glyph(layer)
		light = measured.get("verticalStems", {}).get("dominant")
		if light:
			break
	if not light:
		light = max(20, int(round(width / 12.0)))
	light = max(1, int(round(light)))
	heavy = max(light + 1, int(round(light * 1.8)))
	gap = max(1, int(round(light * 0.75)))
	return light, heavy, gap


def _bd_style_value(style, light, heavy):
	if style == "double":
		return light
	if style == "heavy":
		return heavy
	return light


def _bd_segment_rects(direction, style, width, asc, desc, light, heavy, gap):
	"""Build rectangles for one segment from center to edge."""
	xc = float(width) / 2.0
	yc = (float(asc) + float(desc)) / 2.0
	thickness = _bd_style_value(style, light, heavy)
	rects = []

	def _append_h(x1, x2, center_y, stroke):
		rect = _bd_rect_path(x1, center_y - stroke / 2.0, x2, center_y + stroke / 2.0)
		if rect:
			rects.append(rect)

	def _append_v(center_x, y1, y2, stroke):
		rect = _bd_rect_path(center_x - stroke / 2.0, y1, center_x + stroke / 2.0, y2)
		if rect:
			rects.append(rect)

	if direction in ("L", "R"):
		x1, x2 = (0.0, xc) if direction == "L" else (xc, float(width))
		if style == "double":
			offset = (thickness + gap) / 2.0
			_append_h(x1, x2, yc - offset, thickness)
			_append_h(x1, x2, yc + offset, thickness)
		else:
			_append_h(x1, x2, yc, thickness)
	elif direction in ("U", "D"):
		y1, y2 = (yc, float(asc)) if direction == "U" else (float(desc), yc)
		if style == "double":
			offset = (thickness + gap) / 2.0
			_append_v(xc - offset, y1, y2, thickness)
			_append_v(xc + offset, y1, y2, thickness)
		else:
			_append_v(xc, y1, y2, thickness)

	return rects


def _bd_dash_rects(orientation, style, count, width, asc, desc, light, heavy):
	"""Approximate dashed lines with repeated rectangular segments."""
	thickness = _bd_style_value(style, light, heavy)
	segments = max(2, count + 1)
	rects = []
	if orientation == "H":
		gap_units = max(1.0, width * 0.08)
		seg_w = max(1.0, (width - gap_units * (segments - 1)) / segments)
		yc = (float(asc) + float(desc)) / 2.0
		x = 0.0
		for _ in range(segments):
			rect = _bd_rect_path(x, yc - thickness / 2.0, x + seg_w, yc + thickness / 2.0)
			if rect:
				rects.append(rect)
			x += seg_w + gap_units
	else:
		height = float(asc) - float(desc)
		gap_units = max(1.0, height * 0.08)
		seg_h = max(1.0, (height - gap_units * (segments - 1)) / segments)
		xc = float(width) / 2.0
		y = float(desc)
		for _ in range(segments):
			rect = _bd_rect_path(xc - thickness / 2.0, y, xc + thickness / 2.0, y + seg_h)
			if rect:
				rects.append(rect)
			y += seg_h + gap_units
	return rects


def _bd_quadrant_rects(quadrants, width, asc, desc):
	xc = float(width) / 2.0
	yc = (float(asc) + float(desc)) / 2.0
	rects = []
	for quad in quadrants:
		if quad == "upper left":
			rects.append(_bd_rect_path(0.0, yc, xc, float(asc)))
		elif quad == "upper right":
			rects.append(_bd_rect_path(xc, yc, float(width), float(asc)))
		elif quad == "lower left":
			rects.append(_bd_rect_path(0.0, float(desc), xc, yc))
		elif quad == "lower right":
			rects.append(_bd_rect_path(xc, float(desc), float(width), yc))
	return [r for r in rects if r]


def _bd_shade_rects(level, width, asc, desc):
	"""Approximate shade glyphs with a 4x4 checker pattern."""
	rows = cols = 4
	cell_w = float(width) / cols
	cell_h = (float(asc) - float(desc)) / rows
	rects = []
	for row in range(rows):
		for col in range(cols):
			fill = False
			if level == "light":
				fill = (row + col) % 4 == 0
			elif level == "medium":
				fill = (row + col) % 2 == 0
			elif level == "dark":
				fill = (row + col) % 4 != 0
			if not fill:
				continue
			x1 = col * cell_w
			x2 = x1 + cell_w
			y1 = float(desc) + row * cell_h
			y2 = y1 + cell_h
			rect = _bd_rect_path(x1, y1, x2, y2)
			if rect:
				rects.append(rect)
	return rects


def _bd_fraction_rects(direction, num, den, width, asc, desc):
	"""Return one-sided fractional block rectangles."""
	fraction = float(num) / float(den)
	if direction == "upper":
		y1 = float(asc) - (float(asc) - float(desc)) * fraction
		return [_bd_rect_path(0.0, y1, float(width), float(asc))]
	if direction == "lower":
		y2 = float(desc) + (float(asc) - float(desc)) * fraction
		return [_bd_rect_path(0.0, float(desc), float(width), y2)]
	if direction == "left":
		x2 = float(width) * fraction
		return [_bd_rect_path(0.0, float(desc), x2, float(asc))]
	if direction == "right":
		x1 = float(width) * (1.0 - fraction)
		return [_bd_rect_path(x1, float(desc), float(width), float(asc))]
	return []


def _bd_diagonal_path(x1, y1, x2, y2, thickness):
	"""Return a thick diagonal as a 4-point polygon."""
	import math
	dx = float(x2) - float(x1)
	dy = float(y2) - float(y1)
	length = math.hypot(dx, dy)
	if length <= 0:
		return None
	nx = -dy / length * thickness / 2.0
	ny = dx / length * thickness / 2.0
	return _bd_poly_path([
		(x1 + nx, y1 + ny),
		(x2 + nx, y2 + ny),
		(x2 - nx, y2 - ny),
		(x1 - nx, y1 - ny),
	])


def _bd_arc_path(corner, width, asc, desc, thickness):
	"""Approximate a rounded box corner with a quarter-ring polygon."""
	import math
	height = float(asc) - float(desc)
	outer = min(float(width), height) * 0.48
	inner = max(outer - thickness, outer * 0.35)
	if inner <= 0:
		inner = outer * 0.5

	if corner == "tl":
		cx, cy = 0.0, float(asc)
		a0, a1 = -90.0, 0.0
	elif corner == "tr":
		cx, cy = float(width), float(asc)
		a0, a1 = -180.0, -90.0
	elif corner == "br":
		cx, cy = float(width), float(desc)
		a0, a1 = 90.0, 180.0
	else:
		cx, cy = 0.0, float(desc)
		a0, a1 = 0.0, 90.0

	steps = 10
	outer_pts = []
	inner_pts = []
	for i in range(steps + 1):
		t = a0 + (a1 - a0) * i / steps
		rad = math.radians(t)
		outer_pts.append((cx + math.cos(rad) * outer, cy + math.sin(rad) * outer))
	for i in range(steps, -1, -1):
		t = a0 + (a1 - a0) * i / steps
		rad = math.radians(t)
		inner_pts.append((cx + math.cos(rad) * inner, cy + math.sin(rad) * inner))
	return _bd_poly_path(outer_pts + inner_pts)


def _bd_parse_box_edges(body):
	"""Parse orthogonal BOX DRAWINGS names into edge styles."""
	if "DIAGONAL" in body or "ARC" in body:
		return None
	tokens = body.split()
	default_style = None
	if tokens and tokens[0] in _BD_STYLE_MAP and len(tokens) > 1 and tokens[1] != "DASH":
		default_style = _BD_STYLE_MAP[tokens[0]]
		body = " ".join(tokens[1:])
	parts = body.split(" AND ")
	edges = {}
	for part in parts:
		ptoks = part.split()
		style = default_style or "light"
		if ptoks and ptoks[0] in _BD_STYLE_MAP and len(ptoks) > 1:
			style = _BD_STYLE_MAP[ptoks[0]]
			ptoks = ptoks[1:]
		elif ptoks and ptoks[-1] in _BD_STYLE_MAP:
			style = _BD_STYLE_MAP[ptoks[-1]]
			ptoks = ptoks[:-1]
		words = set(ptoks)
		dirs = set()
		if "HORIZONTAL" in words and "DOWN" in words:
			dirs |= {"L", "R", "D"}
		elif "HORIZONTAL" in words and "UP" in words:
			dirs |= {"L", "R", "U"}
		elif "VERTICAL" in words and "RIGHT" in words:
			dirs |= {"U", "D", "R"}
		elif "VERTICAL" in words and "LEFT" in words:
			dirs |= {"U", "D", "L"}
		elif "HORIZONTAL" in words:
			dirs |= {"L", "R"}
		elif "VERTICAL" in words:
			dirs |= {"U", "D"}
		else:
			if "UP" in words:
				dirs.add("U")
			if "DOWN" in words:
				dirs.add("D")
			if "LEFT" in words:
				dirs.add("L")
			if "RIGHT" in words:
				dirs.add("R")
		if not dirs:
			return None
		for direction in dirs:
			edges[direction] = style
	return edges


def _bd_generate_paths_for_codepoint(codepoint, width, asc, desc, light, heavy, gap):
	"""Generate simple box-drawing/block-element paths from a Unicode codepoint."""
	import unicodedata

	ch = chr(codepoint)
	try:
		name = unicodedata.name(ch)
	except ValueError:
		return None, "Unnamed Unicode character"

	if name.startswith("BOX DRAWINGS "):
		body = name[len("BOX DRAWINGS "):]
		if body == "LIGHT ARC DOWN AND RIGHT":
			return [_bd_arc_path("tl", width, asc, desc, light)], None
		if body == "LIGHT ARC DOWN AND LEFT":
			return [_bd_arc_path("tr", width, asc, desc, light)], None
		if body == "LIGHT ARC UP AND LEFT":
			return [_bd_arc_path("br", width, asc, desc, light)], None
		if body == "LIGHT ARC UP AND RIGHT":
			return [_bd_arc_path("bl", width, asc, desc, light)], None
		if body == "LIGHT DIAGONAL UPPER RIGHT TO LOWER LEFT":
			return [_bd_diagonal_path(float(width), float(asc), 0.0, float(desc), light)], None
		if body == "LIGHT DIAGONAL UPPER LEFT TO LOWER RIGHT":
			return [_bd_diagonal_path(0.0, float(asc), float(width), float(desc), light)], None
		if body == "LIGHT DIAGONAL CROSS":
			return [
				_bd_diagonal_path(0.0, float(asc), float(width), float(desc), light),
				_bd_diagonal_path(float(width), float(asc), 0.0, float(desc), light),
			], None
		if "DASH" in body:
			tokens = body.split()
			style = "light"
			if tokens and tokens[0] in _BD_STYLE_MAP:
				style = _BD_STYLE_MAP[tokens[0]]
			count = 2
			if "TRIPLE" in tokens:
				count = 3
			elif "QUADRUPLE" in tokens:
				count = 4
			orientation = "H" if "HORIZONTAL" in tokens else "V" if "VERTICAL" in tokens else None
			if orientation is None:
				return None, f"Unsupported dashed box drawing: {name}"
			return _bd_dash_rects(orientation, style, count, width, asc, desc, light, heavy), None

		edges = _bd_parse_box_edges(body)
		if edges is None:
			return None, f"Unsupported box drawing form: {name}"
		paths = []
		for direction, style in edges.items():
			paths.extend(_bd_segment_rects(direction, style, width, asc, desc, light, heavy, gap))
		return paths, None

	if (
		name.endswith(" BLOCK")
		or name.endswith(" SHADE")
		or name.startswith("QUADRANT ")
	):
		body = name
		if body == "FULL BLOCK":
			return [_bd_rect_path(0.0, float(desc), float(width), float(asc))], None
		if body in ("LIGHT SHADE", "MEDIUM SHADE", "DARK SHADE"):
			return _bd_shade_rects(body.split()[0].lower(), width, asc, desc), None
		if body.startswith("QUADRANT "):
			quadrants = [part.strip().lower() for part in body[len("QUADRANT "):].split(" AND ")]
			return _bd_quadrant_rects(quadrants, width, asc, desc), None

		fraction_map = {
			"ONE EIGHTH": (1, 8),
			"ONE QUARTER": (1, 4),
			"THREE EIGHTHS": (3, 8),
			"HALF": (1, 2),
			"FIVE EIGHTHS": (5, 8),
			"THREE QUARTERS": (3, 4),
			"SEVEN EIGHTHS": (7, 8),
		}
		direction = None
		for candidate in ("UPPER", "LOWER", "LEFT", "RIGHT"):
			if body.startswith(candidate + " "):
				direction = candidate.lower()
				rest = body[len(candidate) + 1:]
				break
		else:
			rest = body
		if direction and rest.endswith(" BLOCK"):
			fraction_label = rest[:-len(" BLOCK")]
			if fraction_label in fraction_map:
				num, den = fraction_map[fraction_label]
				return [r for r in _bd_fraction_rects(direction, num, den, width, asc, desc) if r], None

		return None, f"Unsupported block element form: {name}"

	return None, "Outside supported ranges"


@route("GET", "/api/recipes")
def handle_list_recipes(bridge, **kwargs):
	"""List available workflow recipes."""
	import os
	recipes_dir = os.path.join(os.path.dirname(__file__), "recipes")
	if not os.path.isdir(recipes_dir):
		return 200, {"recipes": []}
	recipes = []
	for f in sorted(os.listdir(recipes_dir)):
		if f.endswith(".md"):
			name = f[:-3]
			# Read first line as title
			filepath = os.path.join(recipes_dir, f)
			title = name
			with open(filepath, "r", encoding="utf-8") as fh:
				first_line = fh.readline().strip()
				if first_line.startswith("# "):
					title = first_line[2:].strip()
					# Strip "Recipe: " prefix if present
					if title.startswith("Recipe: "):
						title = title[8:]
			recipes.append({"name": name, "title": title})
	return 200, {"recipes": recipes}


@route("GET", "/api/recipes/{name}")
def handle_get_recipe(bridge, name=None, **kwargs):
	"""Get a specific workflow recipe by name."""
	import os
	if not name:
		return 400, {"error": "Recipe name required"}
	recipes_dir = os.path.join(os.path.dirname(__file__), "recipes")
	filepath = os.path.join(recipes_dir, f"{name}.md")
	if not os.path.isfile(filepath):
		return 404, {"error": f"Recipe '{name}' not found"}
	with open(filepath, "r", encoding="utf-8") as fh:
		content = fh.read()

	# Parse steps
	steps = _parse_recipe_steps(content)

	return 200, {"name": name, "content": content, "totalSteps": len(steps)}


@route("GET", "/api/recipes/{name}/step/{step}")
def handle_get_recipe_step(bridge, name=None, step=None, **kwargs):
	"""Get a specific step from a recipe."""
	import os
	if not name:
		return 400, {"error": "Recipe name required"}
	if step is None:
		return 400, {"error": "Step number required"}
	try:
		step_num = int(step)
	except (ValueError, TypeError):
		return 400, {"error": "Step must be a number"}

	recipes_dir = os.path.join(os.path.dirname(__file__), "recipes")
	filepath = os.path.join(recipes_dir, f"{name}.md")
	if not os.path.isfile(filepath):
		return 404, {"error": f"Recipe '{name}' not found"}
	with open(filepath, "r", encoding="utf-8") as fh:
		content = fh.read()

	steps = _parse_recipe_steps(content)
	if not steps:
		return 404, {"error": "No steps found in recipe"}
	if step_num < 1 or step_num > len(steps):
		return 400, {"error": f"Step {step_num} out of range (1-{len(steps)})"}

	s = steps[step_num - 1]
	total = len(steps)

	# Build mandatory directive for strict step ordering
	if step_num < total:
		directive = (
			"MANDATORY: Execute ALL tools listed in this step and report results to the designer. "
			"Then call get_recipe_step('%s', %d) to proceed to step %d of %d. "
			"Do NOT skip ahead. Do NOT combine steps."
		) % (name, step_num + 1, step_num + 1, total)
	else:
		directive = (
			"MANDATORY: Execute ALL tools listed in this step and report results. "
			"This is the FINAL step (%d of %d). After completing it, provide a full summary "
			"of all findings across all steps."
		) % (step_num, total)

	return 200, {
		"recipe": name,
		"step": step_num,
		"totalSteps": total,
		"title": s["title"],
		"content": s["content"],
		"directive": directive,
	}


@route("POST", "/api/recipes")
def handle_create_recipe(bridge, body=None, **kwargs):
	"""Create a new workflow recipe."""
	import os, re
	if not body:
		return 400, {"error": "Request body required"}

	name = body.get("name", "").strip()
	content = body.get("content", "").strip()

	if not name:
		return 400, {"error": "'name' is required (snake_case identifier)"}
	if not content:
		return 400, {"error": "'content' is required (markdown text)"}

	# Validate name: only lowercase, digits, underscores
	if not re.match(r'^[a-z0-9_]+$', name):
		return 400, {"error": "Recipe name must be snake_case (lowercase, digits, underscores only)"}

	recipes_dir = os.path.join(os.path.dirname(__file__), "recipes")
	os.makedirs(recipes_dir, exist_ok=True)
	filepath = os.path.join(recipes_dir, f"{name}.md")

	overwrite = body.get("overwrite", False)
	if os.path.isfile(filepath) and not overwrite:
		return 409, {"error": f"Recipe '{name}' already exists. Set overwrite=true to replace."}

	with open(filepath, "w", encoding="utf-8") as fh:
		fh.write(content)

	# Read back title
	title = name
	first_line = content.split("\n")[0].strip()
	if first_line.startswith("# "):
		title = first_line[2:].strip()
		if title.startswith("Recipe: "):
			title = title[8:]

	return 201, {"name": name, "title": title, "created": True}


@route("DELETE", "/api/recipes/{name}")
def handle_delete_recipe(bridge, name=None, **kwargs):
	"""Delete a workflow recipe."""
	import os
	if not name:
		return 400, {"error": "Recipe name required"}

	recipes_dir = os.path.join(os.path.dirname(__file__), "recipes")
	filepath = os.path.join(recipes_dir, f"{name}.md")
	if not os.path.isfile(filepath):
		return 404, {"error": f"Recipe '{name}' not found"}

	os.remove(filepath)
	return 200, {"name": name, "deleted": True}


def _parse_recipe_steps(content):
	"""Parse numbered ### steps from recipe markdown.

	Expected format:
		### 1. Step title
		Step content...

		### 2. Another step
		More content...

	Returns list of {"number": int, "title": str, "content": str}.
	"""
	import re
	steps = []
	# Split on ### N. or ### Step N headings
	pattern = r'^###\s+(\d+)\.\s*(.*?)$'
	lines = content.split('\n')
	current_step = None
	current_lines = []

	for line in lines:
		m = re.match(pattern, line)
		if m:
			# Save previous step
			if current_step is not None:
				steps.append({
					"number": current_step["number"],
					"title": current_step["title"],
					"content": "\n".join(current_lines).strip(),
				})
			current_step = {
				"number": int(m.group(1)),
				"title": m.group(2).strip(),
			}
			current_lines = []
		elif current_step is not None:
			current_lines.append(line)

	# Save last step
	if current_step is not None:
		steps.append({
			"number": current_step["number"],
			"title": current_step["title"],
			"content": "\n".join(current_lines).strip(),
		})

	return steps


@route("POST", "/api/font/glyphs/bulk-create")
def handle_bulk_create_glyphs(bridge, body=None, **kwargs):
	"""Create multiple glyphs at once. Skips existing glyphs."""
	if not body or "glyphs" not in body:
		return 400, {"error": "Body must contain 'glyphs' array with [{name, unicode?}, ...]"}

	glyphs_to_create = body["glyphs"]
	color_label = body.get("color", None)  # optional color marking

	def _run():
		from GlyphsApp import Glyphs, GSGlyph
		font = _require_font()

		created = []
		skipped = []

		font.disableUpdateInterface()
		try:
			for ginfo in glyphs_to_create:
				name = ginfo if isinstance(ginfo, str) else ginfo.get("name")
				if not name:
					continue

				# Skip existing
				if font.glyphs[name]:
					skipped.append(name)
					continue

				glyph = GSGlyph(name)

				# Set unicode if provided (GlyphsApp auto-assigns from name if not)
				unicode_val = None if isinstance(ginfo, str) else ginfo.get("unicode")
				if unicode_val:
					glyph.unicode = str(unicode_val)

				font.glyphs.append(glyph)

				# Color marking
				if color_label is not None:
					glyph.color = int(color_label)

				created.append(name)
		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"created": len(created),
			"skipped": len(skipped),
			"createdGlyphs": created[:200],
			"skippedGlyphs": skipped[:200],
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 500, {"ok": False, "error": result["error"]}
	return 201, result


@route("POST", "/api/font/export-instance")
def handle_export_instance(bridge, body=None, **kwargs):
	"""Export an instance to a temporary binary and return it as base64."""
	body = body or {}
	instance_name = body.get("instanceName", "")
	export_format = str(body.get("format", "otf") or "otf").lower()

	if export_format not in ("otf", "ttf"):
		return 400, {"error": "Unsupported format. Use 'otf' or 'ttf'."}

	def _run():
		import base64
		import os
		import shutil
		import tempfile

		font = _require_font()

		instances = [inst for inst in font.instances if getattr(inst, "exports", True)]
		if not instances:
			instances = list(font.instances)
		if not instances:
			return {"error": "No instances available to export"}

		instance = None
		if instance_name:
			for inst in instances:
				if str(inst.name) == instance_name:
					instance = inst
					break
			if instance is None:
				return {"error": f"Instance '{instance_name}' not found"}
		else:
			instance = instances[0]

		temp_dir = tempfile.mkdtemp(prefix="glyphsmcp-export-")
		try:
			result = instance.generate(
				format=export_format.upper(),
				fontPath=temp_dir,
				autoHint=False,
				removeOverlap=True,
				useSubroutines=True,
				useProductionNames=True,
			)
			if result is not True:
				return {"error": f"Export failed: {result}"}

			exported_path = None
			if hasattr(instance, "lastExportedFilePath") and instance.lastExportedFilePath:
				exported_path = _glyphs_string(instance.lastExportedFilePath)
			if not exported_path or not os.path.isfile(exported_path):
				ext = f".{export_format}"
				candidates = [
					os.path.join(temp_dir, name)
					for name in os.listdir(temp_dir)
					if name.lower().endswith(ext)
				]
				if not candidates:
					return {"error": f"Export succeeded but no .{export_format} file was found"}
				exported_path = sorted(candidates)[0]

			with open(exported_path, "rb") as fh:
				font_data = base64.b64encode(fh.read()).decode("ascii")

			return {
				"ok": True,
				"instanceName": str(instance.name),
				"familyName": str(font.familyName),
				"format": export_format,
				"fileName": os.path.basename(exported_path),
				"fontData": font_data,
			}
		finally:
			shutil.rmtree(temp_dir, ignore_errors=True)

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 500, {"ok": False, "error": result["error"]}
	return 200, result


@route("POST", "/api/font/box-drawing/generate")
def handle_generate_box_drawing(bridge, body=None, **kwargs):
	"""Generate box drawing and block element glyphs in the open font."""
	body = body or {}
	glyph_names = body.get("glyphNames", []) or []
	overwrite = bool(body.get("overwrite", False))
	color_label = body.get("color", 7)
	custom_width = float(body.get("width", 0) or 0)
	custom_light = float(body.get("stroke", 0) or 0)
	custom_heavy = float(body.get("heavyStroke", 0) or 0)
	custom_gap = float(body.get("doubleGap", 0) or 0)

	def _run():
		import unicodedata
		from GlyphsApp import GSGlyph, GSPath, GSNode
		from Foundation import NSPoint

		font = _require_font()

		def _glyph_name_for_codepoint(codepoint):
			for g in font.glyphs:
				if g.unicode and int(str(g.unicode), 16) == codepoint:
					return str(g.name)
			return f"uni{codepoint:04X}"

		def _target_codepoints():
			if glyph_names:
				result = []
				for name in glyph_names:
					if isinstance(name, int):
						result.append(int(name))
						continue
					s = str(name).strip()
					if not s:
						continue
					if s.startswith("U+"):
						result.append(int(s[2:], 16))
						continue
					glyph = font.glyphs[s]
					if glyph is not None and glyph.unicode:
						result.append(int(str(glyph.unicode), 16))
						continue
					if len(s) == 1:
						result.append(ord(s))
						continue
					raise ValueError(f"Cannot resolve glyph/codepoint '{s}'")
				return sorted(set(result))
			return list(range(0x2500, 0x25A0))

		codepoints = _target_codepoints()
		created = []
		updated = []
		skipped_existing = []
		unsupported = []

		font.disableUpdateInterface()
		try:
			for codepoint in codepoints:
				glyph_name = _glyph_name_for_codepoint(codepoint)
				glyph = font.glyphs[glyph_name]
				if glyph is None:
					glyph = GSGlyph(glyph_name)
					glyph.unicode = f"{codepoint:04X}"
					font.glyphs.append(glyph)
					if color_label is not None:
						glyph.color = int(color_label)
					created.append(glyph_name)
				elif _bd_existing_glyph_has_drawing(glyph) and not overwrite:
					skipped_existing.append(glyph_name)
					continue

				generated_any = False
				last_reason = None
				for master in font.masters:
					layer = glyph.layers[master.id]
					width = custom_width if custom_width > 0 else (_bd_default_width(font, master.id) if float(layer.width) <= 0 else float(layer.width))
					auto_light, auto_heavy, auto_gap = _bd_default_strokes(font, master.id, width)
					light = custom_light if custom_light > 0 else auto_light
					heavy = custom_heavy if custom_heavy > 0 else auto_heavy
					gap = custom_gap if custom_gap > 0 else auto_gap
					paths_data, reason = _bd_generate_paths_for_codepoint(
						codepoint, width, float(master.ascender), float(master.descender), light, heavy, gap
					)
					if not paths_data:
						last_reason = reason
						continue

					layer.beginChanges()
					try:
						for shape in list(layer.shapes):
							layer.removeShape_(shape)
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
						layer.width = float(width)
						layer.correctPathDirection()
					finally:
						layer.endChanges()
					generated_any = True

				if generated_any:
					if glyph_name not in created:
						updated.append(glyph_name)
				else:
					unsupported.append({
						"glyphName": glyph_name,
						"unicode": f"{codepoint:04X}",
						"name": unicodedata.name(chr(codepoint), f"U+{codepoint:04X}"),
						"reason": last_reason or "Unsupported",
					})
		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"created": len(created),
			"updated": len(updated),
			"skippedExisting": len(skipped_existing),
			"unsupported": len(unsupported),
			"createdGlyphs": created[:200],
			"updatedGlyphs": updated[:200],
			"skippedExistingGlyphs": skipped_existing[:200],
			"unsupportedGlyphs": unsupported[:200],
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 500, {"ok": False, "error": result["error"]}
	return 200, result


_KERN_GROUP_MAP = {
	# ── Uppercase ──
	"A": ("A", "A"),
	"B": ("H", "D"),
	"C": ("O", "C"),
	"D": ("H", "O"),
	"E": ("H", "E"),
	"F": ("H", "F"),
	"G": ("O", "G"),
	"H": ("H", "H"),
	"I": ("H", "H"),
	"J": ("J", "J"),
	"K": ("H", "K"),
	"L": ("H", "L"),
	"M": ("H", "H"),
	"N": ("H", "H"),
	"O": ("O", "O"),
	"P": ("H", "P"),
	"Q": ("O", "O"),
	"R": ("H", "R"),
	"S": ("S", "S"),
	"T": ("T", "T"),
	"U": ("H", "U"),
	"V": ("V", "V"),
	"W": ("V", "V"),
	"X": ("X", "X"),
	"Y": ("V", "Y"),
	"Z": ("Z", "Z"),
	# ── Lowercase ──
	"a": ("o", "a"),
	"b": ("h", "o"),
	"c": ("o", "c"),
	"d": ("o", "l"),
	"e": ("o", "e"),
	"f": ("f", "f"),
	"g": ("o", "g"),
	"h": ("h", "n"),
	"i": ("h", "l"),
	"j": ("j", "j"),
	"k": ("h", "k"),
	"l": ("h", "l"),
	"m": ("h", "n"),
	"n": ("h", "n"),
	"o": ("o", "o"),
	"p": ("h", "o"),
	"q": ("o", "l"),
	"r": ("h", "r"),
	"s": ("s", "s"),
	"t": ("t", "t"),
	"u": ("u", "n"),
	"v": ("v", "v"),
	"w": ("v", "v"),
	"x": ("x", "x"),
	"y": ("v", "y"),
	"z": ("z", "z"),
	# ── Figures ──
	"zero": ("zero", "zero"),
	"one": ("one", "one"),
	"two": ("two", "two"),
	"three": ("three", "three"),
	"four": ("four", "four"),
	"five": ("five", "five"),
	"six": ("six", "six"),
	"seven": ("seven", "seven"),
	"eight": ("eight", "eight"),
	"nine": ("nine", "nine"),
	# ── Common ligatures ──
	"fi": ("f", "h"),
	"fl": ("f", "h"),
	"f_i": ("f", "h"),
	"f_l": ("f", "h"),
	# ── Punctuation/symbols with kerning relevance ──
	"period": ("period", "period"),
	"comma": ("period", "period"),
	"colon": ("period", "period"),
	"semicolon": ("period", "period"),
	"ellipsis": ("period", "period"),
	"quoteright": ("quoteright", "quoteright"),
	"quotedblright": ("quoteright", "quoteright"),
	"quoteleft": ("quoteleft", "quoteleft"),
	"quotedblleft": ("quoteleft", "quoteleft"),
	"hyphen": ("hyphen", "hyphen"),
	"endash": ("hyphen", "hyphen"),
	"emdash": ("hyphen", "hyphen"),
	"parenleft": ("parenleft", "parenleft"),
	"parenright": ("parenright", "parenright"),
	"bracketleft": ("bracketleft", "bracketleft"),
	"bracketright": ("bracketright", "bracketright"),
	"guillemotleft": ("guillemotleft", "guillemotleft"),
	"guillemotright": ("guillemotright", "guillemotright"),
}


_SIDE_FALLBACK_LEFT = {
	("UC", "straight"): "H", ("UC", "round"): "O", ("UC", "open"): "A",
	("LC", "straight"): "h", ("LC", "round"): "o", ("LC", "open"): "v",
	("FIG", "straight"): "one", ("FIG", "round"): "zero", ("FIG", "open"): "seven",
}


_SIDE_FALLBACK_RIGHT = {
	("UC", "straight"): "H", ("UC", "round"): "O", ("UC", "open"): "T",
	("LC", "straight"): "h", ("LC", "round"): "o", ("LC", "open"): "r",
	("FIG", "straight"): "one", ("FIG", "round"): "zero", ("FIG", "open"): "seven",
}


_FIGURE_NAMES = frozenset([
	"zero", "one", "two", "three", "four", "five",
	"six", "seven", "eight", "nine",
])


def _glyph_case_group(glyph):
	"""Classify glyph as 'UC', 'LC', or 'FIG'."""
	name = str(glyph.name)
	base = name.split(".")[0]
	if base in _FIGURE_NAMES:
		return "FIG"
	sub = glyph.subCategory
	if sub == "Uppercase":
		return "UC"
	if sub == "Lowercase":
		return "LC"
	# Fallback: unicode category
	u = glyph.unicode
	if u:
		try:
			import unicodedata
			cat = unicodedata.category(chr(int(u, 16)))
			if cat == "Lu":
				return "UC"
			if cat == "Ll":
				return "LC"
		except (ValueError, TypeError):
			pass
	# Last resort: single char name
	if len(base) == 1:
		return "UC" if base.isupper() else "LC"
	return "LC"


def _infer_side_type_kern(layer, side, zone_top):
	"""Heuristic: classify side by measuring contour edge variation.
	Returns 'straight', 'round', or 'open'."""
	from AppKit import NSPoint
	if not layer or not layer.paths:
		return "open"
	if not zone_top or zone_top <= 0:
		return "open"
	step = max(int(zone_top / 10), 10)
	edge_xs = []
	y = 0
	while y <= zone_top:
		wide = 10000
		p1 = NSPoint(-wide, y)
		p2 = NSPoint(wide, y)
		raw = layer.intersectionsBetweenPoints(p1, p2)
		if raw:
			eps = 1.0
			xs = sorted(p.x for p in raw if p.x > (-wide + eps) and p.x < (wide - eps))
			if xs:
				edge_xs.append(max(xs) if side == "right" else min(xs))
		y += step
	if len(edge_xs) < 3:
		return "open"
	x_range = max(edge_xs) - min(edge_xs)
	fraction = x_range / zone_top
	if fraction < 0.08:
		return "straight"
	elif fraction < 0.25:
		return "round"
	return "open"


def _resolve_kern_groups(glyph, font, master):
	"""Resolve kerning groups for a glyph.

	Priority chain:
	1. Dictionary lookup (base name)
	2. Dot-suffix stripping (a.ss01 → a)
	3. Component inheritance (Aacute → A via first component)
	4. Unicode decomposition (Aacute → A via unicode)
	5. Contour analysis fallback

	Returns (left_group, right_group, method) where method describes how it was resolved.
	"""
	name = str(glyph.name)

	# 1. Direct dictionary lookup
	if name in _KERN_GROUP_MAP:
		lg, rg = _KERN_GROUP_MAP[name]
		return lg, rg, "dictionary"

	# 2. Dot-suffix stripping
	base = name.split(".")[0]
	suffix = name[len(base):] if len(name) > len(base) else ""
	if base in _KERN_GROUP_MAP:
		lg, rg = _KERN_GROUP_MAP[base]
		return lg, rg, "suffix_strip"

	# For .smcp / .c2sc: try uppercase equivalent
	if suffix in (".smcp", ".c2sc") and len(base) == 1:
		uc_base = base.upper()
		if uc_base in _KERN_GROUP_MAP:
			lg, rg = _KERN_GROUP_MAP[uc_base]
			return lg, rg, "smcp"

	# 3. Component inheritance
	# Find first layer that has components
	layer = glyph.layers[str(master.id)] if master else (glyph.layers[0] if glyph.layers else None)
	if layer and layer.components:
		comp = layer.components[0]
		comp_name = str(comp.componentName)
		comp_glyph = font.glyphs[comp_name]
		if comp_glyph:
			# Recurse on the component base (but only one level to avoid loops)
			if comp_name in _KERN_GROUP_MAP:
				lg, rg = _KERN_GROUP_MAP[comp_name]
				return lg, rg, "component"
			comp_base = comp_name.split(".")[0]
			if comp_base in _KERN_GROUP_MAP:
				lg, rg = _KERN_GROUP_MAP[comp_base]
				return lg, rg, "component"

	# 4. Unicode decomposition
	u = glyph.unicode
	if u:
		try:
			import unicodedata
			char = chr(int(u, 16))
			decomp = unicodedata.decomposition(char)
			if decomp:
				base_cp = decomp.split()[0]
				if not base_cp.startswith("<"):
					base_char = chr(int(base_cp, 16))
					if base_char in _KERN_GROUP_MAP:
						lg, rg = _KERN_GROUP_MAP[base_char]
						return lg, rg, "unicode_decomp"
		except (ValueError, TypeError):
			pass

	# 5. Contour analysis fallback
	case = _glyph_case_group(glyph)
	zone_top = master.capHeight if case == "UC" else master.xHeight
	if layer:
		try:
			decomposed = layer.copyDecomposedLayer()
		except Exception:
			decomposed = layer
		left_type = _infer_side_type_kern(decomposed, "left", zone_top)
		right_type = _infer_side_type_kern(decomposed, "right", zone_top)
	else:
		left_type = "open"
		right_type = "open"

	lg = _SIDE_FALLBACK_LEFT.get((case, left_type), base if len(base) == 1 else "H")
	rg = _SIDE_FALLBACK_RIGHT.get((case, right_type), base if len(base) == 1 else "H")
	return lg, rg, "contour_analysis"


@route("POST", "/api/font/kerning/groups/analyze")
def handle_analyze_kerning_groups(bridge, body=None, **kwargs):
	"""Analyze and optionally assign kerning groups to glyphs.

	Uses dictionary + component inheritance + contour analysis.
	"""
	if not body:
		body = {}

	glyph_names = body.get("glyphNames")  # None = all Letter/Number glyphs
	apply_groups = body.get("apply", True)
	overwrite = body.get("overwrite", True)

	def _run():
		from GlyphsApp import Glyphs
		font = _require_font()
		master = font.selectedFontMaster

		# Determine which glyphs to process
		if glyph_names:
			glyphs = [font.glyphs[n] for n in glyph_names if font.glyphs[n]]
		else:
			glyphs = [g for g in font.glyphs if g.category in ("Letter", "Number", "Punctuation")]

		results = []
		stats = {
			"total": 0,
			"would_change_left": 0,
			"would_change_right": 0,
			"already_match": 0,
			"skipped_existing": 0,
			"applied_left": 0,
			"applied_right": 0,
			"by_method": {},
		}

		for glyph in glyphs:
			name = str(glyph.name)
			current_left = str(glyph.leftKerningGroup) if glyph.leftKerningGroup else ""
			current_right = str(glyph.rightKerningGroup) if glyph.rightKerningGroup else ""

			proposed_left, proposed_right, method = _resolve_kern_groups(glyph, font, master)

			stats["total"] += 1
			stats["by_method"][method] = stats["by_method"].get(method, 0) + 1

			left_matches = current_left == proposed_left
			right_matches = current_right == proposed_right

			left_action = "match"
			right_action = "match"

			if not left_matches:
				if current_left and not overwrite:
					left_action = "skip_existing"
					stats["skipped_existing"] += 1
				else:
					left_action = "change"
					stats["would_change_left"] += 1
					if apply_groups:
						glyph.leftKerningGroup = proposed_left
						stats["applied_left"] += 1
			else:
				stats["already_match"] += 1

			if not right_matches:
				if current_right and not overwrite:
					right_action = "skip_existing"
					stats["skipped_existing"] += 1
				else:
					right_action = "change"
					stats["would_change_right"] += 1
					if apply_groups:
						glyph.rightKerningGroup = proposed_right
						stats["applied_right"] += 1
			else:
				stats["already_match"] += 1

			# Only include in results if something is notable
			if not (left_matches and right_matches):
				results.append({
					"glyph": name,
					"currentLeft": current_left,
					"currentRight": current_right,
					"proposedLeft": proposed_left,
					"proposedRight": proposed_right,
					"leftAction": left_action,
					"rightAction": right_action,
					"method": method,
				})

		# Color marking (dry run or apply)
		for r in results:
			g = font.glyphs[r["glyph"]]
			if not g:
				continue
			if r["leftAction"] == "change" or r["rightAction"] == "change":
				if apply_groups:
					g.color = 4  # green = applied
				else:
					g.color = 3  # yellow = proposed change
			elif r["leftAction"] == "skip_existing" or r["rightAction"] == "skip_existing":
				g.color = 1  # orange = has existing, skipped

		# Build group summary
		group_summary = {"left": {}, "right": {}}
		for glyph in glyphs:
			lg = str(glyph.leftKerningGroup) if glyph.leftKerningGroup else ""
			rg = str(glyph.rightKerningGroup) if glyph.rightKerningGroup else ""
			if lg:
				group_summary["left"].setdefault(lg, []).append(str(glyph.name))
			if rg:
				group_summary["right"].setdefault(rg, []).append(str(glyph.name))

		return {
			"ok": True,
			"applied": apply_groups,
			"overwrite": overwrite,
			"stats": stats,
			"changes": results,
			"changeCount": len(results),
			"groupSummary": group_summary,
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


def _measure_ref_stems(font, master_id):
	"""Measure V-stem and H-stem on reference glyphs.

	V-stems: H (UC), n (LC) — horizontal rays in the mid-zone
	H-stems: H crossbar (UC), o top/bottom (LC) — vertical rays at center

	Uses horizontal rays for V-stems and vertical rays for H-stems.
	More reliable than perpendicular ray-casting which picks up arch
	thickness instead of stem width at heavy weights.

	Returns dict: {"uc_v": int, "uc_h": int, "lc_v": int, "lc_h": int}
	All values are dominant stem thickness in units, or None if unmeasurable.
	"""
	result = {}

	master = None
	for m in font.masters:
		if m.id == master_id:
			master = m
			break
	if master is None:
		master = font.masters[0]

	cap_h = int(master.capHeight) if master.capHeight else 700
	x_h = int(master.xHeight) if master.xHeight else 500

	# ── V-stems: H (UC), n (LC) via horizontal rays ──
	v_refs = {"uc": ("H", cap_h), "lc": ("n", x_h)}
	for case, (gname, zone_h) in v_refs.items():
		glyph = font.glyphs[gname]
		if glyph is None:
			result[f"{case}_v"] = None
			continue
		layer = glyph.layers[master_id]
		if layer is None:
			result[f"{case}_v"] = None
			continue
		clean = layer.copyDecomposedLayer()
		clean.removeOverlap()
		if len(clean.paths) == 0:
			result[f"{case}_v"] = None
			continue

		y_positions = [zone_h * f for f in [0.3, 0.4, 0.5, 0.6, 0.7]]
		h_meas = _measure_stems_horizontal(clean, y_positions)
		v_thicknesses = []
		for md in h_meas:
			for s in md["stems"]:
				t = s["thickness"]
				if t < float(clean.width) * 0.6:
					v_thicknesses.append(t)
		result[f"{case}_v"] = _find_dominant_stem(v_thicknesses, strategy="thickest") if v_thicknesses else None

	# ── H-stems: H crossbar (UC), o top/bottom (LC) via vertical rays ──
	h_refs = {"uc": ("H", cap_h), "lc": ("o", x_h)}
	for case, (gname, zone_h) in h_refs.items():
		glyph = font.glyphs[gname]
		if glyph is None:
			result[f"{case}_h"] = None
			continue
		layer = glyph.layers[master_id]
		if layer is None:
			result[f"{case}_h"] = None
			continue
		clean = layer.copyDecomposedLayer()
		clean.removeOverlap()
		if len(clean.paths) == 0:
			result[f"{case}_h"] = None
			continue

		# Single vertical ray at center — off-center positions on thick
		# weights have no visible counter, returning full glyph height
		x_center = float(clean.width) * 0.5
		# y range generous to avoid clipping overshoots
		v_meas = _measure_stems_vertical(clean, [x_center], y_min=-50, y_max=zone_h + 100)
		h_thicknesses = []
		for md in v_meas:
			for s in md["stems"]:
				t = s["thickness"]
				if t < zone_h * 0.5:
					h_thicknesses.append(t)
		# Pick thinnest — actual H-stems are always thinner than residual
		# full-height segments or counter measurements
		if h_thicknesses:
			result[f"{case}_h"] = int(round(min(h_thicknesses)))
		else:
			result[f"{case}_h"] = None

	return result


def _measure_diagonal_angle(layer):
	"""Measure the average angle of diagonal strokes in a glyph.

	Finds the steepest long segments (>30% of bounds height) and returns
	their average angle from vertical in degrees.  Used to calculate the
	OffsetCurve compensation factor for diagonal glyphs.

	Returns angle in degrees from vertical (0 = vertical, 45 = 45°),
	or None if no diagonal segments found.
	"""
	import math
	bounds = layer.bounds
	if bounds.size.height < 10:
		return None

	min_seg_len = bounds.size.height * 0.3
	angles = []

	for path in layer.paths:
		nodes = list(path.nodes)
		nn = len(nodes)
		for i in range(nn):
			n0 = nodes[i]
			n1 = nodes[(i + 1) % nn]
			# Only on-curve to on-curve segments
			if n0.type == "offcurve" or n1.type == "offcurve":
				continue
			dx = float(n1.position.x) - float(n0.position.x)
			dy = float(n1.position.y) - float(n0.position.y)
			seg_len = math.sqrt(dx * dx + dy * dy)
			if seg_len < min_seg_len:
				continue
			# Angle from vertical (0° = vertical, 90° = horizontal)
			angle_from_vert = abs(math.degrees(math.atan2(abs(dx), abs(dy))))
			# Only count diagonal segments (15°-75° from vertical)
			if 15 < angle_from_vert < 75:
				angles.append(angle_from_vert)

	if not angles:
		return None
	return sum(angles) / len(angles)


@route("POST", "/api/font/smart-scale")
def handle_smart_scale(bridge, body=None, **kwargs):
	"""Scale glyphs with automatic weight compensation.

	Measures reference stems before scaling, applies the transform, then uses
	GlyphsFilterOffsetCurve with separate X/Y offsets to restore stem weights.

	Parameters:
	  glyphNames: list of glyph names (empty = all exporting glyphs)
	  masterId: master ID (empty = all masters)
	  width: horizontal scale factor (1.0 = no change, 0.97 = 3% narrower)
	  height: vertical scale factor (1.0 = no change, 1.15 = 15% taller)
	  weight: target weight factor (1.0 = maintain original stems, 0.9 = 10% thinner)
	  proportional: if true, height follows width
	  backup: create backup layer before modifying (default true)
	"""
	if not body:
		return 400, {"error": "Body required"}

	sx = float(body.get("width", 1.0))
	sy = float(body.get("height", 1.0))
	weight = float(body.get("weight", 1.0))
	proportional = bool(body.get("proportional", False))
	backup = bool(body.get("backup", True))
	master_id = body.get("masterId", None)
	glyph_names = body.get("glyphNames", [])

	if proportional:
		sy = sx

	if sx == 1.0 and sy == 1.0 and weight == 1.0:
		return 400, {"error": "Nothing to do — all scale factors are 1.0"}

	def _run():
		import math
		import objc
		from Foundation import NSAffineTransform, NSPoint

		font = _require_font()

		# Determine masters to process
		if master_id:
			masters = [font.fontMasterForId_(master_id)] if hasattr(font, 'fontMasterForId_') else [font.masters[0]]
			masters = [m for m in masters if m is not None]
		else:
			masters = list(font.masters)

		if not masters:
			return {"error": "No valid masters found"}

		# Determine glyphs to process
		if not glyph_names:
			names = [g.name for g in font.glyphs if g.export]
		else:
			names = list(glyph_names)

		# Get offset curve filter
		OffsetCurve = None
		try:
			OffsetCurve = objc.lookUpClass('GlyphsFilterOffsetCurve')
		except:
			pass

		per_master = {}

		font.disableUpdateInterface()
		try:
			for master in masters:
				mid = master.id
				mname = str(master.name)

				# 1. Measure reference stems BEFORE scaling
				ref_before = _measure_ref_stems(font, mid)
				print(f"[SmartScale] {mname} refs before: {ref_before}")

				processed = []
				skipped = []
				diagonal_deferred = []  # scale these AFTER OffsetCurve

				# Diagonal-dominant glyphs: OffsetCurve X+Y compounds
				# along the stroke normal, causing over-compensation.
				# These get scaled with the effective Y factor (matching
				# the actual xHeight change) instead of raw sy.
				_DIAGONAL_GLYPHS = {
					"v", "w", "x", "y", "z", "k",
					"V", "W", "X", "Y", "Z", "K",
				}

				# Record old xHeight for diagonal correction later
				old_xHeight = float(master.xHeight) if master.xHeight else 500.0

				for gn in names:
					glyph = font.glyphs[gn]
					if glyph is None:
						skipped.append(gn)
						continue

					layer = glyph.layers[mid]
					if layer is None:
						skipped.append(gn)
						continue

					# Skip empty layers (no paths and no components)
					has_paths = len(layer.paths) > 0
					has_components = len(layer.components) > 0
					if not has_paths and not has_components:
						skipped.append(gn)
						continue

					# Backup
					if backup:
						bk = layer.copy()
						bk.name = "Pre-SmartScale " + mname
						bk.associatedMasterId = mid
						glyph.layers.append(bk)

					# Defer diagonal glyphs — scale them after OffsetCurve
					if gn in _DIAGONAL_GLYPHS:
						diagonal_deferred.append(gn)
						continue

					# Store original metrics (pre-scale, to restore after OffsetCurve)
					orig_width = float(layer.width)
					orig_lsb = float(layer.LSB)
					orig_rsb = float(layer.RSB)

					# 2. Apply transform (non-diagonal glyphs)
					layer.beginChanges()

					t = NSAffineTransform.alloc().init()
					t.scaleXBy_yBy_(sx, sy)
					layer.transform_(t)
					layer.width = round(orig_width * sx)

					layer.endChanges()

					processed.append({
						"name": gn,
						"widthBefore": orig_width,
						"widthAfter": float(layer.width),
						"origLSB": orig_lsb,
						"origRSB": orig_rsb,
					})

				# 3. Calculate compensation offsets per case using THEORETICAL delta
				# Measuring post-scale stems is unreliable at thin weights due to
				# coordinate rounding noise. Instead, calculate the expected change:
				#   V-stems scale by sx → need offsetX = stem * (weight - sx) / 2
				#   H-stems scale by sy → need offsetY = stem * (weight - sy) / 2
				offsets = {}  # case -> (offset_x, offset_y)
				for case in ["lc", "uc"]:
					v_b = ref_before.get(f"{case}_v")
					h_b = ref_before.get(f"{case}_h")
					ox = 0.0
					oy = 0.0
					if v_b:
						ox = v_b * (weight - sx) / 2.0
					if h_b:
						oy = h_b * (weight - sy) / 2.0
					offsets[case] = (ox, oy)

				any_compensation = any(
					abs(ox) > 0.3 or abs(oy) > 0.3
					for ox, oy in offsets.values()
				)
				compensation_applied = False

				if any_compensation and OffsetCurve is not None:
					print(f"[SmartScale] {mname} offsets: lc={offsets.get('lc')}, uc={offsets.get('uc')}")

					oc = OffsetCurve.alloc().init()

					for info in processed:
						gn = info["name"]
						glyph = font.glyphs[gn]
						layer = glyph.layers[mid]

						# Pick offsets based on glyph case
						glyph_class = _classify_glyph(glyph)
						if glyph_class == "uppercase" or glyph_class == "figure":
							g_offset_x, g_offset_y = offsets.get("uc", (0, 0))
						else:
							g_offset_x, g_offset_y = offsets.get("lc", (0, 0))

						if abs(g_offset_x) < 0.3 and abs(g_offset_y) < 0.3:
							continue

						# Only compensate V-stems (X offset). H-stems scale
						# naturally with sy, matching Tuner behavior. Applying
						# OffsetCurve Y causes height contraction (position=0.5
						# contracts both top and bottom edges).
						layer.beginChanges()
						try:
							if abs(g_offset_x) >= 0.3:
								oc.processLayer_withArguments_(layer, [
									"GlyphsFilterOffsetCurve",
									str(round(g_offset_x, 1)),
									"0",   # no Y compensation
									"0",   # makeStroke = no
									"0.5"  # position = center
								])
						except Exception as e:
							print(f"[SmartScale] Offset failed for {gn}: {e}")

						layer.endChanges()

						# Restore ORIGINAL sidebearings (pre-scale values)
						# This preserves the designer's intended spacing.
						# Width adjusts automatically: width = LSB + paths + RSB
						orig_lsb = info.get("origLSB", float(layer.LSB))
						orig_rsb = info.get("origRSB", float(layer.RSB))
						layer.LSB = round(orig_lsb)
						layer.RSB = round(orig_rsb)
						info["widthAfter"] = float(layer.width)

						info["offsetX"] = round(g_offset_x, 1)

					compensation_applied = True
				else:
					# No OffsetCurve needed, but still restore original sidebearings
					for info in processed:
						gn = info["name"]
						layer = font.glyphs[gn].layers[mid]
						orig_lsb = info.get("origLSB")
						orig_rsb = info.get("origRSB")
						if orig_lsb is not None and orig_rsb is not None:
							layer.LSB = round(orig_lsb)
							layer.RSB = round(orig_rsb)
							info["widthAfter"] = float(layer.width)

				# 4.5. Scale diagonal glyphs + angle-compensated OffsetCurve
				# Diagonals are scaled like non-diagonals (sx, sy) but use
				# a single OffsetCurve X pass with a compensation factor
				# based on the diagonal angle: factor = 1/cos(angle).
				# This correctly compensates stroke weight on any diagonal.
				if diagonal_deferred:
					# Measure effective sy from actual xHeight change
					effective_sy = sy
					if sy != 1.0:
						n_glyph = font.glyphs["n"]
						new_xHeight = old_xHeight * sy  # fallback
						if n_glyph:
							n_layer = n_glyph.layers[mid]
							if n_layer:
								n_w = float(n_layer.width)
								stem_top = 0.0
								for path in n_layer.paths:
									for node in path.nodes:
										if node.type in ("line", "curve"):
											if float(node.position.x) < n_w * 0.25 and float(node.position.y) > stem_top:
												stem_top = float(node.position.y)
								if stem_top > 0:
									new_xHeight = stem_top
						effective_sy = new_xHeight / old_xHeight
						print(f"[SmartScale] {mname} diagonal: effective_sy={effective_sy:.4f} (xH {old_xHeight:.0f} -> {new_xHeight:.0f})")

					for gn in diagonal_deferred:
						glyph = font.glyphs[gn]
						layer = glyph.layers[mid]
						orig_width = float(layer.width)
						orig_lsb_d = float(layer.LSB)
						orig_rsb_d = float(layer.RSB)

						# Ascender diags (k/K): use sy=1.0 if capHeight unchanged
						is_ascender_diag = gn in ("k", "K")
						this_sy = effective_sy
						if is_ascender_diag:
							this_sy = 1.0 if abs(sy - 1.0) < 0.001 else effective_sy

						# Scale
						layer.beginChanges()
						t = NSAffineTransform.alloc().init()
						t.scaleXBy_yBy_(sx, this_sy)
						layer.transform_(t)
						layer.width = round(orig_width * sx)
						layer.endChanges()

						# Measure diagonal angle for compensation factor
						diag_angle = _measure_diagonal_angle(layer)
						diag_factor = 1.0
						if diag_angle is not None:
							# factor = 1/cos(angle from vertical)
							diag_factor = 1.0 / max(0.5, math.cos(math.radians(diag_angle)))

						# Pick case-appropriate V-stem reference
						glyph_class = _classify_glyph(glyph)
						if glyph_class == "uppercase":
							base_offset_x = offsets.get("uc", (0, 0))[0]
						else:
							base_offset_x = offsets.get("lc", (0, 0))[0]

						# Apply single OffsetCurve X pass with diagonal factor
						diag_offset_x = base_offset_x * diag_factor
						if abs(diag_offset_x) >= 0.3 and OffsetCurve is not None:
							oc = OffsetCurve.alloc().init()
							oc.processLayer_withArguments_(layer, [
								"GlyphsFilterOffsetCurve",
								str(round(diag_offset_x, 2)),
								"0", "0", "0.5"
							])
							print(f"[SmartScale] {gn}: angle={diag_angle:.1f}° factor={diag_factor:.3f} offsetX={diag_offset_x:.1f}")

						# Restore original sidebearings (pre-scale)
						layer.LSB = round(orig_lsb_d)
						layer.RSB = round(orig_rsb_d)

						processed.append({
							"name": gn,
							"widthBefore": orig_width,
							"widthAfter": float(layer.width),
							"effectiveSy": round(this_sy, 4),
							"diagAngle": round(diag_angle, 1) if diag_angle else None,
							"diagFactor": round(diag_factor, 3),
							"diagOffsetX": round(diag_offset_x, 1),
						})

				# 5. Round all coordinates
				for info in processed:
					gn = info["name"]
					layer = font.glyphs[gn].layers[mid]
					for path in layer.paths:
						for node in path.nodes:
							node.position = NSPoint(
								round(float(node.position.x)),
								round(float(node.position.y))
							)

				# 6. Update xHeight metric if LC was scaled vertically
				if sy != 1.0:
					n_glyph = font.glyphs["n"]
					if n_glyph:
						n_layer = n_glyph.layers[mid]
						if n_layer:
							# xHeight = top of LEFT STEM (flat), not arch
							# The arch overshoots xHeight by ~10-15u
							n_w = float(n_layer.width)
							stem_top = 0.0
							for path in n_layer.paths:
								for node in path.nodes:
									if node.type in ("line", "curve"):
										nx = float(node.position.x)
										ny = float(node.position.y)
										if nx < n_w * 0.25 and ny > stem_top:
											stem_top = ny
							if stem_top > 0:
								old_xh = master.xHeight
								master.xHeight = int(round(stem_top))
								print(f"[SmartScale] {mname}: xHeight {old_xh} -> {master.xHeight}")

				# 7. Measure refs after compensation to report final state
				ref_final = _measure_ref_stems(font, mid)

				per_master[mid] = {
					"masterName": mname,
					"refBefore": ref_before,
					"refFinal": ref_final,
					"compensationApplied": compensation_applied,
					"offsets": {k: {"x": round(v[0], 1), "y": round(v[1], 1)} for k, v in offsets.items()},
					"processed": len(processed),
					"skipped": len(skipped),
					"glyphs": processed[:20],  # limit response size
				}

		finally:
			font.enableUpdateInterface()

		return {
			"ok": True,
			"params": {
				"width": sx,
				"height": sy,
				"weight": weight,
				"proportional": proportional,
			},
			"masters": per_master,
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 400, result
	return 200, result


_AUTO_KERN_CRITICAL = [
	("A","V"),("A","W"),("A","T"),("A","Y"),("A","C"),("A","G"),("A","O"),("A","Q"),("A","U"),
	("F","A"),("F","a"),("F","e"),("F","o"),
	("L","T"),("L","V"),("L","W"),("L","Y"),
	("P","A"),("P","a"),("P","e"),("P","o"),("P","period"),("P","comma"),
	("T","A"),("T","a"),("T","e"),("T","i"),("T","o"),("T","r"),("T","u"),("T","w"),("T","y"),("T","hyphen"),("T","period"),("T","comma"),
	("V","A"),("V","a"),("V","e"),("V","o"),("V","i"),("V","u"),
	("W","A"),("W","a"),("W","e"),("W","o"),("W","i"),
	("Y","A"),("Y","a"),("Y","e"),("Y","i"),("Y","o"),("Y","u"),("Y","hyphen"),("Y","period"),("Y","comma"),
	("f","a"),("f","e"),("f","i"),("f","o"),
	("r","a"),("r","e"),("r","o"),("r","period"),("r","comma"),
	("v","a"),("v","e"),("v","o"),
	("w","a"),("w","e"),("w","o"),
	("y","a"),("y","e"),("y","o"),("y","period"),("y","comma"),
	("g","y"),("o","y"),("q","u"),
]


def _optical_weight(y, x_height, factor=1.25):
	"""Trapezoidal optical weight at height y (MekkaBlue/HT LetterSpacer).
	Full weight baseline→xHeight, linear taper in descender/ascender zones."""
	if x_height <= 0:
		return factor
	if 0 <= y <= x_height:
		return factor
	elif y < 0:
		t = 1.0 + (2.0 * y / x_height)
		return max(0.0, t) * factor
	else:
		t = 1.0 - (y - x_height) / x_height
		return max(0.0, t) * factor


def _gap_at_height(left_layer, right_layer, y):
	"""Measure the gap between two layers at a given y height.
	Returns (rsb_left, lsb_right) or None if either has no ink."""
	from Foundation import NSPoint
	# RSB of left layer
	p1 = NSPoint(-50, y)
	p2 = NSPoint(float(left_layer.width) + 50, y)
	raw = left_layer.intersectionsBetweenPoints(p1, p2)
	if not raw or len(raw) < 2:
		return None
	xs = sorted(float(p.x) for p in raw)
	rightmost = xs[-2] if len(xs) > 2 else xs[-1]  # skip endpoint
	# Filter: only points within the glyph bounds
	ink_xs = [x for x in xs if -1 < x < float(left_layer.width) + 1]
	if not ink_xs:
		return None
	rsb = float(left_layer.width) - max(ink_xs)

	# LSB of right layer
	p1r = NSPoint(-50, y)
	p2r = NSPoint(float(right_layer.width) + 50, y)
	raw_r = right_layer.intersectionsBetweenPoints(p1r, p2r)
	if not raw_r or len(raw_r) < 2:
		return None
	xs_r = sorted(float(p.x) for p in raw_r)
	ink_xs_r = [x for x in xs_r if -1 < x < float(right_layer.width) + 1]
	if not ink_xs_r:
		return None
	lsb = min(ink_xs_r)

	return rsb, lsb


def _calculate_optical_kern(left_layer, right_layer, target_area, step, depth, x_height, factor=1.25, left_bounds=None, right_bounds=None):
	"""Calculate optimal kern value for a glyph pair using optical area method.
	Pass left_bounds/right_bounds from original layers if using decomposed copies.
	Returns int kern value or None."""
	lb = left_bounds or left_layer.bounds
	rb = right_bounds or right_layer.bounds
	if not lb or not rb or lb.size.height == 0 or rb.size.height == 0:
		return None

	bottom_y = max(int(lb.origin.y), int(rb.origin.y))
	top_y = min(int(lb.origin.y + lb.size.height), int(rb.origin.y + rb.size.height))
	if top_y <= bottom_y:
		return 0

	weighted_gap_sum = 0.0
	total_weight = 0.0

	y = bottom_y
	while y <= top_y:
		gap = _gap_at_height(left_layer, right_layer, float(y))
		if gap is not None:
			rsb, lsb = gap
			rsb_c = min(rsb, depth)
			lsb_c = min(lsb, depth)
			w = _optical_weight(float(y), float(x_height), factor)
			weighted_gap_sum += w * (rsb_c + lsb_c)
			total_weight += w
		y += step

	if total_weight == 0:
		return 0

	kern = (target_area / step - weighted_gap_sum) / total_weight
	return int(round(kern))


def _measure_pair_area(left_layer, right_layer, step, depth, x_height, factor=1.25, left_bounds=None, right_bounds=None):
	"""Measure the current optical gap area between two layers (for calibration)."""
	lb = left_bounds or left_layer.bounds
	rb = right_bounds or right_layer.bounds
	if not lb or not rb or lb.size.height == 0 or rb.size.height == 0:
		return None

	bottom_y = max(int(lb.origin.y), int(rb.origin.y))
	top_y = min(int(lb.origin.y + lb.size.height), int(rb.origin.y + rb.size.height))
	if top_y <= bottom_y:
		return None

	weighted_gap_sum = 0.0
	y = bottom_y
	while y <= top_y:
		gap = _gap_at_height(left_layer, right_layer, float(y))
		if gap is not None:
			rsb, lsb = gap
			rsb_c = min(rsb, depth)
			lsb_c = min(lsb, depth)
			w = _optical_weight(float(y), float(x_height), factor)
			weighted_gap_sum += w * (rsb_c + lsb_c)
		y += step

	return weighted_gap_sum * step


@route("POST", "/api/font/kerning/auto")
def handle_auto_kern(bridge, body=None, **kwargs):
	"""Auto-kern glyph pairs using optical gap analysis (MB LetterKerner algorithm)."""
	if not body:
		body = {}

	pairs_mode = str(body.get("pairs", "critical"))
	pairs_list = body.get("pairsList", [])
	user_area = body.get("area", None)
	step = int(body.get("step", 5))
	depth = int(body.get("depth", 200))
	factor = float(body.get("factor", 1.25))
	rounding = int(body.get("rounding", 5))
	threshold = int(body.get("threshold", 3))
	use_groups = bool(body.get("useGroups", True))
	overwrite = bool(body.get("overwrite", False))
	dry_run = bool(body.get("dryRun", False))
	master_id = body.get("masterId", None)

	def _run():
		from GlyphsApp import Glyphs
		font = _require_font()

		if master_id:
			masters = [m for m in font.masters if str(m.id) == master_id]
			if not masters:
				raise ValueError(f"Master '{master_id}' not found")
		else:
			masters = list(font.masters)

		# ── Generate pair list ─────────────────────────────────
		if pairs_mode == "explicit":
			glyph_pairs = [(str(p[0]), str(p[1])) for p in pairs_list if len(p) >= 2]
		elif pairs_mode == "critical":
			glyph_pairs = [(l, r) for l, r in _AUTO_KERN_CRITICAL
				if font.glyphs[l] and font.glyphs[r]]
		elif pairs_mode == "auto":
			# Generate representative pairs per kerning group combination
			export_letters = [g for g in font.glyphs
				if g.export and (g.category or "") in ("Letter", "Number", "Punctuation")]
			# Group by kerning keys
			left_groups = {}  # rightKerningGroup → representative glyph
			right_groups = {}  # leftKerningGroup → representative glyph
			for g in export_letters:
				rkg = str(g.rightKerningGroup or g.name)
				lkg = str(g.leftKerningGroup or g.name)
				if rkg not in left_groups or len(g.name) < len(left_groups[rkg]):
					left_groups[rkg] = g.name
				if lkg not in right_groups or len(g.name) < len(right_groups[lkg]):
					right_groups[lkg] = g.name
			# Cross all left × right group representatives
			glyph_pairs = []
			for l_name in left_groups.values():
				for r_name in right_groups.values():
					glyph_pairs.append((l_name, r_name))
			# Cap to avoid timeout
			if len(glyph_pairs) > 800:
				glyph_pairs = glyph_pairs[:800]
		else:
			raise ValueError(f"Unknown pairs mode: {pairs_mode}")

		if not glyph_pairs:
			return {"ok": True, "dryRun": dry_run, "masters": {},
				"message": "No valid pairs to process"}

		per_master = {}
		font.disableUpdateInterface()
		try:
			for master in masters:
				mid = str(master.id)
				mname = str(master.name)
				x_height = float(master.xHeight)

				# ── Calibration ────────────────────────────────
				calibration = {}
				lc_area = user_area
				uc_area = user_area

				if user_area is None:
					# Calibrate from nn (LC) and HH (UC)
					# _measure_pair_area returns weightedGapSum * step (= the "area")
					# _calculate_optical_kern expects target_area in this form
					# (it internally does target_area / step to recover weightedGapSum)
					n_g = font.glyphs["n"]
					h_g = font.glyphs["H"]
					if n_g:
						nl = n_g.layers[mid]
						a = _measure_pair_area(nl, nl, step, depth, x_height, factor)
						if a is not None:
							lc_area = a  # pass raw area — _calculate_optical_kern divides by step
							calibration["lc_area"] = round(a, 1)
							calibration["lc_ref"] = "nn"
					if h_g:
						hl = h_g.layers[mid]
						a = _measure_pair_area(hl, hl, step, depth, x_height, factor)
						if a is not None:
							uc_area = a  # pass raw area
							calibration["uc_area"] = round(a, 1)
							calibration["uc_ref"] = "HH"

					if lc_area is None and uc_area is not None:
						lc_area = uc_area
					elif uc_area is None and lc_area is not None:
						uc_area = lc_area

					# Mixed: geometric mean
					if lc_area is not None and uc_area is not None:
						import math
						calibration["mixed_area"] = round(
							math.sqrt(calibration.get("lc_area", 0) * calibration.get("uc_area", 0)), 1)

				if lc_area is None and uc_area is None:
					per_master[mid] = {"masterName": mname,
						"error": "Could not calibrate — n and H glyphs missing"}
					continue

				# ── Process pairs ──────────────────────────────
				results = []
				group_done = set()
				upm = int(font.upm)
				max_kern = upm // 3

				for left_name, right_name in glyph_pairs:
					lg = font.glyphs[left_name]
					rg = font.glyphs[right_name]
					if not lg or not rg:
						continue

					# Kerning keys
					if use_groups:
						l_key = "@MMK_L_" + str(lg.rightKerningGroup) if lg.rightKerningGroup else lg.name
						r_key = "@MMK_R_" + str(rg.leftKerningGroup) if rg.leftKerningGroup else rg.name
					else:
						l_key = lg.name
						r_key = rg.name

					# Deduplicate by group pair
					pair_id = (l_key, r_key)
					if pair_id in group_done:
						continue
					group_done.add(pair_id)

					# Check existing kerning
					if not overwrite:
						kern_dict = font.kerning.get(mid, {})
						if kern_dict and l_key in kern_dict and r_key in kern_dict.get(l_key, {}):
							results.append({"left": left_name, "right": right_name,
								"leftKey": l_key, "rightKey": r_key,
								"value": 0, "rawValue": 0,
								"applied": False, "reason": "existing"})
							continue

					# Get layers
					ll = lg.layers[mid]
					rl = rg.layers[mid]
					if not ll.paths and not ll.components:
						continue
					if not rl.paths and not rl.components:
						continue

					# Save original bounds before decomposing (decomposed copies lose parent → bounds=0)
					ll_bounds = ll.bounds
					rl_bounds = rl.bounds

					# Use decomposed copies for intersection measurement
					ll_m = ll.copyDecomposedLayer()
					rl_m = rl.copyDecomposedLayer()

					# Pick target area based on case
					lc = _classify_glyph(lg)
					rc = _classify_glyph(rg)
					if lc == "uppercase" and rc == "uppercase":
						tgt = uc_area
					elif lc == "lowercase" and rc == "lowercase":
						tgt = lc_area
					else:
						# Mixed: geometric mean
						if lc_area is not None and uc_area is not None:
							import math
							tgt = math.sqrt(lc_area * uc_area)
						else:
							tgt = lc_area or uc_area

					if tgt is None:
						continue

					# Calculate kern (pass original bounds)
					raw = _calculate_optical_kern(ll_m, rl_m, tgt, step, depth, x_height, factor,
						left_bounds=ll_bounds, right_bounds=rl_bounds)
					if raw is None:
						results.append({"left": left_name, "right": right_name,
							"leftKey": l_key, "rightKey": r_key,
							"value": 0, "rawValue": 0,
							"applied": False, "reason": "unmeasurable"})
						continue

					# Clamp
					raw = max(-max_kern, min(max_kern, raw))

					# Round
					if rounding > 1:
						rounded = rounding * round(raw / rounding)
					else:
						rounded = raw

					# Threshold
					if abs(rounded) < threshold:
						results.append({"left": left_name, "right": right_name,
							"leftKey": l_key, "rightKey": r_key,
							"value": rounded, "rawValue": round(float(raw), 1),
							"applied": False, "reason": "below_threshold"})
						continue

					# Apply
					if not dry_run:
						font.setKerningForPair(mid, l_key, r_key, float(rounded))

					results.append({"left": left_name, "right": right_name,
						"leftKey": l_key, "rightKey": r_key,
						"value": int(rounded), "rawValue": round(float(raw), 1),
						"applied": not dry_run, "reason": None})

				# ── Summary ────────────────────────────────────
				kerned = [r for r in results if r.get("applied") or (dry_run and r.get("reason") is None)]
				kern_vals = [r["value"] for r in kerned] if kerned else [0]

				per_master[mid] = {
					"masterName": mname,
					"calibration": calibration,
					"pairsProcessed": len(results),
					"pairsKerned": len(kerned),
					"pairsSkipped": len(results) - len(kerned),
					"results": results[:300],
					"summary": {
						"negative": sum(1 for v in kern_vals if v < 0),
						"positive": sum(1 for v in kern_vals if v > 0),
						"zero": sum(1 for v in kern_vals if v == 0),
						"minValue": min(kern_vals) if kern_vals else 0,
						"maxValue": max(kern_vals) if kern_vals else 0,
						"avgValue": round(sum(kern_vals) / len(kern_vals), 1) if kern_vals else 0,
					},
				}
		finally:
			font.enableUpdateInterface()

		return {
			"ok": True, "dryRun": dry_run,
			"params": {"step": step, "depth": depth, "rounding": rounding,
				"threshold": threshold, "useGroups": use_groups, "overwrite": overwrite},
			"masters": per_master,
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 500, {"ok": False, "error": result["error"]}
	return 200, result


_PS_NAME_RE = __import__("re").compile(r'^[A-Za-z_.][A-Za-z0-9_.]*$')


_ESSENTIAL_GLYPHS = [
	("NULL", ".null"), ("CR", "nonmarkingreturn"), ("nbspace",),
	("Euro",), ("softhyphen",),
]


_STANDARD_WEIGHT_CLASSES = {100, 200, 300, 400, 500, 600, 700, 800, 900}


_CRITICAL_PAIRS_PROD = [
	("A","V"),("A","W"),("A","T"),("A","Y"),("A","G"),("A","O"),("A","Q"),
	("F","A"),("F","O"),("L","T"),("L","V"),("L","Y"),("O","A"),("P","A"),
	("T","A"),("T","O"),("V","A"),("V","O"),("W","A"),("W","O"),("Y","A"),("Y","O"),
	("A","v"),("A","w"),("F","a"),("F","e"),("F","o"),("T","a"),("T","e"),
	("T","i"),("T","o"),("T","r"),("T","u"),("T","w"),("V","a"),("V","e"),
	("V","o"),("W","a"),("W","e"),("Y","a"),("Y","e"),("Y","o"),
	("f","a"),("f","e"),("f","i"),("f","o"),("r","v"),("r","y"),
	("v","a"),("v","e"),("v","o"),("w","a"),("w","e"),("w","o"),
	("y","a"),("y","e"),("y","o"),
]


@route("POST", "/api/font/production/review")
def handle_production_review(bridge, body=None, **kwargs):
	"""Run a comprehensive production readiness review on the font.

	Checks metadata, vertical metrics, glyph coverage, master compatibility,
	outline quality, spacing, kerning, and variable font readiness.
	Returns results grouped by severity: critical, warning, info.
	"""
	def _run():
		import re, math
		from GlyphsApp import Glyphs
		font = _require_font()

		checks = []
		def _check(name, severity, passed, message, details=None):
			checks.append({"name": name, "severity": severity,
				"passed": passed, "message": message, "details": details})

		masters = list(font.masters)
		master_ids = [str(m.id) for m in masters]

		# ── Pre-compute shared data ──────────────────────────────────
		export_glyphs = [g for g in font.glyphs if g.export]
		unicode_map = {}
		component_refs = set()
		for g in export_glyphs:
			if g.unicode:
				unicode_map.setdefault(g.unicode, []).append(g.name)
			for m in masters:
				for c in g.layers[m.id].components:
					component_refs.add(str(c.componentName))

		# ══════════════════════════════════════════════════════════════
		# CRITICAL CHECKS
		# ══════════════════════════════════════════════════════════════

		# 1. Family name
		fn = font.familyName or ""
		_check("family_name", "critical",
			bool(fn) and fn not in ("New Font", "Untitled"),
			f"Family name: '{fn}'" if fn else "Family name is empty")

		# 2. .notdef exists
		notdef = font.glyphs[".notdef"]
		has_notdef = notdef is not None
		_check("notdef_exists", "critical", has_notdef,
			".notdef glyph exists" if has_notdef else ".notdef glyph is MISSING")

		# 3. space glyph
		space = font.glyphs["space"]
		space_ok = False
		space_msg = "space glyph missing"
		if space:
			if space.unicode != "0020":
				space_msg = f"space exists but unicode={space.unicode} (expected 0020)"
			else:
				bad_w = [m.name for m in masters if space.layers[m.id].width <= 0]
				if bad_w:
					space_msg = f"space has zero/negative width in: {', '.join(bad_w)}"
				else:
					space_ok = True
					space_msg = "space glyph OK (unicode=0020, width>0)"
		_check("space_glyph", "critical", space_ok, space_msg)

		# 4. Duplicate unicodes
		dupes = {u: names for u, names in unicode_map.items() if len(names) > 1}
		_check("duplicate_unicodes", "critical", len(dupes) == 0,
			f"{len(dupes)} duplicate unicode assignments" if dupes else "No duplicate unicodes",
			{f"U+{u}": names for u, names in list(dupes.items())[:20]} if dupes else None)

		# 5. Duplicate glyph names
		name_counts = {}
		for g in font.glyphs:
			name_counts[g.name] = name_counts.get(g.name, 0) + 1
		dup_names = {n: c for n, c in name_counts.items() if c > 1}
		_check("duplicate_names", "critical", len(dup_names) == 0,
			f"{len(dup_names)} duplicate glyph names" if dup_names else "No duplicate glyph names",
			dup_names if dup_names else None)

		# 6. Master compatibility
		incompat = []
		for g in export_glyphs:
			if len(masters) < 2:
				break
			layers = [g.layers[mid] for mid in master_ids]
			p_counts = [len(l.paths) for l in layers]
			c_counts = [len(l.components) for l in layers]
			if len(set(p_counts)) > 1:
				incompat.append(f"{g.name} (paths: {'/'.join(map(str,p_counts))})")
				continue
			if len(set(c_counts)) > 1:
				incompat.append(f"{g.name} (components: {'/'.join(map(str,c_counts))})")
				continue
			for pi in range(p_counts[0]):
				n_counts = [len(layers[mi].paths[pi].nodes) for mi in range(len(masters))]
				if len(set(n_counts)) > 1:
					incompat.append(f"{g.name} path{pi} (nodes: {'/'.join(map(str,n_counts))})")
					break
		_check("master_compatibility", "critical", len(incompat) == 0,
			f"{len(incompat)} incompatible glyphs" if incompat else "All glyphs compatible",
			incompat[:50] if incompat else None)

		# 7. Vertical metrics defined
		vmetrics_ok = True
		vmetrics_missing = []
		for m in masters:
			required = ["typoAscender", "typoDescender", "winAscent", "winDescent",
						"hheaAscender", "hheaDescender"]
			for key in required:
				val = None
				for p in m.customParameters:
					if p.name == key:
						val = p.value
						break
				if val is None:
					# Also check font-level
					for p in font.customParameters:
						if p.name == key:
							val = p.value
							break
				if val is None:
					vmetrics_missing.append(f"{m.name}: {key}")
					vmetrics_ok = False
		_check("vertical_metrics", "critical", vmetrics_ok,
			"All vertical metrics defined" if vmetrics_ok else f"Missing vertical metrics",
			vmetrics_missing[:20] if vmetrics_missing else None)

		# 8. Open paths
		open_path_glyphs = []
		for g in export_glyphs:
			for m in masters:
				for p in g.layers[m.id].paths:
					if not p.closed:
						open_path_glyphs.append(g.name)
						break
				else:
					continue
				break
		_check("open_paths", "critical", len(open_path_glyphs) == 0,
			f"{len(open_path_glyphs)} glyphs with open paths" if open_path_glyphs else "No open paths",
			open_path_glyphs[:50] if open_path_glyphs else None)

		# 9. Valid glyph names
		bad_names = []
		for g in export_glyphs:
			if not _PS_NAME_RE.match(g.name):
				bad_names.append(f"{g.name} (invalid characters)")
			elif len(g.name) > 63:
				bad_names.append(f"{g.name} (>{63} chars)")
		_check("valid_glyph_names", "critical", len(bad_names) == 0,
			f"{len(bad_names)} invalid glyph names" if bad_names else "All glyph names valid",
			bad_names[:30] if bad_names else None)

		# 10. Missing component references
		missing_comps = []
		for g in export_glyphs:
			for m in masters:
				for c in g.layers[m.id].components:
					cn = str(c.componentName)
					if font.glyphs[cn] is None:
						missing_comps.append(f"{g.name} → {cn}")
		missing_comps = list(set(missing_comps))
		_check("missing_components", "critical", len(missing_comps) == 0,
			f"{len(missing_comps)} missing component references" if missing_comps else "All components valid",
			missing_comps[:30] if missing_comps else None)

		# 11. Alignment zones
		zones_ok = True
		zones_issues = []
		for m in masters:
			zones = list(m.alignmentZones) if m.alignmentZones else []
			if not zones:
				zones_issues.append(f"{m.name}: no alignment zones defined")
				zones_ok = False
				continue
			# Check overlaps
			sorted_z = sorted([(int(z.position), int(z.size)) for z in zones])
			for i in range(len(sorted_z) - 1):
				p1, s1 = sorted_z[i]
				p2, s2 = sorted_z[i+1]
				top1 = p1 + abs(s1)
				if top1 > p2 and s1 != 0:
					zones_issues.append(f"{m.name}: zones overlap at {p1}+{s1} and {p2}+{s2}")
					zones_ok = False
		_check("alignment_zones", "critical", zones_ok,
			"Alignment zones OK" if zones_ok else "Alignment zone issues",
			zones_issues if zones_issues else None)

		# ══════════════════════════════════════════════════════════════
		# WARNING CHECKS
		# ══════════════════════════════════════════════════════════════

		# 13. Metadata
		meta_missing = []
		if not font.copyright: meta_missing.append("copyright")
		if not font.designer: meta_missing.append("designer")
		license_val = None
		for p in font.customParameters:
			if p.name == "license":
				license_val = p.value
		if not license_val: meta_missing.append("license")
		_check("metadata", "warning", len(meta_missing) == 0,
			"Metadata complete" if not meta_missing else f"Missing: {', '.join(meta_missing)}")

		# 14. Version
		ver_ok = font.versionMajor >= 1
		_check("version", "warning", ver_ok,
			f"Version {font.versionMajor}.{font.versionMinor:03d}" if ver_ok else
			f"Version {font.versionMajor}.{font.versionMinor:03d} — should be >= 1.000")

		# 15. Essential glyphs
		missing_essential = []
		for names in _ESSENTIAL_GLYPHS:
			found = any(font.glyphs[n] is not None for n in names)
			if not found:
				missing_essential.append(names[0])
		_check("essential_glyphs", "warning", len(missing_essential) == 0,
			f"Missing: {', '.join(missing_essential)}" if missing_essential else "All essential glyphs present")

		# 16. .notdef has outlines
		if has_notdef:
			notdef_outlines = any(len(notdef.layers[m.id].paths) > 0 for m in masters)
			_check("notdef_outlines", "warning", notdef_outlines,
				".notdef has outlines" if notdef_outlines else ".notdef is empty (should have outlines)")

		# 17. Alignment zone sizes
		zero_zones = []
		for m in masters:
			for z in (m.alignmentZones or []):
				if int(z.size) == 0:
					zero_zones.append(f"{m.name}: pos={int(z.position)} size=0")
		_check("zone_overshoots", "warning", len(zero_zones) == 0,
			f"{len(zero_zones)} zones with size=0 (no overshoot for hinting)" if zero_zones else "All zones have overshoot",
			zero_zones if zero_zones else None)

		# 18. Use Typo Metrics
		use_typo = False
		for p in font.customParameters:
			if p.name == "Use Typo Metrics":
				use_typo = bool(p.value)
				break
		_check("use_typo_metrics", "warning", use_typo,
			"Use Typo Metrics is enabled" if use_typo else "Use Typo Metrics NOT set (recommended for cross-platform consistency)")

		# 19. Consistent typo/hhea
		vmetrics_consistent = True
		vmetrics_diff = []
		for m in masters:
			vals = {}
			for p in m.customParameters:
				if p.name in ("typoAscender","typoDescender","hheaAscender","hheaDescender"):
					vals[p.name] = p.value
			if vals.get("typoAscender") and vals.get("hheaAscender"):
				if vals["typoAscender"] != vals["hheaAscender"]:
					vmetrics_diff.append(f"{m.name}: typoAsc={vals['typoAscender']} ≠ hheaAsc={vals['hheaAscender']}")
					vmetrics_consistent = False
			if vals.get("typoDescender") and vals.get("hheaDescender"):
				if vals["typoDescender"] != vals["hheaDescender"]:
					vmetrics_diff.append(f"{m.name}: typoDesc={vals['typoDescender']} ≠ hheaDesc={vals['hheaDescender']}")
					vmetrics_consistent = False
		_check("typo_hhea_match", "warning", vmetrics_consistent,
			"typo and hhea metrics match" if vmetrics_consistent else "typo/hhea mismatch",
			vmetrics_diff if vmetrics_diff else None)

		# 20. winAscent/winDescent coverage
		win_vals = {}
		for m in masters:
			for p in m.customParameters:
				if p.name == "winAscent": win_vals.setdefault(m.name, {})["winA"] = int(p.value)
				if p.name == "winDescent": win_vals.setdefault(m.name, {})["winD"] = int(p.value)
		# Sample glyph bounds
		max_y, min_y = 0, 0
		sampled = 0
		for g in export_glyphs[:300]:
			for m in masters:
				b = g.layers[m.id].bounds
				if b:
					top = b.origin.y + b.size.height
					bot = b.origin.y
					if top > max_y: max_y = int(top)
					if bot < min_y: min_y = int(bot)
					sampled += 1
		win_issues = []
		for mname, wv in win_vals.items():
			if "winA" in wv and wv["winA"] < max_y:
				win_issues.append(f"{mname}: winAscent={wv['winA']} < max glyph y={max_y}")
			if "winD" in wv and wv["winD"] < abs(min_y):
				win_issues.append(f"{mname}: winDescent={wv['winD']} < |min glyph y|={abs(min_y)}")
		_check("win_metrics_coverage", "warning", len(win_issues) == 0,
			"winAscent/winDescent cover glyph extremes" if not win_issues else "win metrics may clip",
			win_issues if win_issues else None)

		# 21. Kerning group orphans
		orphan_both = []
		orphan_left = []
		orphan_right = []
		for g in export_glyphs:
			if (g.category or "") != "Letter":
				continue
			has_l = bool(g.leftKerningGroup)
			has_r = bool(g.rightKerningGroup)
			if not has_l and not has_r:
				orphan_both.append(g.name)
			elif not has_l:
				orphan_left.append(g.name)
			elif not has_r:
				orphan_right.append(g.name)
		total_orphans = len(orphan_both) + len(orphan_left) + len(orphan_right)
		_check("kerning_groups", "warning", total_orphans == 0,
			f"{total_orphans} letter glyphs without kerning groups" if total_orphans else "All letters have kerning groups",
			{"missing_both": orphan_both[:20], "missing_left": orphan_left[:10], "missing_right": orphan_right[:10]} if total_orphans else None)

		# 22. Cross-master kerning
		if len(masters) >= 2:
			kern_sets = {}
			for m in masters:
				pairs = set()
				kern = font.kerning.get(m.id, {})
				if kern:
					for left, rights in kern.items():
						for right in rights:
							pairs.add((str(left), str(right)))
				kern_sets[m.name] = pairs
			all_pairs = set()
			for s in kern_sets.values():
				all_pairs |= s
			cross_missing = 0
			for pair in all_pairs:
				present_in = [mn for mn, s in kern_sets.items() if pair in s]
				if len(present_in) < len(masters):
					cross_missing += 1
			_check("cross_master_kerning", "warning", cross_missing == 0,
				f"{cross_missing} pairs missing in some masters (interpolation jumps)" if cross_missing else "Kerning consistent across masters")

		# 23. Critical kern pairs
		total_critical = len(_CRITICAL_PAIRS_PROD)
		covered = 0
		missing_critical = []
		for left, right in _CRITICAL_PAIRS_PROD:
			found = False
			for m in masters:
				kern = font.kerning.get(m.id, {})
				if not kern:
					continue
				gl = font.glyphs[left]
				gr = font.glyphs[right]
				if not gl or not gr:
					found = True  # glyph doesn't exist, skip
					break
				# Check direct pair and group pair
				l_group = f"@MMK_L_{gl.rightKerningGroup}" if gl.rightKerningGroup else None
				r_group = f"@MMK_R_{gr.leftKerningGroup}" if gr.leftKerningGroup else None
				l_id = gl.id if hasattr(gl, 'id') else gl.name
				for lk in [l_id, l_group]:
					if lk and lk in kern:
						for rk in [gr.id if hasattr(gr, 'id') else gr.name, r_group]:
							if rk and rk in kern[lk]:
								found = True
								break
					if found:
						break
				if found:
					break
			if found:
				covered += 1
			else:
				missing_critical.append(f"{left}{right}")
		pct = round(covered / total_critical * 100, 1) if total_critical else 0
		_check("critical_kern_pairs", "warning", pct >= 80,
			f"{pct}% critical pairs covered ({covered}/{total_critical})" ,
			{"missing": missing_critical[:30]} if missing_critical else None)

		# 24. Features exist
		has_features = len(font.features) > 0 if font.features else False
		_check("features_exist", "warning", has_features,
			f"{len(font.features)} features defined" if has_features else "No OpenType features defined")

		# 25. Instance weight classes
		bad_wc = []
		for inst in font.instances:
			if inst.weightClass not in _STANDARD_WEIGHT_CLASSES:
				bad_wc.append(f"{inst.name}: weightClass={inst.weightClass}")
		_check("weight_classes", "warning", len(bad_wc) == 0,
			"All instance weight classes standard" if not bad_wc else f"{len(bad_wc)} non-standard weight classes",
			bad_wc if bad_wc else None)

		# 26. Style linking
		link_issues = []
		for inst in font.instances:
			if inst.isBold and not inst.linkStyle:
				link_issues.append(f"{inst.name}: isBold but no linkStyle")
		_check("style_linking", "warning", len(link_issues) == 0,
			"Style linking OK" if not link_issues else f"{len(link_issues)} style linking issues",
			link_issues if link_issues else None)

		# 27. nbspace width = space width
		nbsp = font.glyphs["nbspace"]
		if space and nbsp:
			nb_issues = []
			for m in masters:
				sw = space.layers[m.id].width
				nw = nbsp.layers[m.id].width
				if abs(sw - nw) > 1:
					nb_issues.append(f"{m.name}: space={sw} nbspace={nw}")
			_check("nbspace_width", "warning", len(nb_issues) == 0,
				"nbspace width matches space" if not nb_issues else "nbspace width ≠ space",
				nb_issues if nb_issues else None)

		# 28. Zero-width letters
		zw_letters = []
		for g in export_glyphs:
			if (g.category or "") != "Letter":
				continue
			for m in masters:
				if g.layers[m.id].width == 0:
					zw_letters.append(g.name)
					break
		_check("zero_width_letters", "warning", len(zw_letters) == 0,
			f"{len(zw_letters)} zero-width letter glyphs" if zw_letters else "No zero-width letters",
			zw_letters[:20] if zw_letters else None)

		# 29. PANOSE
		panose = None
		for p in font.customParameters:
			if p.name == "panose":
				panose = list(p.value)
		panose_ok = panose and any(v != 0 for v in panose)
		_check("panose", "warning", bool(panose_ok),
			f"PANOSE set: {panose}" if panose_ok else "PANOSE not set or all zeros")

		# 30. Stems defined
		stems_ok = True
		stems_info = []
		for m in masters:
			try:
				stems = list(m.stems) if m.stems else []
			except:
				stems = []
			if not stems:
				stems_ok = False
				stems_info.append(f"{m.name}: no stems defined")
			else:
				stems_info.append(f"{m.name}: {len(stems)} stems")
		_check("stems_defined", "warning", stems_ok,
			"Stems defined in all masters" if stems_ok else "Missing stem definitions",
			stems_info if not stems_ok else None)

		# 31. Short segments
		short_segs = []
		for g in export_glyphs[:200]:
			if (g.category or "") != "Letter":
				continue
			for m in masters:
				found = False
				for path in g.layers[m.id].paths:
					nodes = list(path.nodes)
					n = len(nodes)
					for i in range(n):
						n1 = nodes[i]
						n2 = nodes[(i+1) % n]
						if n1.type != "offcurve" and n2.type != "offcurve":
							dx = n2.position.x - n1.position.x
							dy = n2.position.y - n1.position.y
							dist = math.sqrt(dx*dx + dy*dy)
							if dist < 2 and dist > 0:
								short_segs.append(f"{g.name} ({m.name})")
								found = True
								break
					if found:
						break
				if found:
					break
		_check("short_segments", "warning", len(short_segs) == 0,
			f"{len(short_segs)} glyphs with very short segments (<2u)" if short_segs else "No short segments",
			short_segs[:20] if short_segs else None)

		# 32. Near-vertical/horizontal lines
		near_vh = []
		for g in export_glyphs[:200]:
			if (g.category or "") != "Letter":
				continue
			for m in masters:
				found = False
				for path in g.layers[m.id].paths:
					nodes = list(path.nodes)
					n = len(nodes)
					for i in range(n):
						n1 = nodes[i]
						n2 = nodes[(i+1) % n]
						if n1.type == "offcurve" or n2.type == "offcurve":
							continue
						dx = abs(n2.position.x - n1.position.x)
						dy = abs(n2.position.y - n1.position.y)
						if (0 < dx <= 2 and dy > 20) or (0 < dy <= 2 and dx > 20):
							near_vh.append(f"{g.name} ({m.name})")
							found = True
							break
					if found:
						break
				if found:
					break
		_check("near_misses", "warning", len(near_vh) == 0,
			f"{len(near_vh)} glyphs with near-vertical/horizontal lines (off by 1-2u)" if near_vh else "No near-miss alignments",
			near_vh[:20] if near_vh else None)

		# 33. Presentation forms decomposition (fi, fl, ff, etc.)
		pf_names = ["fi", "fl", "ff", "ffi", "ffl"]
		pf_present = [n for n in pf_names
			if font.glyphs[n] and font.glyphs[n].export and font.glyphs[n].unicode]
		if pf_present:
			ccmp_feat = font.features["ccmp"] if font.features else None
			ccmp_code = ccmp_feat.code if ccmp_feat else ""
			not_decomposed = [n for n in pf_present if f"sub {n} " not in ccmp_code and f"sub {n}\t" not in ccmp_code]
			_check("presentation_forms", "warning", len(not_decomposed) == 0,
				"Presentation forms decomposed in ccmp" if not not_decomposed
				else f"{len(not_decomposed)} presentation forms not decomposed in ccmp (breaks tracking & smallcaps)",
				not_decomposed if not_decomposed else None)

		# 34. German sharp S contextual substitution
		germandbls_g = font.glyphs["germandbls"]
		uc_sharp = font.glyphs["Germandbls"] or font.glyphs["germandbls.calt"]
		if germandbls_g and uc_sharp and germandbls_g.export and uc_sharp.export:
			has_german_sub = False
			for feat in (font.features or []):
				if feat.name in ("calt", "locl"):
					if "germandbls" in (feat.code or "") or "Germandbls" in (feat.code or ""):
						has_german_sub = True
						break
			_check("german_sharp_s", "warning", has_german_sub,
				"German sharp S substitution found" if has_german_sub
				else f"Font has {uc_sharp.name} but no contextual calt/locl substitution (German ß→ẞ between caps)")

		# 35. Dutch IJ localization
		jacute_g = font.glyphs["Jacute"]
		if jacute_g and jacute_g.export:
			locl_feat = font.features["locl"] if font.features else None
			has_nld = bool(locl_feat and "NLD" in (locl_feat.code or ""))
			_check("dutch_ij", "warning", has_nld,
				"Dutch NLD localization found in locl" if has_nld
				else "Font has Jacute but no NLD localization in locl")

		# 36. Smallcap feature completeness
		smcp_feat = font.features["smcp"] if font.features else None
		if smcp_feat:
			sc_missing = []
			sc_casefoldings = {"idotless": "i", "jdotless": "j", "kgreenlandic": "k", "longs": "s"}
			sc_suffixes = ("sc", "smcp", "c2sc", "small", "smallcap")
			smcp_code = smcp_feat.code or ""
			for src_name in sorted(sc_casefoldings.keys()):
				src_g = font.glyphs[src_name]
				if src_g and src_g.export:
					has_own_sc = any(font.glyphs[f"{src_name}.{sfx}"] for sfx in sc_suffixes)
					in_smcp = f"sub {src_name}" in smcp_code
					if not has_own_sc and not in_smcp:
						sc_missing.append(src_name)
			if sc_missing:
				_check("sc_completeness", "warning", False,
					f"smcp missing substitutions for: {', '.join(sc_missing)}",
					sc_missing)

		# 37. salt feature from ssXX
		ssXX_feats = [f for f in (font.features or [])
			if f.name.startswith("ss") and len(f.name) == 4 and f.name[2:].isdigit()]
		if ssXX_feats:
			salt_feat = font.features["salt"] if font.features else None
			_check("salt_feature", "warning", salt_feat is not None,
				f"salt feature present ({len(ssXX_feats)} ssXX features)" if salt_feat
				else f"{len(ssXX_feats)} ssXX features but no salt feature (limits app compatibility)")

		# 38. Languagesystems prefix
		if font.features and len(font.features) > 0:
			has_langsys = False
			for pfx in (font.featurePrefixes or []):
				if "languagesystem" in (pfx.code or "").lower() or pfx.name == "Languagesystems":
					has_langsys = True
					break
			_check("languagesystems", "warning", has_langsys,
				"Languagesystems prefix defined" if has_langsys
				else "No languagesystems prefix (needed for locl and script/language support)")

		# 39. Remove Overlap on export
		# RemoveOverlap is ON by default in GlyphsApp export.
		# Only flag if explicitly disabled at font or instance level.
		ro_disabled_font = False
		for p in font.customParameters:
			if p.name == "RemoveOverlap" and not p.value:
				ro_disabled_font = True
				break
		ro_disabled_instances = []
		for inst in font.instances:
			for p in inst.customParameters:
				if p.name == "RemoveOverlap" and not p.value:
					ro_disabled_instances.append(inst.name)
					break
		ro_ok = not ro_disabled_font and len(ro_disabled_instances) == 0
		if ro_disabled_font:
			ro_msg = "RemoveOverlap DISABLED at font level (overlaps in exported outlines cause rendering issues with stroked text)"
		elif ro_disabled_instances:
			ro_msg = f"RemoveOverlap disabled on {len(ro_disabled_instances)} instances"
		else:
			ro_msg = "RemoveOverlap active (default on)"
		_check("remove_overlap", "warning", ro_ok, ro_msg,
			ro_disabled_instances[:10] if ro_disabled_instances else None)

		# ══════════════════════════════════════════════════════════════
		# INFO CHECKS
		# ══════════════════════════════════════════════════════════════

		# 40. Glyph count by category
		cat_counts = {}
		for g in export_glyphs:
			cat = g.category or "Other"
			cat_counts[cat] = cat_counts.get(cat, 0) + 1
		_check("glyph_summary", "info", True,
			f"{len(export_glyphs)} exporting glyphs",
			cat_counts)

		# 41. Font metrics summary
		metrics_summary = {}
		for m in masters:
			metrics_summary[m.name] = {
				"ascender": int(m.ascender), "descender": int(m.descender),
				"xHeight": int(m.xHeight), "capHeight": int(m.capHeight),
			}
		_check("font_metrics", "info", True,
			f"UPM={font.upm}", metrics_summary)

		# 42. fsType
		fs_type = None
		for p in font.customParameters:
			if p.name == "fsType":
				fs_type = list(p.value) if p.value else []
		fs_msg = "Installable (no restrictions)" if fs_type == [] else f"fsType={fs_type}"
		_check("fs_type", "info", True, fs_msg)

		# 43. VF readiness
		if len(masters) >= 2:
			vf_info = {
				"axes": [(a.name, a.axisTag) for a in font.axes],
				"masters": len(masters),
				"instances": len(font.instances),
			}
			# Check STAT
			has_stat = False
			for p in font.customParameters:
				if "STAT" in str(p.name):
					has_stat = True
			vf_info["STAT_configured"] = has_stat
			_check("vf_readiness", "info", True,
				f"Variable font: {len(font.axes)} axes, {len(font.instances)} instances",
				vf_info)

		# 44. Unreachable glyphs
		unreachable = []
		for g in export_glyphs:
			if g.unicode:
				continue
			if g.name.startswith(".") or g.name.startswith("_"):
				continue
			if g.name in component_refs:
				continue
			unreachable.append(g.name)
		_check("unreachable_glyphs", "info", True,
			f"{len(unreachable)} glyphs with no unicode and not used as components",
			unreachable[:30] if unreachable else None)

		# ── Build summary ────────────────────────────────────────────
		summary = {"critical": 0, "warning": 0, "info": 0,
				   "critical_passed": 0, "warning_passed": 0}
		for c in checks:
			sev = c["severity"]
			if sev == "critical":
				if c["passed"]: summary["critical_passed"] += 1
				else: summary["critical"] += 1
			elif sev == "warning":
				if c["passed"]: summary["warning_passed"] += 1
				else: summary["warning"] += 1
			else:
				summary["info"] += 1

		return {
			"ok": True,
			"fontName": font.familyName,
			"glyphCount": len(export_glyphs),
			"masterCount": len(masters),
			"summary": summary,
			"checks": checks,
		}

	result = bridge.execute_on_main(_run)
	if isinstance(result, dict) and "error" in result and "ok" not in result:
		return 500, {"ok": False, "error": result["error"]}
	return 200, result
