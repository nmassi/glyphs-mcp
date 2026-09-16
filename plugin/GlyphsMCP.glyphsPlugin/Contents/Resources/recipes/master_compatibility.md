# Recipe: Master Compatibility Check

Verify interpolation compatibility between masters before exporting variable fonts or generating instances.

## When to use
- Before exporting a variable font
- After adding new glyphs to a multi-master font
- After significant edits to glyph outlines
- When interpolation preview shows unexpected results

## Steps

### 1. Run compatibility check
- `check_compatibility` — checks all glyphs across all masters

Reports per-glyph:
- Path count mismatches
- Node count mismatches per path
- Component count/order mismatches
- Anchor count/name mismatches
- Starting point mismatches

### 2. Fix critical issues first
Priority order:
1. **Path count mismatches** — glyph structure differs between masters
2. **Node count mismatches** — points added/removed in one master
3. **Component mismatches** — different component structure
4. **Anchor mismatches** — missing anchors for mark positioning

### 3. Verify metrics interpolation
- `get_masters` — check that metrics interpolate smoothly
- xHeight, capHeight, ascender, descender should change gradually
- Sudden jumps indicate potential issues

### 4. Test interpolation
Generate test instances at intermediate positions:
- Use GlyphsApp's preview slider
- Check for kinks, bumps, or collapsing shapes
- Pay special attention to diagonals and curves

### 5. Check alignment zones
Overshoots should be consistent across masters:
- `check_overshoots` — verify overshoot consistency
- Alignment zones should be proportional to weight

## Common problems
- **Crossed paths**: paths that work in extremes but cross at intermediate weights
- **Kinks**: smooth connections that break during interpolation (incompatible handles)
- **Disappearing counters**: counters that close at heavy weights
- **Starting point mismatch**: same path structure but different starting nodes
