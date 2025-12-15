# Codex Slash Commands (Repo Workflow)

These are conversational commands to use with Codex (the assistant) while working in this repo.

## `/research`
Input: a goal + any notes/constraints.

Codex actions:
1. Inspect the codebase to understand current behavior (files, functions, patterns).
2. If you approve network access, pull a few best-practice references from the internet.
3. Write a single working document at `notes/plan/plan.md` that includes:
   - Summary (goal, success criteria, non-goals, constraints)
   - Current State (repo findings with file paths)
   - Research Notes (external references + takeaways)
   - Proposed Approach (options + recommendation)
   - Draft Implementation Plan + Validation

Output: `notes/plan/plan.md` updated/created.

## `/plan`
Input: optionally, tweaks/decisions since `/research`.

Codex actions:
1. Read `notes/plan/plan.md`.
2. Convert the “Draft Implementation Plan” into an execution-ready checklist:
   - ordered steps
   - files to touch
   - commands to run
   - acceptance criteria per step
3. Update `notes/plan/plan.md` in-place (no new files unless you ask).

Output: `notes/plan/plan.md` refined into an implementable plan.

## `/implement`
Input: optionally, “start at step N” / “skip X” / “do only Y”.

Codex actions:
1. Read `notes/plan/plan.md`.
2. Implement the plan in the repo using small, reviewable patches.
3. Run the validation commands listed in the plan when feasible (requesting approval if needed).
4. Update the plan with completion status (and adjust if realities differ).

Output: code changes + a short summary of what shipped and how to validate.

