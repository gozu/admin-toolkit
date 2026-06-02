---
description: Refactor new Admin Toolkit additions for project soul, UI consistency, rendering performance, module contracts, and typed trends contracts.
argument-hint: [optional focus area]
allowed-tools: [Read, Glob, Grep, Bash, Edit, Write]
---

# /consistency-pass

The user invoked this command with: $ARGUMENTS

## Workflow

1. Inspect `git diff --stat`, `git diff`, and untracked files relevant to the current task.
2. Use the `consistency_reviewer` agent role when available. It is configured for `gpt-5.5` with `xhigh` reasoning in this repo.
3. Refactor only new or touched work. Do not rewrite unrelated code.
4. Check the additions against:
   - `README.md` Project Soul
   - `AGENTS.md`
   - shared progress indicator semantics
   - module registry participation
   - typed trends registry participation
   - rendering performance: stable dimensions, memoized derived data, no expensive render-time work, no avoidable layout shift, GPU-friendly animation
5. Run targeted verification:
   - `cd resource/frontend && npm run check:contracts`
   - `cd resource/frontend && npm run typecheck`
   - `cd resource/frontend && npm run build` when UI changed
   - `pytest tests/backend` when Python changed

## Output

Report findings first if anything remains risky, then summarize edits and verification. Keep the pass scoped to consistency and performance.
