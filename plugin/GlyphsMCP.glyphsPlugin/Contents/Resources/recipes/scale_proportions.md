# Recipe: Scale Proportions

Scale glyph proportions (width/height) while maintaining stem weight.

## Parameters needed
- `width`: horizontal scale factor (e.g., 1.15 = 15% wider)
- `height`: vertical scale factor (e.g., 1.0 = no change)
- `weight`: target weight factor (1.0 = maintain original stems)
- `glyphNames`: which glyphs to scale (empty = all exporting)
- `masterId`: which master (empty = all)

## Steps

### 1. Analyze current state
Before any changes, measure and record reference values.

**Tools to use:**
- `get_font_info` — get UPM, masters, axes
- `get_masters` — get xHeight, capHeight per master
- `measure_stems` on `n` — LC vertical stem reference
- `measure_stems` on `o` — LC horizontal stem reference
- `measure_stems` on `H` — UC vertical stem reference
- `measure_stems` on `O` — UC horizontal stem reference
- `get_glyph_svg` on `n`, `o`, `H`, `O` — visual baseline (per master)

**Record these values — you will compare against them after scaling.**

Report a table:
```
Master | xHeight | capHeight | n v-stem | o h-stem | H v-stem | O h-stem
-------|---------|-----------|---------|---------|---------|--------
EL     | 532     | 700       | 20      | 19      | 22      | 20
Black  | 584     | 700       | 220     | 171     | 230     | 175
```

### 2. Identify diagonal glyphs
Check which glyphs in the target set are diagonal-dominant:
`v, w, x, y, z, k, V, W, X, Y, Z, K`

These require special handling (angle-compensated OffsetCurve).

### 3. Execute Smart Scale
Call `smart_scale` with the parameters.

The tool automatically:
- Scales non-diagonal glyphs + two-pass OffsetCurve (X then Y)
- Scales diagonal glyphs + single OffsetCurve X with 1/cos(angle) factor
- Corrects baseline shift from OffsetCurve Y
- Updates xHeight metric from n's left stem top

### 4. Verify stems post-scale
Repeat the same measurements from Step 1:
- `measure_stems` on `n`, `o`, `H`, `O`
- `get_glyph_svg` on the same glyphs

**Compare with pre-scale values.** Report delta table:
```
Master | Glyph | Pre  | Post | Delta | Status
-------|-------|------|------|-------|-------
Black  | n v   | 220  | 221  | +1    | OK
Black  | o h   | 171  | 170  | -1    | OK
```

Acceptable tolerance: +/-2u for straight stems, +/-5u for diagonals.

### 5. Verify diagonal glyphs
For each diagonal glyph processed:
- `get_glyph_svg` — check visually that proportions look correct
- Verify height matches xHeight (LC) or capHeight (ascender glyphs)
- Check stem weight against reference (should be within +/-5u)

### 6. Verify metrics
- `get_masters` — confirm xHeight was updated correctly
- xHeight should equal the flat stem top of `n` (NOT arch top)
- capHeight should be unchanged (unless explicitly scaled)

### 7. Report and confirm
Present a summary to the user:
- Pre/post stem comparison table
- Pre/post SVG comparison of key glyphs
- Any glyphs outside tolerance
- Updated metrics

Ask for confirmation before proceeding with additional work.

## Known limitations
- OffsetCurve drift: +/-1-2u at heavy weights on curved segments
- Diagonal compensation assumes relatively uniform stroke angles
- Ascender diagonals (k/K): only scale Y if capHeight changed
- Very thin weights (<30u stems): compensation may be < 1u (negligible)
