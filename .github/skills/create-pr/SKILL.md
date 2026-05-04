---
name: create-pr
description: 'Prepare and create pull requests. Use for drafting PR titles/descriptions, reviewing local changes, running checks, and opening draft or final PRs.'
argument-hint: 'repo path or change description'
user-invocable: true
disable-model-invocation: false
---

# Create Pull Request

Use this skill when turning a finished change into a pull request. It helps the agent validate the work, summarize the change clearly, and open or draft the PR with the right context.

## When to Use
- Preparing a pull request for an implemented change
- Reviewing the local diff before publishing
- Writing a concise PR title, summary, testing notes, and risk notes
- Creating either a draft PR or a ready-to-review PR

## Procedure
1. Identify the change scope.
   - Check the current branch, the committed/uncommitted diff, and the files that changed.
   - Confirm the change belongs together as one PR. If it does not, split the work before creating the PR.

2. Review the implementation.
   - Read the touched files closely enough to explain the user-visible behavior change.
   - Note any follow-up work, compatibility concerns, or intentional limitations.

3. Validate the change.
   - Run the narrowest relevant checks first.
   - For this repository, prefer `python -m unittest discover -s tests -p "test_*.py"` when the change touches core behavior.
   - If a focused test exists for the changed area, run it before broader validation.
   - If checks fail, fix the issue before drafting the PR.

4. Summarize the PR.
   - Write a title that describes the user-facing change, not the implementation detail.
   - Write a body with three parts: what changed, how it was validated, and any caveats or follow-up items.
   - Keep the body factual and short enough for a reviewer to scan quickly.

5. Open the PR.
   - Use the existing branch if the work is already committed there.
   - Prefer a draft PR if the change is still awaiting review or has known follow-up work.
   - Use a final PR only when validation is complete and the scope is stable.

## Quality Checks
- The diff matches a single coherent change
- Validation was run or the missing validation is explicitly noted
- The PR title is specific and reviewer-friendly
- The PR body explains impact, verification, and risks without unnecessary detail

## Output Shape
When this skill completes, it should provide:
- The recommended PR title
- A concise PR description
- The validation commands that were run
- Any notable review risks or follow-up items

## Related Prompts
- Use this skill alongside code review or release-prep workflows when a change is ready to publish.
