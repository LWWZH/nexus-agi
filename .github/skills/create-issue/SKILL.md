---
name: create-issue
description: 'Create a high-quality GitHub issue from a problem description, bug report, or feature request. Use for triaging, clarifying scope, drafting titles and bodies, and opening issues with the right labels and assignees.'
argument-hint: 'repo context or problem description'
user-invocable: true
disable-model-invocation: false
---

# Create Issue

Use this skill when turning a problem, idea, or bug into a well-scoped GitHub issue. It helps the agent gather the minimum useful context, decide whether more clarification is needed, and produce an issue that is easy to act on.

## When to Use
- Reporting a bug, regression, or unexpected behavior
- Capturing a feature request or enhancement idea
- Converting a rough task description into a concrete issue
- Triage work that needs a concise title, body, labels, and owner

## Procedure
1. Identify the issue type and desired outcome.
   - Classify the request as a bug, enhancement, task, docs item, or question.
   - State the user-visible outcome or failure mode in one sentence.

2. Gather the minimum needed context.
   - Identify the affected area, recent change, reproduction steps, or business impact.
   - If the request is vague, ask only for the missing details that change the issue content.

3. Decide whether the issue is ready to write.
   - If the problem statement is incomplete, pause and ask targeted clarifying questions.
   - If the scope is clear enough, proceed with a draft using the facts already available.

4. Draft the issue.
   - Write a title that names the problem or requested outcome, not the implementation detail.
   - Write a body with three parts: context, expected vs. actual behavior or requested change, and acceptance criteria.
   - Add reproduction steps, examples, or screenshots only when they materially help.

5. Apply metadata.
   - Choose labels that match the issue type and urgency.
   - Assign an owner only when there is a clear one.
   - Prefer a milestone or project link only when the repository already uses them consistently.

6. Open or finalize the issue.
   - Create the issue when the title, body, and metadata are stable.
   - If the issue is still under discussion, keep the draft concise and mark the open questions clearly.

## Quality Checks
- The title is specific, searchable, and outcome-focused
- The body explains why the issue matters and what completion looks like
- Reproduction or acceptance details are included when they change how the issue should be handled
- Labels and assignees are justified by the available context
- Any missing information is called out instead of guessed

## Output Shape
When this skill completes, it should provide:
- A recommended issue title
- A concise issue body
- Suggested labels and assignee, if any
- Any open questions or follow-up context needed before filing

## Related Prompts
- Use this skill with bug-fixing, roadmap planning, or code-review workflows when a problem needs to be tracked.