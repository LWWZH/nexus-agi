from __future__ import annotations

import re

from .models import ApprovalStatus, Plan, PlanStep, StepStatus, utc_now_iso


class SimplePlanner:
    """Turn a prompt into a small, deterministic plan."""

    def build_plan(self, prompt: str) -> Plan:
        normalized = " ".join(prompt.split()).strip()
        if not normalized:
            raise ValueError("prompt cannot be empty")

        summary = self._summarize(normalized)
        slices = self._split_prompt(normalized)
        step_titles = [
            "Clarify scope and constraints",
            *[self._title_from_slice(slice_text) for slice_text in slices[:4]],
            "Validate outcome and capture results",
        ]
        step_details = [
            f"Review the request and define the execution boundaries for: {summary}",
            *[f"Address this task slice: {slice_text}" for slice_text in slices[:4]],
            "Check the output, record artifacts, and summarize the result.",
        ]

        steps: list[PlanStep] = []
        for index, (title, detail) in enumerate(zip(step_titles, step_details, strict=True), start=1):
            approval_required = index == 1 and self._requires_approval(detail, normalized)
            steps.append(
                PlanStep(
                    step_id=f"step-{index}",
                    title=title,
                    detail=detail,
                    requires_approval=approval_required,
                    approval_status=(ApprovalStatus.PENDING if approval_required else ApprovalStatus.NOT_REQUIRED),
                    status=StepStatus.PENDING,
                )
            )

        return Plan(summary=summary, steps=steps, source_prompt=normalized, created_at=utc_now_iso())

    def _summarize(self, prompt: str) -> str:
        candidate = prompt.rstrip(".?!")
        if len(candidate) <= 80:
            return candidate
        return candidate[:77].rstrip() + "..."

    def _split_prompt(self, prompt: str) -> list[str]:
        chunks = [chunk.strip(" .") for chunk in re.split(r"[;\n]+", prompt) if chunk.strip()]
        if not chunks:
            return [prompt]
        if len(chunks) == 1:
            sub_chunks = [chunk.strip(" .") for chunk in re.split(r",| and then | then ", chunks[0]) if chunk.strip()]
            if len(sub_chunks) > 1:
                return sub_chunks
        return chunks

    def _title_from_slice(self, slice_text: str) -> str:
        words = re.findall(r"[A-Za-z0-9_'-]+", slice_text)
        if not words:
            return "Execute task slice"
        return " ".join(word.capitalize() for word in words[:5])

    def _requires_approval(self, detail: str, prompt: str) -> bool:
        text = f"{detail} {prompt}".lower()
        risky_terms = (
            "delete",
            "remove",
            "overwrite",
            "destroy",
            "rm ",
            "shell",
            "execute command",
            "write file",
        )
        return any(term in text for term in risky_terms)
from __future__ import annotations

from dataclasses import dataclass

from .models import Action, Goal, Plan, RiskLevel, Task, make_id


@dataclass(slots=True)
class PlanTemplate:
    summary: str
    task_specs: list[tuple[str, str]]
    next_action_specs: list[tuple[str, str, RiskLevel, bool]]


