# Curve Continuity Evidence

Use this reference only when a task inspects measured Bezier continuity or joins. It is read-only evidence, not an aesthetic verdict.

## Continuity levels

- C0 is positional continuity: the endpoints meet.
- G1 is tangent direction continuity: incoming and outgoing tangents are aligned.
- G2 (curvature continuity) is not measured by available tools. Never claim it, and say so when it matters.

## Coordinates and vectors

- Font units, X right and Y up, without integer rounding.
- A cubic is supplied as absolute points `P0`, `C1`, `C2`, `P3` with node indices, node types, and smooth flags.
- Handle vectors:
  - outgoing handle at `P0`: `C1 - P0`;
  - incoming handle at `P3`: `C2 - P3`;
  - forward incoming tangent: `P3 - C2`, the opposite sign of the incoming handle.
- Zero-length handles make the tangent angle unmeasured; preserve that status.
- A closed seam is explicit; do not assume it is smooth.
- An audit `path_index` addresses the selected-path array while a layer `shape_index` addresses foreground shapes. Equal numbers do not establish a mapping.

## Interpretation guardrails

- A measured status is not a pass.
- Corners, asymmetry, contrast, and optical compensation can be intentional. Do not prescribe universal smoothness.
- Separate the numeric measurement, model inference, and aesthetic judgment explicitly.
- Explain values inside the captured scope only. Never expand to the whole glyph or layer, and never recompute or reuse earlier measurements.
- Report `measured`, `unmeasured`, and `failed` states as given; they are not proof of quality.
