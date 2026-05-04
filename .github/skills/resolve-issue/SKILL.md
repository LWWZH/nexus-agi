---
name: resolve-issue
description: 'Resolve a GitHub issue by reproducing the problem, tracing the relevant code path, implementing the smallest correct fix, and validating the result.'
argument-hint: 'GitHub issue number, issue body, or problem description'
user-invocable: true
disable-model-invocation: false
---

# Resolve Issue

Use this skill when taking a GitHub issue from report to fix. It helps the agent confirm the problem, inspect the code path that controls it, make a focused change, and verify the result before closing out the work.

## When to Use
- Fixing a bug, regression, or broken workflow captured in a GitHub issue
- Implementing a small scoped change tied to a single issue
- Turning issue text into a concrete code change, validation, and follow-up summary
- Preparing work that should end with a fix, test coverage, and closure notes

## Procedure
1. Read the issue and restate the goal.
   - Identify the expected behavior, the failing behavior, and any constraints.
   - If the issue is vague, extract the minimum missing detail needed to make the fix testable.

2. Find the controlling code path.
   - Trace from the user-visible symptom to the smallest abstraction that decides the behavior.
   - Prefer nearby tests, call sites, or existing implementations over broad repo exploration.

3. Form one local hypothesis.
   - State the most likely cause in one sentence.
   - Choose the cheapest check that could disconfirm it, such as a focused test, a small repro, or a narrow read of the implementation.

4. Make the smallest correct change.
   - Fix the root cause rather than patching symptoms.
   - Keep the edit focused on the issue path and preserve existing style and public behavior unless the issue requires otherwise.

5. Add or update tests.
   - Cover the new behavior at the narrowest useful level.
   - If the issue exposes an untested edge case, add a regression test that would fail before the fix.

6. Validate the fix.
   - Run the narrowest relevant test or check first.
   - Expand only if the focused check passes or if it cannot discriminate the fix.
   - If validation fails, repair the same slice before widening scope.

7. Close out clearly.
   - Summarize the root cause, the fix, and the validation that was run.
   - Call out any follow-up work, unknowns, or residual risk.

## Quality Checks
- The issue goal is restated in testable terms
- The fix targets the code that actually controls the behavior
- A clear hypothesis and discriminating check were used before editing
- The change is small, coherent, and supported by tests when practical
- Validation covers the affected path and any regression risk introduced by the fix

## Output Shape
When this skill completes, it should provide:
- A concise summary of the issue and root cause
- The fix that was made and where it lives
- The validation commands or checks that were run
- Any follow-up risks, constraints, or open questions

## Related Prompts
- Use this skill with review, PR preparation, or issue-triage workflows when an issue needs to be driven to a fix.