class PlanGenerator:
    """Generate a structured starter plan from a plain-language goal."""

    def create_plan(self, goal_text: str) -> Plan:
        goal = Goal.create(goal_text)
        template = self._template_for_goal(goal.text)
        tasks = self._materialize_tasks(template.task_specs)
        return Plan.create(goal=goal, summary=template.summary, tasks=tasks)

    def suggest_actions(self, goal_text: str, plan: Plan) -> list[Action]:
        template = self._template_for_goal(goal_text)
        return [
            Action.create(
                tool_name=tool_name,
                description=description,
                arguments=self._arguments_for_action(tool_name, goal_text, plan),
                action_id=make_id("action"),
                risk=risk,
                requires_approval=requires_approval,
            )
            for tool_name, description, risk, requires_approval in template.next_action_specs
        ]

    def _materialize_tasks(self, task_specs: list[tuple[str, str]]) -> list[Task]:
        tasks: list[Task] = []
        previous_task_id: str | None = None

        for title, detail in task_specs:
            dependencies = [previous_task_id] if previous_task_id else []
            task = Task.create(title=title, detail=detail, dependencies=dependencies)
            tasks.append(task)
            previous_task_id = task.id

        return tasks

    def _template_for_goal(self, goal_text: str) -> PlanTemplate:
        normalized = goal_text.lower()

        if self._contains(normalized, "fix", "bug", "broken", "error", "failing"):
            return PlanTemplate(
                summary="Diagnose the failure, isolate the root cause, patch it, and verify the regression is fixed.",
                task_specs=[
                    ("Reproduce the failure", "Confirm the bug or regression, capture the symptoms, and define expected behavior."),
                    ("Inspect the relevant code path", "Search the workspace for the smallest surface area that could cause the failure."),
                    ("Apply a focused fix", "Change only the code required to resolve the root cause and keep the patch reviewable."),
                    ("Validate the fix", "Use a narrow test or check that can confirm the behavior is corrected."),
                    ("Summarize the result", "Record the cause, the fix, and any remaining risk or follow-up work."),
                ],
                next_action_specs=[
                    ("workspace.inspect", "Inspect the current workspace and locate the failing surface.", RiskLevel.LOW, False),
                    ("workspace.search", "Search for the symbols, files, or tests most likely to explain the failure.", RiskLevel.LOW, False),
                    ("workspace.edit", "Prepare a focused patch that addresses the root cause.", RiskLevel.MEDIUM, True),
                    ("shell.run", "Run the targeted test suite to confirm the fix.", RiskLevel.LOW, False),
                ],
            )

        if self._contains(normalized, "implement", "build", "create", "feature", "add"):
            return PlanTemplate(
                summary="Inspect the codebase, design the smallest coherent change, implement it, and validate the result.",
                task_specs=[
                    ("Inspect the implementation surface", "Find the owning files, modules, or abstractions that should change."),
                    ("Design the smallest viable change", "Break the goal into a narrow plan that can ship incrementally."),
                    ("Implement the change", "Apply the code and state updates needed for the requested capability."),
                    ("Validate the behavior", "Run a focused check to confirm the new behavior works as intended."),
                    ("Summarize the delivery", "Document what changed, why it changed, and what should happen next."),
                ],
                next_action_specs=[
                    ("workspace.inspect", "Inspect the repository structure and identify the implementation entry points.", RiskLevel.LOW, False),
                    ("workspace.search", "Search for nearby code and supporting tests before editing.", RiskLevel.LOW, False),
                    ("workspace.edit", "Prepare the smallest reviewable implementation patch.", RiskLevel.MEDIUM, True),
                    ("shell.run", "Run the targeted test suite to confirm the behavior.", RiskLevel.LOW, False),
                ],
            )

        if self._contains(normalized, "document", "docs", "readme", "write"):
            return PlanTemplate(
                summary="Gather source material, draft the documentation, review it for consistency, and finalize the update.",
                task_specs=[
                    ("Collect source context", "Find the authoritative code paths or notes that the documentation should reflect."),
                    ("Draft the documentation", "Write the explanation, usage guidance, or reference material requested by the goal."),
                    ("Review for accuracy", "Check the draft against the implementation and update any outdated claims."),
                    ("Finalize the output", "Save the approved content and record any remaining follow-up tasks."),
                ],
                next_action_specs=[
                    ("workspace.inspect", "Inspect existing docs or code comments that should stay consistent.", RiskLevel.LOW, False),
                    ("workspace.edit", "Prepare a documentation patch that is easy to review.", RiskLevel.LOW, False),
                    ("workspace.validate", "Check the updated text for internal consistency and completeness.", RiskLevel.LOW, False),
                ],
            )

        if self._contains(normalized, "memory", "remember", "persist", "save context"):
            return PlanTemplate(
                summary="Identify durable facts, store them in the right memory scope, and verify they can be retrieved later.",
                task_specs=[
                    ("Identify durable facts", "Separate short-lived task context from useful long-term memory candidates."),
                    ("Choose the correct memory scope", "Decide whether the fact belongs in session, persistent, or project memory."),
                    ("Persist the confirmed memory", "Write the fact in a compact, unambiguous form."),
                    ("Verify retrieval", "Confirm the memory can be read back and used in a later run."),
                ],
                next_action_specs=[
                    ("memory.inspect", "Review existing memory and identify what should be retained.", RiskLevel.LOW, False),
                    ("memory.write", "Persist the confirmed memory item in the appropriate scope.", RiskLevel.LOW, False),
                    ("memory.verify", "Confirm the stored fact can be retrieved cleanly.", RiskLevel.LOW, False),
                ],
            )

        return PlanTemplate(
            summary="Clarify the objective, gather context, choose the narrowest useful path, and validate the result.",
            task_specs=[
                ("Clarify the objective", "Restate the goal in concrete terms and identify what success looks like."),
                ("Gather local context", "Inspect the workspace and capture the minimum information needed to proceed."),
                ("Choose the next action", "Select the smallest high-value step that moves the goal forward."),
                ("Validate and summarize", "Check the outcome, capture any artifacts, and report the result."),
            ],
            next_action_specs=[
                ("workspace.inspect", "Inspect the workspace and locate the likely implementation surface.", RiskLevel.LOW, False),
                ("workspace.search", "Search for nearby code or notes that should inform the plan.", RiskLevel.LOW, False),
                ("workspace.edit", "Prepare a targeted change if the goal requires one.", RiskLevel.MEDIUM, True),
                ("shell.run", "Run the targeted test suite to validate the result.", RiskLevel.LOW, False),
            ],
        )

    @staticmethod
    def _contains(text: str, *keywords: str) -> bool:
        return any(keyword in text for keyword in keywords)

    def _arguments_for_action(self, tool_name: str, goal_text: str, plan: Plan) -> dict[str, object]:
        base_arguments: dict[str, object] = {"goal_id": plan.goal.id, "plan_id": plan.id}

        if tool_name == "workspace.inspect":
            return {**base_arguments, "root": "."}

        if tool_name == "workspace.search":
            return {
                **base_arguments,
                "query": goal_text,
                "include_pattern": "**/*",
                "max_results": 20,
            }

        if tool_name == "workspace.edit":
            return {**base_arguments, "instruction": goal_text}

        if tool_name == "memory.inspect":
            return {**base_arguments}

        if tool_name == "memory.write":
            return {
                **base_arguments,
                "content": goal_text,
                "tags": self._keywords(goal_text),
            }

        if tool_name == "memory.verify":
            return {**base_arguments, "expected_content": goal_text}

        if tool_name == "shell.run":
            return {
                **base_arguments,
                "command": self._validation_command(),
                "timeout_seconds": 300,
            }

        return base_arguments

    @staticmethod
    def _keywords(text: str) -> list[str]:
        words = [word.strip(".,:;!?()[]{}\"'`).-_/") for word in text.lower().split()]
        return [word for word in words if len(word) > 3][:5]

    @staticmethod
    def _validation_command() -> str:
        return "python -m unittest discover -s tests -p test_*.py"

