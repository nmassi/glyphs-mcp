# Recipe: Audit Font Consistency

Full consistency audit of a font across all masters. Run checks in dependency order — later checks assume earlier ones pass.

## Steps

### 1. Font overview
- `get_font_info` — family name, UPM, glyph count
- `get_masters` — list masters with metrics (xHeight, capHeight, ascender, descender)
- `list_glyphs` — get full glyph inventory

### 2. Stem consistency
Run first — stems are the foundation for all other checks.

- `compare_stems` with LC set: `n, h, i, l, m, u, o, c, e, b, d, p, q, r, s, t`
- `compare_stems` with UC set: `H, I, L, E, F, T, U, D, O, C, G, B, R, P, K, S`

Review results per master. Flag any inconsistencies (red) vs optical compensations (yellow).

### 3. Typographic color
Only meaningful if stems are consistent.

- `audit_font_color` — full font audit across all masters

Review density ratios. Glyphs significantly darker/lighter than reference need attention.

### 4. Width proportions
- `compare_proportions` — checks width groups, ordering, industry ranges

Flag width outliers and ordering violations (e.g., m narrower than n).

### 5. Diagonal weights
- `check_diagonal_weights` — perpendicular stem thickness of diagonal glyphs

Compare diagonal stems to straight reference. Group consistency (v/w/y, V/A/W).

### 6. Junction thinning
- `check_junctions` — stem thinning at arch/bowl junctions

Check arch group consistency (n vs m). Report thinning percentages.

### 7. Related forms
- `check_related_forms` — figure/letter cross-validation
- `check_punctuation` — punctuation width matching and ratios

### 8. Spacing and kerning
- `analyze_spacing` — sidebearing rules, counter validation, word space
- `analyze_kerning` — critical pair coverage, exception ratio

### 9. Summary report
Compile findings into priority order:
1. **Critical** (red): Stem inconsistencies, broken proportions
2. **Review** (orange): Measurement artifacts, needs manual check
3. **Optical** (yellow): Intentional compensations, likely correct
4. **Pass** (green): Within tolerance

Present per-master summary with glyph counts per category.
