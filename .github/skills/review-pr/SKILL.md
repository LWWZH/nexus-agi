---
name: review-pr
description: 'Review pull requests, code review diffs, and local changes for bugs, regressions, missing tests, risk, and merge readiness.'
argument-hint: 'pull request, branch, or diff to review'
user-invocable: true
disable-model-invocation: false
---

# Review Pull Request

Use this skill when performing a focused code review of a pull request, branch, or patch. It helps the agent inspect the change thoroughly, validate the most likely failure modes, and report findings in a reviewer-friendly format.

## When to Use
- Reviewing a pull request before merge
- Checking a local diff for bugs, regressions, or missing tests
- Evaluating whether a change is safe, coherent, and ready to ship
- Producing review feedback that prioritizes concrete issues over summary prose

## Procedure
1. Identify the review scope.
   - Determine the branch, PR, or diff being reviewed.
   - Confirm the change is coherent enough to review as one unit. If it is not, separate the changes before continuing.

2. Read the implementation.
   - Inspect the touched files and the closest related code paths.
   - Trace the behavior that changed, including inputs, outputs, and state transitions.
   - Look for mismatches between the implementation and the intended behavior.

3. Check for risks.
   - Prioritize correctness, regressions, API compatibility, data loss, security, and performance concerns.
   - Verify that error handling, edge cases, and state persistence still behave sensibly.
   - Check whether existing tests actually cover the changed behavior.

4. Validate when useful.
   - Run the narrowest relevant tests or checks available for the touched area.
   - For this repository, prefer `python -m unittest discover -s tests -p "test_*.py"` when the review touches core runtime or dashboard behavior.
   - If validation is unavailable or too broad, state that clearly and explain the gap.

5. Report findings first.
   - List concrete issues in descending severity.
   - Include the file path, the specific problem, and why it matters.
   - If there are no blocking issues, say that explicitly and mention any residual risks or testing gaps.

## Quality Checks
- Findings are concrete and tied to code, not general impressions
- The review focuses on user impact, not implementation trivia
- The most severe issue appears first
- Validation was run when it would materially improve confidence
- If no issues were found, that is stated plainly along with any remaining uncertainty

## Output Shape
When this skill completes, it should provide:
- Review findings ordered by severity, with file references
- A short note on validation that was run or skipped
- Any open questions, residual risks, or missing tests
