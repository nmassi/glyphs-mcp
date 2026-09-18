import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import glyphs_export


class CompletedProcess:
    returncode = 0
    stdout = ""
    stderr = ""


class GlyphsExportTests(unittest.TestCase):
    def test_export_source_organizes_all_formats_without_deleting_prior_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Family.glyphs"
            source.write_text("familyName = Family;", encoding="utf-8")
            now = datetime(2026, 9, 17, 12, 34, 56, tzinfo=timezone.utc)

            def fake_run(command, **kwargs):
                config_path = Path(command[command.index("--config") + 1])
                report_path = Path(command[command.index("--json-output") + 1])
                config = json.loads(config_path.read_text(encoding="utf-8"))
                settings = config["settings"]
                output = Path(settings["outputPath"])
                outline = settings["outlineFormats"][0]
                containers = settings["containerFormats"]
                include_variable = "instances" not in config["sourceFiles"][0]

                records = []
                suffixes = []
                if outline == "cff":
                    suffixes = [".otf"]
                elif containers == ["standard"]:
                    suffixes = [".ttf"]
                else:
                    suffixes = [".woff", ".woff2"]

                for suffix in suffixes:
                    generated = output / f"Family-Regular{suffix}"
                    generated.write_bytes(b"font")
                    records.append({
                        "sourceFilePath": str(source),
                        "instanceName": "Regular",
                        "exportFilePath": str(generated),
                        "warnings": [],
                        "errors": [],
                    })
                if include_variable:
                    generated = output / "Family-VF.ttf"
                    generated.write_bytes(b"variable")
                    records.append({
                        "sourceFilePath": str(source),
                        "instanceName": "Variable",
                        "instanceType": "variable",
                        "exportFilePath": str(generated),
                        "warnings": [{"title": "Variable warning"}],
                        "errors": [],
                    })
                report_path.write_text(
                    "".join(json.dumps(record) + "\n" for record in records),
                    encoding="utf-8",
                )
                return CompletedProcess()

            with patch.object(glyphs_export.subprocess, "run", side_effect=fake_run):
                first = glyphs_export.export_source(
                    source,
                    app="/Applications/Glyphs 4.app",
                    glyphs_executable="/fake/glyphs",
                    now=now,
                )
                second = glyphs_export.export_source(
                    source,
                    glyphs_executable="/fake/glyphs",
                    now=now,
                )

            self.assertTrue(first["ok"])
            self.assertEqual(len(first["exportedFiles"]), 5)
            self.assertEqual(
                {Path(path).parent.name for path in first["exportedFiles"]},
                {"otf", "ttf", "woff", "woff2", "variable"},
            )
            self.assertEqual(Path(first["outputDirectory"]).name, "2026-09-17_12-34-56")
            self.assertEqual(Path(second["outputDirectory"]).name, "2026-09-17_12-34-56_2")
            self.assertEqual(first["warnings"], ["Variable warning"])
            self.assertIn("Export complete", first["exportLog"])
            self.assertIn(f"Output: {first['outputDirectory']}", first["exportLog"])
            self.assertIn("Exported files: 5", first["exportLog"])
            self.assertIn(glyphs_export.ANSI_GREEN, first["exportLogAnsi"])
            self.assertTrue(Path(first["reportPath"]).is_file())
            self.assertFalse((Path(first["outputDirectory"]) / ".glyphs-cli-tmp").exists())
            self.assertTrue(all("--plugins" in command for command in first["commands"]))

    def test_export_configs_exclude_variable_instances_from_cff_and_web(self):
        source = Path("/tmp/Family.glyphs")
        output = Path("/tmp/export")

        static = glyphs_export._export_config(source, output, "cff", ["standard"], False)
        variable = glyphs_export._export_config(source, output, "tt", ["standard"], True)

        self.assertEqual(
            static["sourceFiles"][0]["instances"],
            [{"selector": {"type": "single"}}],
        )
        self.assertNotIn("instances", variable["sourceFiles"][0])

    def test_problem_text_omits_transient_instance_paths(self):
        problem = {
            "title": "Black",
            "instancePath": "/tmp/export/Family-Black.otf",
            "description": "Stems can't be zero.",
        }

        self.assertEqual(glyphs_export._problem_text(problem), "Black: Stems can't be zero.")

    def test_export_rejects_missing_or_invalid_sources(self):
        missing = glyphs_export.export_source("missing.glyphs", glyphs_executable="/fake/glyphs")
        invalid = glyphs_export.export_source(__file__, glyphs_executable="/fake/glyphs")

        self.assertFalse(missing["ok"])
        self.assertIn("Source not found", missing["error"])
        self.assertIn("Status: FAILED", missing["exportLog"])
        self.assertIn("Source not found", missing["exportLog"])
        self.assertIn(glyphs_export.ANSI_RED, missing["exportLogAnsi"])
        self.assertFalse(invalid["ok"])
        self.assertIn("Source must be", invalid["error"])

    def test_find_glyphs_cli_prefers_current_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / "python"
            glyphs = Path(directory) / "glyphs"
            glyphs.write_text("#!/bin/sh\n", encoding="utf-8")
            glyphs.chmod(0o755)

            with patch.object(glyphs_export.sys, "executable", str(python)):
                self.assertEqual(glyphs_export.find_glyphs_cli(), str(glyphs))

    def test_ansi_log_colors_complete_error_block_red(self):
        result = {"ok": False, "errors": ["Black: Stems can't be zero."]}

        log = glyphs_export.format_export_log(result, color=True)

        self.assertIn(f"{glyphs_export.ANSI_RED}Errors:{glyphs_export.ANSI_RESET}", log)
        self.assertIn(
            f"{glyphs_export.ANSI_RED}- Black: Stems can't be zero.{glyphs_export.ANSI_RESET}",
            log,
        )


if __name__ == "__main__":
    unittest.main()
