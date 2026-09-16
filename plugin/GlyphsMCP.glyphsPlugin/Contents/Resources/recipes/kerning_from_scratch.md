# Recipe: Kerning From Scratch

Systematic kerning workflow. Spacing must be finalized first — kerning supports spacing, not replaces it.

## Prerequisites
- Sidebearings finalized (run `spacing_workflow` recipe first)
- A well-spaced font should work adequately WITHOUT kerning

## Steps

### 1. Assign kerning groups
Groups allow one kern pair to affect many glyph combinations.

- `analyze_kerning_groups` with `apply=false` — preview assignments (dry run)
- Review proposed groups: dictionary matches, component inheritance, contour analysis
- `analyze_kerning_groups` with `apply=true, overwrite=false` — apply without overwriting existing

**GlyphsApp convention:**
- `leftKerningGroup` = used when glyph is on the RIGHT side of a pair
- `rightKerningGroup` = used when glyph is on the LEFT side of a pair

### 2. Check critical pair coverage
- `analyze_kerning` — reports coverage of ~80 essential pairs (Cheng §8)

Critical pair categories:
- UC-UC: AV, AT, AW, FA, LT, PA, TA, TO, VA, WA, YA...
- UC-lc: Av, Aw, Ay, Fa, Fe, Fo, Ta, Te, To, Tr, Tu, Ty, Va, Vo, Wa, We, Yo...
- lc-lc: av, aw, ay, ov, ow, oy, va, ve, vo, wa, we, wo, ya, ye, yo...

### 3. Set kern pairs for critical combinations
Start with the most visible problem pairs:

**Priority 1 — Large gaps (diagonal + round):**
- AV, AW, AT, AY — diagonal meets straight/diagonal
- Ta, Te, To, Tr — T overhang creates large gap
- VA, Vo, Wa, We — V/W overhang

**Priority 2 — Round combinations:**
- OA, OT, OV — round meets straight
- oc, od — round meets vertical

**Priority 3 — Punctuation:**
- Period/comma after V, W, T, Y, f
- Quotes before/after various letters

### 4. Verify with test strings
For each kerned glyph:
- `get_spacing_strings` — review in context
- Check that kerning doesn't create new problems (over-kerning)

### 5. Analyze quality
- `analyze_kerning` — full analysis
- Check exception ratio: >40% exceptions suggests sidebearing issues (fix spacing, not kerning)
- Verify cross-master consistency

### 6. Final review
- Test at multiple sizes (display and text)
- Check that font still works acceptably with kerning disabled
- Review any orphaned pairs (pairs without group coverage)

## Key principles (Cheng §8)
- Kerning is tedious but essential — focus and concentration required
- Manual fine-tuning always needed after automated tools
- Too many kern exceptions = sidebearing problem, not kerning problem
- Optical kerning (Adobe) should only override when font is poorly crafted
