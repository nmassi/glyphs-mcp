import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "plugin/GlyphsMCP.glyphsPlugin/Contents/Resources"
sys.path.insert(0, str(RESOURCES))

import connection_config as config


class ConnectionConfigTests(unittest.TestCase):
    def test_discovers_checkout_from_symlinked_plugin_source(self):
        plugin_file = RESOURCES / "plugin.py"

        self.assertEqual(config.discover_repo_path(str(plugin_file)), str(ROOT))

    def test_server_name_is_app_specific(self):
        self.assertEqual(config.server_name(4.1), "glyphs-mcp")
        self.assertEqual(config.server_name(3.3), "glyphs-mcp-3")
        self.assertEqual(config.server_name(None), "glyphs-mcp")

    def test_release_spec_uses_stable_name_and_active_port(self):
        spec = config.build_server_spec("4.1.2", 7746, uvx_path="/opt/homebrew/bin/uvx")

        self.assertEqual(spec, {
            "name": "glyphs-mcp",
            "command": "/opt/homebrew/bin/uvx",
            "args": ["glyphs-mcp"],
            "env": {"GLYPHS_URL": "http://127.0.0.1:7746"},
        })

    def test_development_spec_uses_root_server_script(self):
        spec = config.build_server_spec(3.3, 7745, repo_path="/repo")

        self.assertEqual(spec["name"], "glyphs-mcp-3")
        self.assertEqual(spec["command"], "/repo/.venv/bin/python")
        self.assertEqual(spec["args"], ["/repo/glyphs_mcp_server.py"])

    def test_client_commands_include_scope_transport_and_bridge_url(self):
        spec = config.build_server_spec(4, 7746)

        self.assertEqual(config.claude_code_command(spec), [
            "claude", "mcp", "add", "--env", "GLYPHS_URL=http://127.0.0.1:7746",
            "--scope", "user", "--transport", "stdio", "glyphs-mcp",
            "--", "uvx", "glyphs-mcp",
        ])
        self.assertEqual(config.codex_command(spec), [
            "codex", "mcp", "add", "glyphs-mcp", "--env",
            "GLYPHS_URL=http://127.0.0.1:7746", "--", "uvx", "glyphs-mcp",
        ])
        self.assertEqual(config.opencode_command(spec), [
            "opencode", "mcp", "add", "glyphs-mcp", "--env",
            "GLYPHS_URL=http://127.0.0.1:7746", "--", "uvx", "glyphs-mcp",
        ])

    def test_vscode_command_contains_one_server_registration(self):
        spec = config.build_server_spec(4, 7746)
        command = config.vscode_command(spec)
        registration = json.loads(command[2])

        self.assertEqual(command[:2], ["code", "--add-mcp"])
        self.assertEqual(registration["name"], "glyphs-mcp")
        self.assertEqual(registration["type"], "stdio")
        self.assertEqual(registration["env"]["GLYPHS_URL"], "http://127.0.0.1:7746")

    def test_generic_config_omits_client_specific_type(self):
        spec = config.build_server_spec(3, 7745)
        server = config.generic_config(spec)["mcpServers"]["glyphs-mcp-3"]

        self.assertNotIn("type", server)
        self.assertEqual(server["env"]["GLYPHS_URL"], "http://127.0.0.1:7745")

    def test_cursor_write_preserves_servers_and_creates_backup(self):
        spec = config.build_server_spec(4, 7746)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".cursor" / "mcp.json"
            path.parent.mkdir()
            original = {"mcpServers": {"existing": {"command": "existing"}}, "setting": True}
            path.write_text(json.dumps(original))

            backup = config.write_cursor_config(str(path), spec, timestamp="test")
            written = json.loads(path.read_text())

            self.assertEqual(json.loads(Path(backup).read_text()), original)
            self.assertEqual(written["mcpServers"]["existing"], {"command": "existing"})
            self.assertEqual(written["mcpServers"]["glyphs-mcp"]["type"], "stdio")
            self.assertTrue(written["setting"])

    def test_cursor_write_does_not_replace_invalid_json(self):
        spec = config.build_server_spec(4, 7746)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mcp.json"
            path.write_text("not json")

            with self.assertRaises(json.JSONDecodeError):
                config.write_cursor_config(str(path), spec, timestamp="test")

            self.assertEqual(path.read_text(), "not json")
            self.assertFalse(Path(f"{path}.backup-test").exists())


class ServerEntrypointTests(unittest.TestCase):
    def test_bridge_url_can_be_set_by_environment(self):
        environment = os.environ.copy()
        environment["GLYPHS_URL"] = "http://127.0.0.1:7746/"
        result = subprocess.run(
            [sys.executable, "-c", "import glyphs_mcp_server; print(glyphs_mcp_server.GLYPHS_URL)"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.stdout.strip(), "http://127.0.0.1:7746")

    def test_packaged_entrypoint_exists(self):
        import glyphs_mcp_server

        self.assertTrue(callable(glyphs_mcp_server.main))


if __name__ == "__main__":
    unittest.main()
