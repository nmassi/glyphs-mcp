# encoding: utf-8
"""
server.py — HTTP server for the GlyphsMCP plugin.

Runs a ThreadingHTTPServer on a daemon thread, bound to 127.0.0.1.
Routes incoming requests to handler functions via a simple path-based router.
All actual Glyphs API work is delegated through the bridge to the main thread.

See ARCHITECTURE.md §3.4 for technology choice rationale.
"""

import json
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from handlers import ROUTES, handle_not_found


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
	"""HTTPServer that handles each request in a new thread."""
	daemon_threads = True
	allow_reuse_address = True


class MCPRequestHandler(BaseHTTPRequestHandler):
	"""Handles HTTP requests and routes them to handler functions."""

	# Suppress default logging to stderr (noisy)
	def log_message(self, format, *args):
		# Route to print() so it appears in GlyphsApp Macro Panel
		print(f"[GlyphsMCP] {args[0]} {args[1]} {args[2]}")

	def do_GET(self):
		self._handle_request("GET")

	def do_POST(self):
		self._handle_request("POST")

	def do_DELETE(self):
		self._handle_request("DELETE")

	def _handle_request(self, method):
		"""Route request to the appropriate handler."""
		parsed = urlparse(self.path)
		path = parsed.path.rstrip("/")
		query = parse_qs(parsed.query)

		# Read body for POST and DELETE
		body = None
		if method in ("POST", "DELETE"):
			content_length = int(self.headers.get("Content-Length", 0))
			if content_length > 0:
				raw = self.rfile.read(content_length)
				try:
					body = json.loads(raw)
				except json.JSONDecodeError as e:
					self._send_json(400, {"error": f"Invalid JSON: {e}"})
					return

		# Find matching route
		handler, path_params = self._match_route(method, path)

		if handler is None:
			self._send_json(404, handle_not_found(method, path))
			return

		# Execute handler — it will use bridge internally
		try:
			bridge = self.server.bridge
			status, result = handler(bridge=bridge, query=query, body=body, **path_params)
			self._send_json(status, result)
		except TimeoutError as e:
			self._send_json(503, {"error": str(e)})
		except Exception as e:
			print(f"[GlyphsMCP] Handler error: {e}")
			traceback.print_exc()
			self._send_json(500, {"error": str(e), "type": type(e).__name__})

	def _match_route(self, method, path):
		"""Match URL path against registered routes. Returns (handler, path_params) or (None, None).

		Routes can have path parameters: /api/font/glyphs/{name}
		"""
		for (route_method, route_pattern), handler in ROUTES.items():
			if route_method != method:
				continue

			params = self._match_pattern(route_pattern, path)
			if params is not None:
				return handler, params

		return None, None

	@staticmethod
	def _match_pattern(pattern, path):
		"""Match a route pattern like /api/font/glyphs/{name} against a path.

		Returns dict of captured params, or None if no match.
		"""
		pattern_parts = pattern.rstrip("/").split("/")
		path_parts = path.rstrip("/").split("/")

		if len(pattern_parts) != len(path_parts):
			return None

		params = {}
		for pp, pathp in zip(pattern_parts, path_parts):
			if pp.startswith("{") and pp.endswith("}"):
				param_name = pp[1:-1]
				params[param_name] = pathp
			elif pp != pathp:
				return None

		return params

	def _send_json(self, status, data):
		"""Send a JSON response."""
		body = json.dumps(data, ensure_ascii=False, default=str)
		self.send_response(status)
		self.send_header("Content-Type", "application/json; charset=utf-8")
		self.send_header("Access-Control-Allow-Origin", "*")
		self.send_header("Content-Length", str(len(body.encode("utf-8"))))
		self.end_headers()
		self.wfile.write(body.encode("utf-8"))

	def _send_binary(self, status, data, content_type):
		"""Send a binary response (for images, SVG)."""
		self.send_response(status)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)


class MCPHTTPServer:
	"""Manages the HTTP server lifecycle on a daemon thread."""

	def __init__(self, port=7745, bridge=None):
		self.port = port
		self.bridge = bridge
		self._server = None
		self._thread = None
		self.is_running = False

	def start(self):
		"""Start the HTTP server on a daemon thread."""
		if self.is_running:
			return

		self._server = ThreadingHTTPServer(("127.0.0.1", self.port), MCPRequestHandler)
		self._server.bridge = self.bridge  # Make bridge accessible to handlers

		self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
		self._thread.start()
		self.is_running = True

	def stop(self):
		"""Shut down the HTTP server."""
		if self._server:
			self._server.shutdown()
			self._server.server_close()
			self._server = None
		if self._thread:
			self._thread.join(timeout=5.0)
			self._thread = None
		self.is_running = False
