# Incorporate Type Designer Skill

## Objective

Ship concise GlyphsMCP-specific guidance through MCP server instructions and a bundled `type-designer` skill.

## Problem

GlyphsMCP currently exposes only per-tool docstrings. It has no server-level instructions, and its type-design workflow guidance is available only through a personal global skill that is not versioned with the project.

## Why

Users should receive the essential safe workflow whenever GlyphsMCP is connected, while distributors should be able to install a small, project-owned type-design skill without copying a large personal configuration.

## Scope

- Add minimal MCP server instructions through the supported `FastMCP` API.
- Add a concise bundled `skills/type-designer/SKILL.md`.
- Include the skill in built distributions and document its current installation model.
- Add focused regression tests.

## Constraints

- Preserve all pre-existing uncommitted work.
- Do not implement unsupported SEP-2640 protocol hooks against the current MCP SDK.
- Keep runtime instructions concise and cross-tool; keep tool-specific behavior in tool descriptions.
- Technical artifacts remain in English.

## Authorization

The user explicitly authorized incorporating the type-designer skill into GlyphsMCP.

## TDD

- Mode: strict TDD enabled
- Source: active project instructions
- Runner: `.venv/bin/python -m unittest discover -s tests -v`
- Required cycle: RED → GREEN → REFACTOR

## Delivery

- Strategy: `ask-on-risk`
- Forecast: approximately 150 authored changed lines, below the 400-line review-slice threshold.
- Branch: `feat/export-font-tool` (pre-existing working branch)
- RDD: disabled/unmanaged

## Tasks

- [x] **TDS-1 — Add minimal server instructions**
  - Write a focused failing test for initialization metadata.
  - Add the minimal `FastMCP` instruction string.
  - Run the focused test and full unit suite.
  - Commit the work unit with a Conventional Commit message.

- [ ] **TDS-2 — Bundle the minimal type-designer skill**
  - Write failing tests for skill presence, required frontmatter, and package inclusion.
  - Add the concise skill, package metadata, and README installation note.
  - Verify the wheel contains the skill and run the full unit suite.
  - Commit the work unit with a Conventional Commit message.

## Acceptance Criteria

- MCP initialization exposes concise cross-tool instructions.
- The bundled skill follows the LLM-first skill structure and stays within the prescribed size budget.
- Built distributions contain `skills/type-designer/SKILL.md`.
- Documentation does not claim live SEP-2640 serving while the installed SDK lacks support.
- All applicable tests and packaging checks pass.
- Existing unrelated dirty changes remain uncommitted and intact.

## Progress

- Current task: TDS-2
- TDS-1 RED: `.venv/bin/python -m unittest discover -s tests -p 'test_server_instructions.py' -v` failed as expected because initialization instructions were `None` (`1` test, `1` failure).
- TDS-1 GREEN: the same focused command passed (`1` test), then `.venv/bin/python -m unittest discover -s tests -v` passed (`44` tests), and `git diff --check` passed.
- TDS-1 runtime harness: N/A — initialization metadata is exercised directly through `create_initialization_options()` without starting the long-lived stdio server.
- TDS-1 rollback boundary: remove `tests/test_server_instructions.py`, `SERVER_INSTRUCTIONS`, and the `instructions` argument in `glyphs_mcp_server.py`.
- TDS-1 commit evidence: pending work-unit commit.
- Next step: begin TDS-2 with failing skill-presence and package-inclusion tests.
