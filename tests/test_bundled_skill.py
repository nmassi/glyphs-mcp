import tomllib
import unittest
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills/type-designer"
SKILL_PATH = SKILL_DIR / "SKILL.md"
REFERENCES_DIR = SKILL_DIR / "references"

REQUIRED_REFERENCES = (
    "type-design-workflows.md",
    "type-dna.md",
    "glyphsmcp.md",
    "audit-contract.md",
    "curve-continuity.md",
    "glyphs4-model.md",
)


class BundledSkillTests(unittest.TestCase):
    def _read_skill(self):
        self.assertTrue(SKILL_PATH.is_file(), "bundled type-designer skill is missing")
        return SKILL_PATH.read_text()

    def test_type_designer_skill_exists(self):
        self.assertTrue(SKILL_PATH.is_file())

    def test_frontmatter_is_complete_and_description_is_one_line(self):
        content = self._read_skill()
        frontmatter = content.split("---", 2)[1]
        description_lines = [
            line for line in frontmatter.splitlines() if line.startswith("description:")
        ]

        self.assertIn("name: type-designer", frontmatter)
        self.assertEqual(len(description_lines), 1)
        self.assertRegex(description_lines[0], r'^description: "Trigger: .+"$')
        self.assertLessEqual(len(description_lines[0]), 263)
        self.assertIn("license: Apache-2.0", frontmatter)
        self.assertIn("metadata:", frontmatter)
        self.assertIn("  author:", frontmatter)
        self.assertIn('  version: "1.1"', frontmatter)

    def test_body_uses_required_sections_and_stays_within_budget(self):
        content = self._read_skill()
        body = content.split("---", 2)[2]
        headings = [
            "## Activation Contract",
            "## Hard Rules",
            "## Decision Gates",
            "## Execution Steps",
            "## Output Contract",
            "## References",
        ]

        positions = [body.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertGreaterEqual(len(body.split()), 180)
        self.assertLessEqual(len(body.split()), 450)

    def test_required_reference_files_exist(self):
        for name in REQUIRED_REFERENCES:
            self.assertTrue(
                (REFERENCES_DIR / name).is_file(),
                f"missing bundled reference: {name}",
            )

    def test_every_reference_file_is_linked_and_no_link_is_broken(self):
        content = self._read_skill()
        links = re.findall(r"\[[^]]+\]\(([^)]+)\)", content)
        expected = sorted(f"references/{name}" for name in REQUIRED_REFERENCES)

        self.assertEqual(sorted(links), expected)
        for relative_path in links:
            self.assertTrue(
                (SKILL_DIR / relative_path).is_file(),
                f"broken skill reference: {relative_path}",
            )

    def test_corpus_preserves_general_practice_and_safe_mcp_guidance(self):
        reference_paths = sorted(REFERENCES_DIR.glob("*.md"))
        corpus = "\n".join(
            path.read_text().lower() for path in (SKILL_PATH, *reference_paths)
        )

        for topic in (
            "skeleton",
            "contrast",
            "spacing",
            "kerning",
            "interpolation",
            "production qa",
            "script",
            "plugin",
            "evidence",
            "confidence",
            "continuity",
            "glyphs 4",
        ):
            self.assertIn(topic, corpus)
        self.assertIn("mark_glyphs=true", corpus)
        self.assertIn("analyze_kerning_groups(apply=false)", corpus)
        self.assertIn("dry_run=true", corpus)

    def test_skill_is_included_in_wheel_configuration(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        includes = config["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]

        self.assertIn("skills/type-designer/**", includes)


if __name__ == "__main__":
    unittest.main()
