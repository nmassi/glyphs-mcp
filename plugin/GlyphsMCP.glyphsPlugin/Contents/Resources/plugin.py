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
from AppKit import NSAlert, NSAlertFirstButtonReturn, NSApp, NSLinkAttributeName, NSMenu, NSMenuItem, NSPasteboard, NSTextField, NSWorkspace
from Foundation import NSAttributedString, NSMakeRect, NSURL
from PyObjCTools import AppHelper
import objc
import json
import os
import shlex
import shutil
import subprocess
import threading

# Sibling imports from Resources/
from bridge import MainThreadBridge
from connection_config import (
	build_server_spec,
	claude_code_command,
	codex_command,
	discover_repo_path,
	generic_config,
	opencode_command,
	vscode_command,
	write_cursor_config,
)
from server import MCPHTTPServer

DEFAULT_PORT = 7745
PREF_PORT = "com.nico.glyphs-mcp.port"
PREF_AUTOSTART = "com.nico.glyphs-mcp.autostart"
PREF_ALLOW_EXECUTE = "com.nico.glyphs-mcp.allowExecute"

DOCS_URL = "https://github.com/nmassi/glyphs-mcp/blob/main/README.md"


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
		self._repo_path = discover_repo_path(__file__)

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

		connect_actions = (
			("Claude Code…", self.connectClaudeCode_),
			("Codex / ChatGPT Desktop…", self.connectCodex_),
			("OpenCode…", self.connectOpenCode_),
			("Visual Studio Code…", self.connectVSCode_),
			("Cursor…", self.connectCursor_),
			("Other MCP Client…", self.copyGenericConfig_),
		)
		for title, action in connect_actions:
			item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
			item.setTarget_(self)
			self._connect_submenu.addItem_(item)

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

	@objc.python_method
	def _showServerStatusAlert(self, started, port=None):
		"""Confirm a manual server start or stop action."""
		alert = NSAlert.alloc().init()
		alert.addButtonWithTitle_("OK")

		if started:
			alert.setMessageText_("GlyphsMCP is ready")
			alert.setInformativeText_(f"Listening at http://127.0.0.1:{port}")

			link = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 22))
			link.setBezeled_(False)
			link.setDrawsBackground_(False)
			link.setEditable_(False)
			link.setSelectable_(True)
			link.setAllowsEditingTextAttributes_(True)
			link.setAttributedStringValue_(
				NSAttributedString.alloc().initWithString_attributes_(
					"Read the GlyphsMCP documentation on GitHub",
					{NSLinkAttributeName: NSURL.URLWithString_(DOCS_URL)},
				)
			)
			alert.setAccessoryView_(link)
		else:
			alert.setMessageText_("GlyphsMCP is offline")
			alert.setInformativeText_(
				"Start it again anytime from Window > GlyphsMCP."
			)

		alert.runModal()

	def toggleServer_(self, sender):
		"""Submenu callback — toggle server on/off."""
		if self.http_server and self.http_server.is_running:
			self.stopServer()
			self._showServerStatusAlert(False)
		else:
			self.startServer()
			if self.http_server and self.http_server.is_running:
				port = int(Glyphs.defaults[PREF_PORT] or DEFAULT_PORT)
				self._showServerStatusAlert(True, port)

	def toggleExecute_(self, sender):
		"""Submenu callback — toggle allow-execute preference."""
		current = bool(Glyphs.defaults[PREF_ALLOW_EXECUTE])
		new_val = not current
		Glyphs.defaults[PREF_ALLOW_EXECUTE] = new_val
		self._execute_item.setState_(1 if new_val else 0)
		state = "enabled" if new_val else "disabled"
		print(f"[GlyphsMCP] Execute endpoint {state}")

	def connectClaudeCode_(self, sender):
		"""Register GlyphsMCP in Claude Code's user scope."""
		self._installWithCLI("Claude Code", "claude", claude_code_command)

	def connectCodex_(self, sender):
		"""Register GlyphsMCP in the config shared by Codex and ChatGPT Desktop."""
		self._installWithCLI("Codex / ChatGPT Desktop", "codex", codex_command)

	def connectOpenCode_(self, sender):
		"""Register GlyphsMCP in OpenCode's global configuration."""
		self._installWithCLI(
			"OpenCode",
			"opencode",
			opencode_command,
			confirmation_note=(
				"An existing server with the same name will be replaced. "
				"Other OpenCode settings will be preserved."
			),
			success_note="Restart OpenCode to load the new connection.",
		)

	def connectVSCode_(self, sender):
		"""Register GlyphsMCP in the active VS Code user profile."""
		self._installWithCLI("Visual Studio Code", "code", vscode_command)

	def connectCursor_(self, sender):
		"""Merge GlyphsMCP into Cursor's global MCP configuration."""
		spec = self._connectionSpec()
		path = os.path.expanduser("~/.cursor/mcp.json")
		if not self._confirmConnection(
			"Connect Cursor?",
			f"Add {spec['name']} to {path}? Existing MCP servers will be preserved.",
		):
			return

		try:
			backup = write_cursor_config(path, spec)
			detail = f"Connected as {spec['name']}."
			if backup:
				detail += f" Backup: {backup}"
			self._showConnectionAlert("Cursor is connected", detail)
		except Exception as error:
			self._showConnectionAlert(
				"Cursor could not be connected",
				f"No configuration was replaced. {error}",
			)

	def copyGenericConfig_(self, sender):
		"""Copy a portable stdio MCP configuration to the clipboard."""
		config = json.dumps(generic_config(self._connectionSpec()), indent=2)
		self._copyText(config)
		self._showConnectionAlert(
			"MCP configuration copied",
			"Paste it into a client that supports the mcpServers configuration format.",
		)

	def openDocumentation_(self, sender):
		"""Open the GitHub documentation in the default browser."""
		url = NSURL.URLWithString_(DOCS_URL)
		NSWorkspace.sharedWorkspace().openURL_(url)

	@objc.python_method
	def _connectionSpec(self):
		port = int(Glyphs.defaults[PREF_PORT] or DEFAULT_PORT)
		uvx_path = self._findExecutable("uvx") or "uvx"
		repo = self._repo_path
		if repo:
			python_path = os.path.join(repo, ".venv", "bin", "python")
			server_path = os.path.join(repo, "glyphs_mcp_server.py")
			if not (os.path.isfile(python_path) and os.path.isfile(server_path)):
				repo = None
		return build_server_spec(Glyphs.versionNumber, port, repo, uvx_path)

	@objc.python_method
	def _findExecutable(self, name):
		candidates = [
			shutil.which(name),
			os.path.expanduser(f"~/.local/bin/{name}"),
			f"/opt/homebrew/bin/{name}",
			f"/usr/local/bin/{name}",
		]
		if name == "claude":
			candidates.append(os.path.expanduser("~/.claude/local/claude"))
		elif name == "opencode":
			candidates.append(os.path.expanduser("~/.opencode/bin/opencode"))
		elif name == "code":
			candidates.extend((
				"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
				os.path.expanduser("~/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
			))
		for candidate in candidates:
			if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
				return candidate
		return None

	@objc.python_method
	def _installWithCLI(
		self,
		label,
		executable_name,
		command_builder,
		confirmation_note=None,
		success_note=None,
	):
		spec = self._connectionSpec()
		executable = self._findExecutable(executable_name)
		fallback_command = command_builder(spec, executable_name)
		if executable is None:
			command_text = shlex.join(fallback_command)
			self._copyText(command_text)
			self._showConnectionAlert(
				f"{label} command copied",
				f"GlyphsMCP could not find {executable_name}. Paste the command into Terminal after installing it.",
			)
			return

		command = command_builder(spec, executable)
		confirmation = f"Register {spec['name']} using:\n\n{shlex.join(command)}"
		if confirmation_note:
			confirmation += f"\n\n{confirmation_note}"
		if not self._confirmConnection(
			f"Connect {label}?",
			confirmation,
		):
			return

		thread = threading.Thread(
			target=self._runClientInstaller,
			args=(label, command, fallback_command, success_note),
			daemon=True,
		)
		thread.start()

	@objc.python_method
	def _runClientInstaller(self, label, command, fallback_command, success_note):
		try:
			result = subprocess.run(
				command,
				capture_output=True,
				text=True,
				timeout=30,
				check=False,
			)
			output = (result.stdout or result.stderr or "").strip()
			AppHelper.callAfter(
				self._finishClientInstaller,
				label,
				result.returncode,
				output,
				fallback_command,
				success_note,
			)
		except Exception as error:
			AppHelper.callAfter(
				self._finishClientInstaller,
				label,
				1,
				str(error),
				fallback_command,
				success_note,
			)

	@objc.python_method
	def _finishClientInstaller(
		self,
		label,
		return_code,
		output,
		fallback_command,
		success_note,
	):
		if return_code == 0:
			detail = output or "GlyphsMCP was added successfully."
			if success_note:
				detail += f"\n\n{success_note}"
			self._showConnectionAlert(
				f"{label} is connected",
				detail,
			)
			return

		self._copyText(shlex.join(fallback_command))
		detail = output[-1200:] if output else "The client command failed."
		self._showConnectionAlert(
			f"{label} could not be connected",
			f"{detail}\n\nThe command was copied so you can retry it in Terminal.",
		)

	@objc.python_method
	def _confirmConnection(self, title, detail):
		alert = NSAlert.alloc().init()
		alert.setMessageText_(title)
		alert.setInformativeText_(detail)
		alert.addButtonWithTitle_("Connect")
		alert.addButtonWithTitle_("Cancel")
		return alert.runModal() == NSAlertFirstButtonReturn

	@objc.python_method
	def _showConnectionAlert(self, title, detail):
		alert = NSAlert.alloc().init()
		alert.setMessageText_(title)
		alert.setInformativeText_(detail)
		alert.addButtonWithTitle_("OK")
		alert.runModal()

	@objc.python_method
	def _copyText(self, text):
		pb = NSPasteboard.generalPasteboard()
		pb.clearContents()
		pb.setString_forType_(text, "public.utf8-plain-text")

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
