# Type Design Workflows

Use this reference for font-agnostic design practice. Establish the design system first; choose software and automation second.

## Diagnose before drawing

Define the intended use, sizes, scripts, weight range, width range, and voice. Inspect representative glyphs rather than isolated details:

- Core system: `H O n o`
- Distinctive forms: `a g R Q S G &`
- Diagonals: `A V W X Y v w x y`
- Figures: `0 1 2 3 4 6 7 8 9`
- Texture: words, paragraphs, punctuation, and mixed case

Classify the issue before acting:

- Structural: skeleton, construction, proportions, width system, vertical metrics, stress
- Modulating: weight, contrast, tension, counters, apertures, terminals, joins
- Textural: spacing, rhythm, color, kerning behavior
- Production: compatibility, components, anchors, features, naming, export settings

Do not redraw a skeleton to solve a spacing problem, or use kerning to hide bad sidebearings.

## Establish the core system

Start with `H O n o`. They define straight and round stems, overshoot, counters, proportions, sidebearings, and upper/lowercase relationships. Resolve them before distinctive glyphs or full-alphabet propagation.

Measure stems and dimensions, but inspect optical weight and internal white space. Round strokes usually need compensation; diagonals, joins, and crowded bowls may need different measured values to produce equal color.

Work by morphological family:

1. Core: `H O n o`
2. Round and spine: `c e s a g C G S Q`
3. Arch and stem: `h m u r b d p q`
4. Diagonal: `v w x y z A V W X Y`
5. Figures, punctuation, symbols, and marks

Solve one representative before propagating its logic. Related glyphs inherit principles, not coordinates.

## Spacing

Spacing creates the default rhythm; kerning only handles exceptional pairs.

1. Establish straight references with `H` and `n`.
2. Establish round references with `O` and `o`.
3. Space related forms by edge character: straight, round, diagonal, open, or irregular.
4. Test three-at-a-time strings and systematic pair strings.
5. Read words and paragraphs at intended sizes.
6. Check word space, punctuation, figures, and mixed case.

Compare apparent white space, not sidebearing numbers alone. Preserve intended ratios across masters instead of forcing identical numeric values.

Classify each spacing finding: missing, malformed, or cross-master inconsistent data is an engineering failure; a measured outlier without an accepted project threshold is a recommendation or optical observation; a violated documented project rule is a project-specific deviation. Preserve units, master IDs, and the comparison set in the evidence.

## Kerning

Kern only after spacing and groups are stable.

1. Assign groups from shared left and right edge behavior.
2. Preview critical pairs such as `AV`, `AT`, `To`, `Ta`, `Va`, and punctuation pairs.
3. Add group pairs before exceptions.
4. Keep glyph-level exceptions only when the shape genuinely differs.
5. Verify every pair across masters to prevent interpolation jumps or sign changes.
6. Proof real words and difficult strings; remove redundant exceptions.

Automation can propose values, but visual proof decides whether they belong. Classify kerning findings the same way: structural coverage gaps and cross-master inconsistencies across masters are engineering failures, while outlier values without an accepted threshold are recommendations or observations.

## Weight, color, and proportions

Use `H` and `n` as straight-stem references and `O` and `o` as round references. Compare diagonals within related groups. Evaluate typographic color in strings, not only per-glyph density.

Color emerges from outline mass, counters, spacing, word space, and line spacing. A glyph can have a plausible density number and still disrupt a line through poor distribution of mass.

Check mirrored or construction-related forms together: `b d p q`, `h n m u`, `O Q`, `6 9`, punctuation pairs, and figure families. Similarity is a constraint, not proof that widths must be identical.

## Curves, joins, and overshoot

Keep smooth connections intentional and handles economical. Judge curve acceleration, not just node count. Correct kinks only after the form and stress are decided.

Rounds normally overshoot flat alignment zones; pointed forms often need more. Junction thinning is design-specific, but related joins should behave consistently. Validate at intended sizes because mathematically clean joins can still clog in text.

## Masters and interpolation

Define extremes that express the same design logic. Before interpolation, verify:

- matching path, node, component, anchor, and contour order;
- compatible node types and path directions;
- corresponding extrema and starting points;
- consistent component structure and alignment;
- sensible intermediate shapes, not only compatible endpoints.

After every structural edit, inspect all masters and representative intermediate instances. Compatibility is necessary but does not guarantee good interpolation.

Keep three questions separate: structural compatibility (matching paths, nodes, components, anchors, and directions), interpolation quality (shapes behave sensibly between masters), and variable-font table validity (`fvar`, `STAT`, `gvar`, `HVAR`/`MVAR`/`VVAR`, feature variations). A compatibility check answers only the first. Never claim sampled instances, special or bracket layers, or variable tables were validated unless a deterministic tool tested them.

Use bracket or alternate layers only when smooth interpolation cannot preserve the intended construction. Keep conditions explicit, structures compatible where required, and test around transition boundaries.

## Components and destructive prototypes

Use components for genuinely shared construction, while watching alignment, anchors, metrics, and decomposition behavior. Do not force unrelated forms into components merely to reduce paths.

For component fills, dotted outlines, or pattern prototypes:

- preserve original outlines in a clearly named source or backup layer;
- regenerate the visible layer from that source rather than repeatedly transforming output;
- define placement, clipping, overlap, and component alignment rules;
- test a small glyph set before family-wide execution;
- verify effective bounds manually if copied or decomposed layers report zero bounds.

Choose a script for one-shot or explicitly triggered transformations. Choose a plugin only when behavior must remain live, reactive, or integrated into the editor.

## Production QA

Before export, review:

- master compatibility and interpolation;
- open paths, contour direction, overlaps, short segments, and near misses;
- metrics, alignment zones, overshoots, and vertical-metric coverage;
- glyph names, Unicode assignments, components, anchors, and language coverage;
- spacing, kerning groups, cross-master pairs, and critical exceptions;
- OpenType features, style linking, naming, weight classes, and instance settings;
- `.notdef`, spaces, zero-width glyphs, figures, punctuation, ligatures, and marks.

Treat automated QA as evidence. Resolve severe structural failures first, then warnings, then informational polish. Export only with explicit authorization and report the actual output and logs.

## Visual validation

At each meaningful pass, compare before and after using the same strings and sizes. Check isolated glyphs, related families, words, paragraphs, and target environments. Stop propagation when compatibility breaks, rhythm collapses, color becomes uneven, or the design move can no longer be explained clearly.
