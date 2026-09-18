# Audit Contract

Use this contract for integral, evidence-based review. An audit is read-only unless the user authorizes a specific change.

## Sequence

`PREFLIGHT -> OBSERVE -> ANALYZE -> VALIDATE -> REPORT`. Do not skip stages.

## Target and revision

- Resolve exactly one target (font, and master or layer where relevant) before making claims. An ambiguous or missing target is `blocked`, not a guess.
- Record the starting revision when the environment exposes one and recheck it before reporting.
- If the target or revision changed, mark dependent evidence stale and downgrade the result to partial or blocked.

## Project context

- `type-project.yaml` is optional, read-only input. When present and valid, use `project-context` and cite the exact fields relied on.
- When absent, use `generic-context`: lower confidence for intent-dependent conclusions and list the missing input.
- Never infer project intent from family names, glyph notes, feature comments, metadata, or visual style.
- Never label a finding `PROJECT_SPECIFIC_DEVIATION` without a project rule with provenance, and never in `generic-context`.

## Finding classes

Use exactly these classes:

- `ERROR` - deterministic failure or broken contract.
- `WARNING` - suspicious, unverified, or degraded condition.
- `ENGINEERING_RECOMMENDATION` - objective improvement with evidence.
- `DESIGN_OBSERVATION` - optical or interpretive judgment that needs designer authority.
- `PROJECT_SPECIFIC_DEVIATION` - conflict with a documented project rule.
- `INFORMATION` - context without action.

An observation is not a rule. Do not present a preference as a universal defect.

## Evidence

- Every finding needs evidence: tool result, command result, source observation, project rule, standard reference, or explicit limitation.
- Attach exact object identifiers (glyph, layer, master, pair, table) to every finding.
- Deduplicate repeated symptoms under one probable root cause without hiding the affected objects.
- Keep engineering failures separate from optical observations.

## Deterministic validation

- A successful tool call is not a passing check. Derive outcomes from structured tool output, a process exit code, or structured evidence.
- Record the validator, its execution status, and its outcome separately. A successful invocation is not a passing result.
- Never claim a module, platform, or behavior was validated when no deterministic check ran; mark it unaudited.
- Do not reproduce GlyphsMCP analysis algorithms in prose or ad hoc scripts. Reuse tool evidence or declare a limitation.

## Source-of-truth priority

1. current explicit user instruction;
2. project file and documented project rules;
3. the actual source and current document state;
4. official standards and tool documentation;
5. this skill and its references;
6. prior memory and general model knowledge.

A project rule may override a general recommendation, but not a hard technical requirement without an explicit warning.

## Output

Report classified findings with evidence, scope, confidence, limitations, missing inputs, and whether the source was modified. Keep recommendations separate from changes actually applied.
