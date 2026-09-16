# Recipe: Spacing Workflow

Systematic spacing of a font following Cheng/Briem/Ruder methodology.
Sidebearings first, kerning second. A well-spaced font should work adequately without kerning.

## Prerequisites
- All glyphs should be drawn and stable (avoid re-spacing after glyph changes)
- Stems should be consistent (run `audit_consistency` first)

## Steps

### 1. Establish reference sidebearings
Set spacing for the control characters first — everything else derives from these.

**Tools to use:**
- `get_glyph` on `n`, `o`, `H`, `O` — check current sidebearings
- `get_glyph_svg` on same — visual check

**Reference pairs (Cheng §4.3):**
- LC: `n` (straight reference), `o` (round reference)
- UC: `H` (straight reference), `O` (round reference)

The n/o sidebearing ratio should be ~1.4-1.6 (n wider than o because straight sides need more air).

### 2. Analyze current spacing
- `analyze_spacing` — runs Tracy/Smith per-glyph rules, checks side-type ordering, counter validation, word space

Review the report for:
- Side-type ordering violations (straight > round > diagonal)
- Counter vs sidebearing ratio (LSB should be 25-50% of counter width)
- Word space (should be 1/5 to 1/2 em)

### 3. Fix sidebearings by category
Work in this order — each group builds on the previous:

**Straight-sided glyphs** (H, I, l, i, etc.):
- Should have the largest sidebearings
- LC straight = n's sidebearings
- UC straight = H's sidebearings

**Round-sided glyphs** (O, o, C, c, etc.):
- Slightly less than straight (~85-95%)
- Must be optically balanced, not mathematically equal

**Diagonal-sided glyphs** (V, W, v, w, A, etc.):
- Smallest sidebearings
- Triangular shapes need the least air

**Open-sided glyphs** (L, T, r, etc.):
- Open side gets less space than closed side

### 4. Test with spacing strings
For each adjusted glyph:
- `get_spacing_strings` — generates test strings (three-at-a-time, Briem, Ruder)
- Review the strings visually in GlyphsApp Edit view

**Ruder test**: words in left columns are hard to space, right columns are easy. When correct, all columns have equal color.

**Briem test**: each letter between o's and n's (e.g., `nonon`, `onooo`). Check for even rhythm.

### 5. Re-analyze and iterate
- `analyze_spacing` — verify improvements
- Compare before/after sidebearing values
- Check that no new ordering violations were introduced

### 6. Proceed to kerning
Only after sidebearings are stable:
- `analyze_kerning_groups` with `apply=false` — preview group assignments
- `analyze_kerning_groups` with `apply=true` — assign groups
- `analyze_kerning` — check critical pair coverage

## Key principles (Cheng)
- "Spacing as you go" — space each character after drawing, don't batch
- Good spacing should be imperceptible (Frutiger)
- Kerning is support for initial spacing, not a replacement
- It may be necessary to redesign letters to resolve spacing issues
