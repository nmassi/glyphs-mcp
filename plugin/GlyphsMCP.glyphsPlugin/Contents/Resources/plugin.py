# encoding: utf-8
"""
GlyphsMCP — MCP Bridge Plugin for GlyphsApp

This is the main plugin file. It:
- Starts/stops the HTTP server on plugin lifecycle
- Adds a top-level GlyphsMCP menu to the menu bar
- Sets up the NSTimer-based bridge for thread-safe Glyphs API access

See ARCHITECTURE.md §3 for the full design.
"""

from GlyphsApp import *
from GlyphsApp.plugins import *
from AppKit import NSApp, NSMenu, NSMenuItem

# Sibling imports from Resources/
from bridge import MainThreadBridge
from server import MCPHTTPServer

PLUGIN_VERSION = "0.1.0"
DEFAULT_PORT = 7745
PREF_PORT = "com.glyphsmcp.port"
PREF_AUTOSTART = "com.glyphsmcp.autostart"
PREF_ALLOW_EXECUTE = "com.glyphsmcp.allowExecute"


class GlyphsMCP(GeneralPlugin):
	"""General plugin that runs an HTTP server for MCP communication."""

	@objc.python_method
	def settings(self):
		self.name = "GlyphsMCP"

	@objc.python_method
	def start(self):
		# Initialize defaults
		if Glyphs.defaults[PREF_PORT] is None:
			Glyphs.defaults[PREF_PORT] = DEFAULT_PORT
		if Glyphs.defaults[PREF_AUTOSTART] is None:
			Glyphs.defaults[PREF_AUTOSTART] = True
		if Glyphs.defaults[PREF_ALLOW_EXECUTE] is None:
			Glyphs.defaults[PREF_ALLOW_EXECUTE] = False

		self.bridge = None
		self.http_server = None

		# Build submenu
		self._server_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Start Server", self.toggleServer_, ""
		)
		self._server_item.setTarget_(self)

		self._execute_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Allow Execute Endpoint", self.toggleExecute_, ""
		)
		self._execute_item.setTarget_(self)
		self._execute_item.setState_(1 if Glyphs.defaults[PREF_ALLOW_EXECUTE] else 0)

		submenu = NSMenu.alloc().initWithTitle_("GlyphsMCP")
		submenu.addItem_(self._server_item)
		submenu.addItem_(NSMenuItem.separatorItem())
		submenu.addItem_(self._execute_item)

		parentItem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"GlyphsMCP", None, ""
		)
		parentItem.setSubmenu_(submenu)
		self._menu_item = parentItem
		NSApp.mainMenu().addItem_(parentItem)

		# Auto-start if enabled
		if Glyphs.defaults[PREF_AUTOSTART]:
			self.startServer()

	@objc.python_method
	def startServer(self):
		"""Start the bridge and HTTP server."""
		if self.http_server and self.http_server.is_running:
			print("[GlyphsMCP] Server already running")
			return

		port = int(Glyphs.defaults[PREF_PORT] or DEFAULT_PORT)

		try:
			self.bridge = MainThreadBridge()
			self.bridge.start()
			self.http_server = MCPHTTPServer(port=port, bridge=self.bridge)
			self.http_server.start()
			self._server_item.setTitle_("Stop Server")
			print(f"[GlyphsMCP] Server running on http://127.0.0.1:{port}")
		except Exception as e:
			print(f"[GlyphsMCP] Failed to start: {e}")
			import traceback
			traceback.print_exc()

	@objc.python_method
	def stopServer(self):
		"""Stop the HTTP server and bridge."""
		if self.http_server:
			self.http_server.stop()
			self.http_server = None
		if self.bridge:
			self.bridge.stop()
			self.bridge = None
		if hasattr(self, '_server_item'):
			self._server_item.setTitle_("Start Server")
		print("[GlyphsMCP] Server stopped")

	def toggleServer_(self, sender):
		"""Submenu callback — toggle server on/off."""
		if self.http_server and self.http_server.is_running:
			self.stopServer()
		else:
			self.startServer()

	def toggleExecute_(self, sender):
		"""Submenu callback — toggle allow-execute preference."""
		current = bool(Glyphs.defaults[PREF_ALLOW_EXECUTE])
		new_val = not current
		Glyphs.defaults[PREF_ALLOW_EXECUTE] = new_val
		self._execute_item.setState_(1 if new_val else 0)
		state = "enabled" if new_val else "disabled"
		print(f"[GlyphsMCP] Execute endpoint {state}")

	@objc.python_method
	def __del__(self):
		self.stopServer()
		if hasattr(self, '_menu_item') and self._menu_item:
			NSApp.mainMenu().removeItem_(self._menu_item)

	@objc.python_method
	def __file__(self):
		"""Please leave this method unchanged"""
		return __file__
