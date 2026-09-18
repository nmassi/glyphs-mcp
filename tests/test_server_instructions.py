import unittest

import glyphs_mcp_server as server


class ServerInstructionsTests(unittest.TestCase):
    def test_initialization_exposes_concise_cross_tool_instructions(self):
        instructions = server.mcp._mcp_server.create_initialization_options().instructions

        self.assertIsInstance(instructions, str)
        self.assertEqual(instructions, server.SERVER_INSTRUCTIONS)
        self.assertIn("Inspect the open font", instructions)
        self.assertIn("Prefer dedicated GlyphsMCP tools", instructions)
        self.assertIn("verify compatibility after edits", instructions)
        self.assertLessEqual(len(instructions), 512)


if __name__ == "__main__":
    unittest.main()
