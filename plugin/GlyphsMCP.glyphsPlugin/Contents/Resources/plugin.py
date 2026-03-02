# encoding: utf-8
"""
GlyphsMCP — MCP Bridge Plugin for GlyphsApp

This is the main plugin file. It:
- Starts/stops the HTTP server on plugin lifecycle
- Adds a GlyphsMCP submenu under the Window menu
- Sets up the NSTimer-based bridge for thread-safe Glyphs API access

See ARCHITECTURE.md §3 for the full design.
"""

from GlyphsApp import *
from GlyphsApp.plugins import *
from AppKit import NSApp, NSMenu, NSMenuItem, NSPasteboard, NSWorkspace
from Foundation import NSURL
import os
import json

# Sibling imports from Resources/
from bridge import MainThreadBridge
from server import MCPHTTPServer

DEFAULT_PORT = 7745
PREF_PORT = "com.glyphsmcp.port"
PREF_AUTOSTART = "com.glyphsmcp.autostart"
PREF_ALLOW_EXECUTE = "com.glyphsmcp.allowExecute"

DOCS_URL = "https://github.com/glyphsapp-mcp/glyphsapp-mcp"


def _discover_repo_path():
	"""Read the repo path from .repo_path breadcrumb written by install_plugin.sh."""
	resources_dir = os.path.dirname(__file__)
	breadcrumb = os.path.join(resources_dir, ".repo_path")
	try:
		with open(breadcrumb, "r") as f:
			path = f.read().strip()
			if path and os.path.isdir(path):
				return path
	except (OSError, IOError):
		pass
	return None


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

		# Cache repo path for config generation
		self._repo_path = _discover_repo_path()

		# Build menu items
		self._server_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Start Server", self.toggleServer_, ""
		)
		self._server_item.setTarget_(self)

		self._execute_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Allow Execute Endpoint", self.toggleExecute_, ""
		)
		self._execute_item.setTarget_(self)
		self._execute_item.setState_(1 if Glyphs.defaults[PREF_ALLOW_EXECUTE] else 0)

		# Connect submenu
		self._connect_submenu = NSMenu.alloc().initWithTitle_("Connect")

		self._copy_claude_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Copy Config for Claude Code", self.copyClaudeConfig_, ""
		)
		self._copy_claude_item.setTarget_(self)
		self._connect_submenu.addItem_(self._copy_claude_item)

		self._copy_vscode_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Copy Config for Visual Studio Code (Or any fork)", self.copyVSCodeConfig_, ""
		)
		self._copy_vscode_item.setTarget_(self)
		self._connect_submenu.addItem_(self._copy_vscode_item)

		self._connect_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Connect", None, ""
		)
		self._connect_parent.setSubmenu_(self._connect_submenu)

		# Documentation item
		self._docs_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"Documentation", self.openDocumentation_, ""
		)
		self._docs_item.setTarget_(self)

		# Assemble submenu
		submenu = NSMenu.alloc().initWithTitle_("GlyphsMCP")
		submenu.addItem_(self._server_item)
		submenu.addItem_(NSMenuItem.separatorItem())
		submenu.addItem_(self._connect_parent)
		submenu.addItem_(NSMenuItem.separatorItem())
		submenu.addItem_(self._docs_item)
		submenu.addItem_(NSMenuItem.separatorItem())
		submenu.addItem_(self._execute_item)

		parentItem = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
			"GlyphsMCP", None, ""
		)
		parentItem.setSubmenu_(submenu)
		self._menu_item = parentItem

		# Add under Window menu (WINDOW_MENU is locale-independent)
		Glyphs.menu[WINDOW_MENU].append(NSMenuItem.separatorItem())
		Glyphs.menu[WINDOW_MENU].append(parentItem)

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

	def copyClaudeConfig_(self, sender):
		"""Copy Claude Code MCP config JSON to clipboard."""
		self._copyConfigToClipboard("claude")

	def copyVSCodeConfig_(self, sender):
		"""Copy VS Code MCP config JSON to clipboard."""
		self._copyConfigToClipboard("vscode")

	def openDocumentation_(self, sender):
		"""Open the GitHub documentation in the default browser."""
		url = NSURL.URLWithString_(DOCS_URL)
		NSWorkspace.sharedWorkspace().openURL_(url)

	@objc.python_method
	def _copyConfigToClipboard(self, target):
		"""Generate MCP config JSON and copy to clipboard."""
		repo = self._repo_path
		if repo is not None:
			# Dev install — use local paths
			command = os.path.join(repo, ".venv", "bin", "python")
			args = [os.path.join(repo, "server", "glyphs_mcp_server.py")]
		else:
			# Plugin Manager install — use uvx
			command = "uvx"
			args = ["glyphs-mcp"]

		if target == "claude":
			config = {
				"mcpServers": {
					"glyphs-mcp": {
						"command": command,
						"args": args
					}
				}
			}
		elif target == "vscode":
			config = {
				"servers": {
					"glyphs-mcp": {
						"type": "stdio",
						"command": command,
						"args": args
					}
				}
			}
		else:
			return

		config_json = json.dumps(config, indent=2)
		pb = NSPasteboard.generalPasteboard()
		pb.clearContents()
		pb.setString_forType_(config_json, "public.utf8-plain-text")

		label = "Claude Code" if target == "claude" else "VS Code"
		suffix = "" if repo else " (with placeholder paths)"
		print(f"[GlyphsMCP] Copied {label} config to clipboard{suffix}")

	@objc.python_method
	def __del__(self):
		self.stopServer()
		if hasattr(self, '_menu_item') and self._menu_item:
			try:
				Glyphs.menu[WINDOW_MENU].submenu().removeItem_(self._menu_item)
			except Exception:
				pass

	@objc.python_method
	def __file__(self):
		"""Please leave this method unchanged"""
		return __file__
