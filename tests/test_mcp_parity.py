import ast
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import glyphs_mcp_server as server


ROOT = Path(__file__).resolve().parents[1]
HANDLERS_PATH = ROOT / "plugin/GlyphsMCP.glyphsPlugin/Contents/Resources/handlers.py"
PLUGIN_PATH = ROOT / "plugin/GlyphsMCP.glyphsPlugin/Contents/Resources/plugin.py"
NEW_TOOLS = {
    "analyze_kerning_groups", "auto_kern", "check_font_name",
    "check_glyphset_coverage", "check_language_support", "create_recipe",
    "delete_recipe", "generate_box_drawing", "get_recipe", "get_recipe_step",
    "list_recipes", "review_production", "smart_scale",
}


def load_handlers():
    spec = importlib.util.spec_from_file_location("public_handlers", HANDLERS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MCPToolCatalogTests(unittest.TestCase):
    def test_catalog_has_exactly_54_tools_and_excludes_proofing(self):
        tree = ast.parse((ROOT / "glyphs_mcp_server.py").read_text())
        tools = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                for decorator in node.decorator_list
            )
        }
        self.assertEqual(len(tools), 54)
        self.assertEqual(len(server.mcp._tool_manager._tools), 54)
        self.assertTrue(NEW_TOOLS <= tools)
        self.assertTrue({"init_font_proof", "generate_font_proof"}.isdisjoint(tools))

    @patch.object(server, "_post", return_value={"ok": True})
    def test_rmx_scale_payload_disables_fallback_by_default(self, post):
        server.rmx_scale("R", width=[95, 90], weight=[0, 10])
        post.assert_called_once_with("/api/filters/rmx/scale", {
            "glyphName": "R", "width": [95, 90], "height": 100,
            "weight": [0, 10], "adjustSpace": 0, "verticalShift": 0,
            "allowFallback": False,
        })

    @patch.object(server, "_post", return_value={"ok": True})
    def test_rmx_tune_payload_supports_blend_and_all_masters(self, post):
        server.rmx_tune("R", blend=0.5, all_masters=True)
        payload = post.call_args.args[1]
        self.assertEqual(payload["blend"], 0.5)
        self.assertTrue(payload["allMasters"])

    @patch.object(server, "_get", return_value={"glyphs": []})
    def test_list_glyphs_forwards_filters(self, get):
        server.list_glyphs(category="Letter", limit=25)
        get.assert_called_once_with("/api/font/glyphs?category=Letter&limit=25")

    @patch.object(server, "_get", return_value={"pairs": []})
    def test_get_kerning_forwards_filters(self, get):
        server.get_kerning(master_id="M1", left="A", limit=50)
        get.assert_called_once_with("/api/font/kerning?master=M1&left=A&limit=50")


class PluginRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handlers = load_handlers()

    def test_required_internal_routes_are_registered(self):
        expected = {
            ("POST", "/api/font/glyphs/bulk-create"),
            ("POST", "/api/font/export-instance"),
            ("POST", "/api/font/box-drawing/generate"),
            ("POST", "/api/font/kerning/groups/analyze"),
            ("POST", "/api/font/kerning/auto"),
            ("POST", "/api/font/smart-scale"),
            ("POST", "/api/font/production/review"),
            ("POST", "/api/filters/rmx/scale"),
            ("POST", "/api/filters/rmx/tune"),
        }
        self.assertTrue(expected <= set(self.handlers.ROUTES))

    def test_box_drawing_helper_generates_light_horizontal(self):
        paths, error = self.handlers._bd_generate_paths_for_codepoint(
            0x2500, 600, 800, -200, 50, 90, 35,
        )
        self.assertIsNone(error)
        self.assertGreaterEqual(len(paths), 2)

    def test_rmx_parameter_values_resolve_per_master(self):
        class Master:
            id = "M2"
            name = "Bold"

        self.assertEqual(
            self.handlers._rmx_parameter_for_master([90, 110], Master(), 1, 2),
            110,
        )
        with self.assertRaises(ValueError):
            self.handlers._rmx_parameter_for_master([90, 100, 110], Master(), 1, 2)

    def test_plugin_imports_objc_for_python_method_decorators(self):
        tree = ast.parse(PLUGIN_PATH.read_text())
        imported_names = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertIn("objc", imported_names)

    def test_manual_server_toggle_shows_start_and_stop_alerts(self):
        tree = ast.parse(PLUGIN_PATH.read_text())
        plugin_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "GlyphsMCP"
        )
        methods = {
            node.name: node
            for node in plugin_class.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("_showServerStatusAlert", methods)
        alert_calls = [
            node for node in ast.walk(methods["toggleServer_"])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_showServerStatusAlert"
        ]
        self.assertEqual(len(alert_calls), 2)

    def test_connect_menu_exposes_supported_clients(self):
        source = PLUGIN_PATH.read_text()
        for label in (
            "Claude Code…",
            "Codex / ChatGPT Desktop…",
            "OpenCode…",
            "Visual Studio Code…",
            "Cursor…",
            "Other MCP Client…",
        ):
            self.assertIn(label, source)
        self.assertNotIn("Copy Config for Visual Studio Code", source)

    def test_opencode_default_install_path_is_discoverable(self):
        source = PLUGIN_PATH.read_text()
        self.assertIn('~/.opencode/bin/opencode', source)

    def test_recipe_crud_and_step_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_file = Path(directory) / "handlers.py"
            fake_file.touch()
            content = "# Recipe: Test\n\n## Steps\n\n### 1. Inspect\nRun checks.\n"
            with patch.object(self.handlers, "__file__", str(fake_file)):
                status, created = self.handlers.handle_create_recipe(
                    None, body={"name": "test_recipe", "content": content},
                )
                self.assertEqual(status, 201)
                status, step = self.handlers.handle_get_recipe_step(None, name="test_recipe", step="1")
                self.assertEqual((status, step["title"]), (200, "Inspect"))
                status, listed = self.handlers.handle_list_recipes(None)
                self.assertEqual(listed["recipes"][0]["name"], "test_recipe")
                status, deleted = self.handlers.handle_delete_recipe(None, name="test_recipe")
                self.assertEqual((status, deleted["deleted"]), (200, True))


if __name__ == "__main__":
    unittest.main()
