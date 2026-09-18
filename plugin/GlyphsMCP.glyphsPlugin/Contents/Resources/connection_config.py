# encoding: utf-8
"""Pure helpers for registering GlyphsMCP with local MCP clients."""

import copy
import json
import os
import shutil
import tempfile
import time


def discover_repo_path(plugin_file):
	"""Find a development checkout from a breadcrumb or a symlinked plugin."""
	resources_dir = os.path.dirname(os.path.realpath(plugin_file))
	breadcrumb = os.path.join(resources_dir, ".repo_path")
	try:
		with open(breadcrumb, "r", encoding="utf-8") as breadcrumb_file:
			path = breadcrumb_file.read().strip()
			if path and os.path.isdir(path):
				return path
	except (OSError, IOError):
		pass

	checkout = os.path.abspath(os.path.join(resources_dir, "..", "..", "..", ".."))
	if (
		os.path.isfile(os.path.join(checkout, "pyproject.toml"))
		and os.path.isfile(os.path.join(checkout, "glyphs_mcp_server.py"))
	):
		return checkout
	return None


def server_name(version_number=None):
	"""Return a per-app MCP server name: Glyphs 4 keeps 'glyphs-mcp', Glyphs 3 uses 'glyphs-mcp-3'."""
	try:
		major = int(str(version_number).split(".", 1)[0])
	except (TypeError, ValueError):
		major = None
	return "glyphs-mcp-3" if major == 3 else "glyphs-mcp"


def build_server_spec(version_number, port, repo_path=None, uvx_path="uvx"):
	"""Build the stdio server entry shared by client-specific formats."""
	if repo_path:
		command = os.path.join(repo_path, ".venv", "bin", "python")
		args = [os.path.join(repo_path, "glyphs_mcp_server.py")]
	else:
		command = uvx_path
		args = ["glyphs-mcp"]

	return {
		"name": server_name(version_number),
		"command": command,
		"args": args,
		"env": {"GLYPHS_URL": f"http://127.0.0.1:{int(port)}"},
	}


def claude_code_command(spec, executable="claude"):
	return [
		executable,
		"mcp", "add",
		"--env", f"GLYPHS_URL={spec['env']['GLYPHS_URL']}",
		"--scope", "user",
		"--transport", "stdio",
		spec["name"],
		"--",
		spec["command"],
		*spec["args"],
	]


def codex_command(spec, executable="codex"):
	return [
		executable,
		"mcp", "add", spec["name"],
		"--env", f"GLYPHS_URL={spec['env']['GLYPHS_URL']}",
		"--",
		spec["command"],
		*spec["args"],
	]


def opencode_command(spec, executable="opencode"):
	return [
		executable,
		"mcp", "add", spec["name"],
		"--env", f"GLYPHS_URL={spec['env']['GLYPHS_URL']}",
		"--",
		spec["command"],
		*spec["args"],
	]


def vscode_command(spec, executable="code"):
	server = _client_server(spec, include_type=True)
	server["name"] = spec["name"]
	return [executable, "--add-mcp", json.dumps(server, separators=(",", ":"))]


def cursor_config(spec):
	return {"mcpServers": {spec["name"]: _client_server(spec, include_type=True)}}


def generic_config(spec):
	return {"mcpServers": {spec["name"]: _client_server(spec)}}


def merge_cursor_config(existing, spec):
	"""Merge one server into a Cursor config without mutating the input."""
	if not isinstance(existing, dict):
		raise ValueError("Cursor MCP configuration must contain a JSON object")
	merged = copy.deepcopy(existing)
	servers = merged.setdefault("mcpServers", {})
	if not isinstance(servers, dict):
		raise ValueError("Cursor mcpServers must contain a JSON object")
	servers[spec["name"]] = _client_server(spec, include_type=True)
	return merged


def write_cursor_config(path, spec, timestamp=None):
	"""Atomically merge a server into Cursor's config and return the backup path."""
	existing = {}
	if os.path.exists(path):
		with open(path, "r", encoding="utf-8") as config_file:
			existing = json.load(config_file)
	merged = merge_cursor_config(existing, spec)
	directory = os.path.dirname(path)
	os.makedirs(directory, exist_ok=True)

	backup = None
	if os.path.exists(path):
		stamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
		backup = f"{path}.backup-{stamp}"
		shutil.copy2(path, backup)

	fd, temporary_path = tempfile.mkstemp(prefix=".mcp-", suffix=".json", dir=directory)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as config_file:
			json.dump(merged, config_file, indent=2)
			config_file.write("\n")
		os.replace(temporary_path, path)
	finally:
		if os.path.exists(temporary_path):
			os.unlink(temporary_path)
	return backup


def _client_server(spec, include_type=False):
	server = {
		"command": spec["command"],
		"args": list(spec["args"]),
		"env": dict(spec["env"]),
	}
	if include_type:
		server["type"] = "stdio"
	return server
