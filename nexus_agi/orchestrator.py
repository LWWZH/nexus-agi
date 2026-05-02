from __future__ import annotations

from uuid import uuid4

from .models import (
    ApprovalStatus,
    AppState,
    Message,
    Plan,
    PlanStep,
    RunRecord,
    RunStatus,
    StepStatus,
    utc_now_iso,
)
from .planner import SimplePlanner
from .providers import CUSTOM_PROVIDER_ID, LOCAL_PROVIDER_ID, ProviderError, ProviderRegistry
from .storage import JsonStateStore


class AgentRuntime:
    def __init__(self, store: JsonStateStore, planner: SimplePlanner | None = None, providers: ProviderRegistry | None = None) -> None:
        self.store = store
        self.planner = planner or SimplePlanner()
        self.providers = providers or ProviderRegistry()

    def snapshot(self) -> AppState:
        return self.store.load_state()

    def list_runs(self) -> list[RunRecord]:
        return self.store.list_runs()

    def latest_run(self) -> RunRecord | None:
        runs = self.list_runs()
        return runs[-1] if runs else None

    def list_provider_statuses(self) -> list[dict[str, object]]:
        state = self.snapshot()
        return [status.to_dict() for status in self.providers.list_statuses(state.config)]

    def plan(self, prompt: str, *, provider_id: str | None = None) -> RunRecord:
        state = self.snapshot()
        resolved_provider = provider_id or state.config.default_provider or LOCAL_PROVIDER_ID
        plan = self.planner.build_plan(prompt)
        run = RunRecord(
            run_id=uuid4().hex[:12],
            prompt=prompt,
            provider_id=resolved_provider,
            status=RunStatus.PLANNED,
            plan=plan,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        self._record_event(run, "run.created", "Created new run from prompt.")
        self._persist_plan_artifact(run)
        return self.store.upsert_run(run)

    def run(self, prompt: str, *, provider_id: str | None = None) -> RunRecord:
        created = self.plan(prompt, provider_id=provider_id)
        return self.execute(created.run_id)

    def execute(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        state = self.snapshot()
        provider = self._resolve_provider(run.provider_id, state.config)
        run.status = RunStatus.RUNNING
        run.updated_at = utc_now_iso()
        self._record_event(run, "run.started", f"Running with provider {run.provider_id}.")
        self.store.upsert_run(run)

        last_output = run.result
        for index in range(run.current_step_index, len(run.plan.steps)):
            step = run.plan.steps[index]
            if step.requires_approval and step.approval_status != ApprovalStatus.APPROVED:
                step.status = StepStatus.BLOCKED
                run.status = RunStatus.BLOCKED
                run.blocked_step_id = step.step_id
                run.blocked_reason = "Approval required before continuing."
                run.current_step_index = index
                self._record_event(run, "step.blocked", f"Blocked on {step.step_id}; approval required.", step_id=step.step_id)
                self.store.upsert_run(run)
                return run

            step.status = StepStatus.RUNNING
            run.current_step_index = index
            run.updated_at = utc_now_iso()
            self._record_event(run, "step.started", step.title, step_id=step.step_id)
            self.store.upsert_run(run)

            response = provider.complete(
                [
                    Message(role="system", content="You are nexus-agi, a local-first personal agent."),
                    Message(role="user", content=run.prompt),
                    Message(role="assistant", content=step.detail),
                ]
            )
            last_output = response.text
            step.notes = response.text
            step.status = StepStatus.COMPLETED
            run.current_step_index = index + 1
            run.updated_at = utc_now_iso()
            artifact = self.store.write_artifact(run.run_id, f"step-{index + 1}.txt", response.text, metadata={"step_id": step.step_id})
            run.artifacts.append(artifact)
            self._record_event(run, "step.completed", step.title, step_id=step.step_id, artifact_path=artifact.path)
            self.store.upsert_run(run)

        run.status = RunStatus.COMPLETED
        run.blocked_step_id = ""
        run.blocked_reason = ""
        run.result = last_output
        run.updated_at = utc_now_iso()
        result_artifact = self.store.write_artifact(run.run_id, "result.txt", last_output or "Run completed.", metadata={"type": "final-result"})
        run.artifacts.append(result_artifact)
        self._record_event(run, "run.completed", "Run completed successfully.", artifact_path=result_artifact.path)
        return self.store.upsert_run(run)

    def approve(self, run_id: str, step_id: str | None = None) -> RunRecord:
        run = self.store.get_run(run_id)
        target_step = self._find_approval_target(run, step_id)
        target_step.approval_status = ApprovalStatus.APPROVED
        if target_step.status == StepStatus.BLOCKED:
            target_step.status = StepStatus.PENDING
        run.blocked_step_id = ""
        run.blocked_reason = ""
        if run.status == RunStatus.BLOCKED:
            run.status = RunStatus.PAUSED
        run.updated_at = utc_now_iso()
        self._record_event(run, "step.approved", f"Approved {target_step.step_id}.", step_id=target_step.step_id)
        return self.store.upsert_run(run)

    def resume(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        if run.status == RunStatus.COMPLETED:
            return run
        if run.status not in {RunStatus.PAUSED, RunStatus.BLOCKED, RunStatus.PLANNED, RunStatus.RUNNING}:
            run.status = RunStatus.PAUSED
        run.updated_at = utc_now_iso()
        self._record_event(run, "run.resumed", "Resumed run execution.")
        self.store.upsert_run(run)
        return self.execute(run_id)

    def pause(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        run.status = RunStatus.PAUSED
        run.updated_at = utc_now_iso()
        self._record_event(run, "run.paused", "Paused run execution.")
        return self.store.upsert_run(run)

    def _resolve_provider(self, provider_id: str, config) -> object:
        if provider_id == LOCAL_PROVIDER_ID:
            return self.providers.create_provider(LOCAL_PROVIDER_ID, config)
        try:
            return self.providers.create_provider(provider_id, config)
        except ProviderError:
            if config.default_provider != LOCAL_PROVIDER_ID:
                return self.providers.create_provider(LOCAL_PROVIDER_ID, config)
            raise

    def _persist_plan_artifact(self, run: RunRecord) -> None:
        artifact = self.store.write_artifact(run.run_id, "plan.txt", self._plan_summary(run.plan), kind="text", metadata={"type": "plan"})
        run.artifacts.append(artifact)
        run.updated_at = utc_now_iso()
        self._record_event(run, "plan.created", "Plan created and stored.", artifact_path=artifact.path)
        self.store.upsert_run(run)

    def _plan_summary(self, plan: Plan) -> str:
        return "\n".join([
            f"summary: {plan.summary}",
            f"created_at: {plan.created_at}",
            "steps:",
            *[f"- {step.step_id}: {step.title} ({step.status.value})" for step in plan.steps],
        ])

    def _find_approval_target(self, run: RunRecord, step_id: str | None) -> PlanStep:
        if step_id:
            for step in run.plan.steps:
                if step.step_id == step_id:
                    return step
            raise ValueError(f"unknown step id: {step_id}")
        if run.blocked_step_id:
            for step in run.plan.steps:
                if step.step_id == run.blocked_step_id:
                    return step
        for step in run.plan.steps:
            if step.requires_approval and step.approval_status != ApprovalStatus.APPROVED:
                return step
        raise ValueError("no step requires approval")

    def _record_event(self, run: RunRecord, event_type: str, message: str, **data: object) -> None:
        run.events.append(
            {
                "event_type": event_type,
                "message": message,
                "timestamp": utc_now_iso(),
                **{key: value for key, value in data.items() if value is not None},
            }
        )
from __future__ import annotations

import json
from pathlib import Path

from .models import (
    Action,
    ActionExecution,
    Artifact,
    Approval,
    ApprovalDecision,
    ExecutionStatus,
    Goal,
    MemoryItem,
    MemoryScope,
    Plan,
    RiskLevel,
    RunRecord,
    RunStatus,
)
from .planner import PlanGenerator
from .safety import ActionReview, ApprovalPolicy
from .shell import ShellToolset
from .storage import JsonStateStore
from .workspace import WorkspaceToolset


class NexusOrchestrator:
    """Coordinate planning, persistence, and future tool execution."""

    transient_retry_limit = 1

    def __init__(
        self,
        state_store: JsonStateStore | None = None,
        planner: PlanGenerator | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ):
        self.state_store = state_store or JsonStateStore.default()
        self.planner = planner or PlanGenerator()
        self.approval_policy = approval_policy or ApprovalPolicy()

    def preview_run(self, goal_text: str) -> RunRecord:
        plan = self.planner.create_plan(goal_text)
        next_actions = self.planner.suggest_actions(goal_text, plan)
        return RunRecord.create(goal=plan.goal, plan=plan, status=RunStatus.READY, next_actions=next_actions)

    def start_run(self, goal_text: str) -> RunRecord:
        run = self.preview_run(goal_text)
        self.state_store.append_run(run)
        return run

    def execute_run(self, goal_text: str, *, workspace_root: Path | str | None = None) -> RunRecord:
        run = self.preview_run(goal_text)
        run.status = RunStatus.RUNNING
        run.next_action_index = 0
        run.touch()
        self.state_store.upsert_run(run)

        workspace = WorkspaceToolset(workspace_root or Path("."))
        return self._advance_run(run, workspace, start_index=0, record_blocked_events=True)

    def resume_run(self, run_id: str, *, workspace_root: Path | str | None = None) -> RunRecord:
        run = self.load_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        if run.status == RunStatus.COMPLETE:
            return run

        if run.status not in {RunStatus.BLOCKED, RunStatus.RUNNING}:
            raise ValueError(f"Run {run_id} is not resumable from status {run.status.value}.")

        workspace = WorkspaceToolset(workspace_root or Path("."))
        run.status = RunStatus.RUNNING
        run.touch()
        self.state_store.upsert_run(run)
        return self._advance_run(run, workspace, start_index=run.next_action_index, record_blocked_events=False)

    def approve_action(self, run_id: str, action_id: str, *, comment: str | None = None) -> RunRecord:
        run = self.load_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        approval = self._approval_for_run_action(run, action_id)
        if approval is None:
            raise ValueError(f"Approval not found for action {action_id} in run {run_id}.")

        approval.decision = ApprovalDecision.APPROVED
        if comment is not None and comment.strip():
            approval.comment = comment.strip()
        elif not approval.comment:
            approval.comment = "Approved by user."

        run.touch()
        self.state_store.upsert_run(run)
        return run

    def remember(self, content: str, *, scope: MemoryScope = MemoryScope.PERSISTENT, tags: list[str] | None = None) -> MemoryItem:
        item = MemoryItem.create(scope=scope, content=content, tags=tags)
        self.state_store.add_memory(item)
        return item

    def latest_summary(self) -> dict[str, object]:
        return self.state_store.summary()

    def load_run(self, run_id: str) -> RunRecord | None:
        return self.state_store.load_run(run_id)

    def load_latest_run(self) -> RunRecord | None:
        return self.state_store.load_latest_run()

    def review_actions(self, run: RunRecord) -> ActionReview:
        ready_actions: list[Action] = []
        pending_approvals: list[Approval] = []

        for action in run.next_actions:
            requires_approval, reason = self.approval_policy.requires_approval(action)
            approval = self._approval_for_run_action(run, action)

            if not requires_approval:
                ready_actions.append(action)
                continue

            if approval is None:
                pending_approvals.append(Approval.create(action_id=action.id, comment=reason))
                continue

            if approval.decision == ApprovalDecision.APPROVED:
                ready_actions.append(action)
                continue

            pending_approvals.append(approval)

        return ActionReview(ready_actions=ready_actions, pending_approvals=pending_approvals)

    def render_run(self, run: RunRecord) -> str:
        review = self.review_actions(run)
        lines: list[str] = [
            f"Run: {run.id}",
            f"Goal: {run.goal.text}",
            f"Status: {run.status.value}",
            f"Progress: {min(run.next_action_index, len(run.next_actions))}/{len(run.next_actions)} actions",
            "",
            f"Plan: {run.plan.summary}",
            "Tasks:",
        ]
        for index, task in enumerate(run.plan.tasks, start=1):
            dependency_text = f" (depends on {', '.join(task.dependencies)})" if task.dependencies else ""
            lines.append(f"  {index}. {task.title}{dependency_text}")
            if task.detail:
                lines.append(f"     - {task.detail}")

        if run.next_actions:
            lines.append("")
            lines.append("Next actions:")
            for index, action in enumerate(run.next_actions, start=1):
                approval_text = self._approval_state_text(run, action)
                lines.append(f"  {index}. {action.tool_name} ({action.id}) [{action.risk.value}, {approval_text}]")
                lines.append(f"     - {action.description}")

        if run.approvals:
            lines.append("")
            lines.append("Approvals:")
            for index, approval in enumerate(run.approvals, start=1):
                lines.append(f"  {index}. {approval.action_id} [{approval.decision.value}]")
                if approval.comment:
                    lines.append(f"     - {approval.comment}")

        if review.pending_approvals:
            lines.append("")
            lines.append("Pending approvals:")
            for index, approval in enumerate(review.pending_approvals, start=1):
                lines.append(f"  {index}. {approval.action_id}")
                if approval.comment:
                    lines.append(f"     - {approval.comment}")

        if run.events:
            lines.append("")
            lines.append("Execution:")
            for index, event in enumerate(run.events, start=1):
                lines.append(f"  {index}. {event.tool_name} ({event.action_id}) [{event.status.value}] {event.summary}")

        if run.evaluation:
            lines.append("")
            lines.append("Evaluation:")
            lines.append(f"  - status: {run.evaluation.get('status', run.status.value)}")
            lines.append(f"  - completion_ratio: {run.evaluation.get('completion_ratio', 0.0)}")
            lines.append(f"  - successful_actions: {run.evaluation.get('successful_actions', 0)}")
            lines.append(f"  - failed_actions: {run.evaluation.get('failed_actions', 0)}")
            lines.append(f"  - blocked_actions: {run.evaluation.get('blocked_actions', 0)}")
            lines.append(f"  - retry_count: {run.evaluation.get('retry_count', 0)}")

        if run.artifacts:
            lines.append("")
            lines.append("Artifacts:")
            for index, artifact in enumerate(run.artifacts, start=1):
                lines.append(f"  {index}. {artifact.kind} -> {artifact.path}")

        return "\n".join(lines)

    def _advance_run(
        self,
        run: RunRecord,
        workspace: WorkspaceToolset,
        *,
        start_index: int,
        record_blocked_events: bool,
    ) -> RunRecord:
        run.status = RunStatus.RUNNING
        self._checkpoint_run(run)

        for index in range(start_index, len(run.next_actions)):
            action = run.next_actions[index]
            requires_approval, reason = self.approval_policy.requires_approval(action)
            approval = self._approval_for_run_action(run, action)

            if requires_approval and (approval is None or approval.decision != ApprovalDecision.APPROVED):
                if approval is None and record_blocked_events:
                    approval = self._ensure_pending_approval(run, action, reason)

                if record_blocked_events:
                    run.events.append(
                        ActionExecution.create(
                            action_id=action.id,
                            tool_name=action.tool_name,
                            status=ExecutionStatus.BLOCKED,
                            summary=approval.comment if approval is not None else reason,
                        )
                    )
                    self._append_note(run, approval.comment if approval is not None else reason)

                run.status = RunStatus.BLOCKED
                run.next_action_index = index
                self._checkpoint_run(run)
                break

            retry_attempts = 0
            while True:
                try:
                    event, artifact = self._execute_action(action, run, workspace, index + 1)
                    break
                except OSError as exc:
                    if retry_attempts < self.transient_retry_limit:
                        retry_attempts += 1
                        self._append_note(
                            run,
                            f"Retrying {action.tool_name} after transient error: {exc}",
                        )
                        self._checkpoint_run(run)
                        continue

                    failure_summary = f"{action.tool_name} failed: {exc}"
                    event = ActionExecution.create(action.id, action.tool_name, ExecutionStatus.FAILED, failure_summary)
                    artifact = None
                    break
                except Exception as exc:
                    failure_summary = f"{action.tool_name} failed: {exc}"
                    event = ActionExecution.create(action.id, action.tool_name, ExecutionStatus.FAILED, failure_summary)
                    artifact = None
                    break

            run.events.append(event)
            if artifact is not None:
                run.artifacts.append(artifact)
            self._append_note(run, event.summary)
            run.next_action_index = index + 1
            self._checkpoint_run(run)
            if event.status == ExecutionStatus.FAILED:
                run.status = RunStatus.FAILED
                self._checkpoint_run(run)
                break

        if run.status == RunStatus.RUNNING:
            run.status = RunStatus.COMPLETE
            run.next_action_index = len(run.next_actions)

        self._checkpoint_run(run)
        return run

    def _execute_action(
        self,
        action: Action,
        run: RunRecord,
        workspace: WorkspaceToolset,
        index: int,
    ) -> tuple[ActionExecution, Artifact | None]:
        if action.tool_name == "workspace.inspect":
            snapshot = workspace.inspect()
            artifact = self._write_artifact(
                workspace,
                run,
                index,
                action.tool_name,
                "workspace-inspection.json",
                json.dumps(snapshot.to_dict(), indent=2, sort_keys=True),
                "Workspace inspection snapshot",
                "workspace.snapshot",
            )
            summary = f"Inspected {snapshot.file_count} files across {snapshot.directory_count} directories."
            return (
                ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                artifact,
            )

        if action.tool_name == "workspace.search":
            query = str(action.arguments.get("query", run.goal.text))
            include_pattern = str(action.arguments.get("include_pattern", "**/*"))
            max_results = int(action.arguments.get("max_results", 20))
            matches = workspace.search_text(query, include_pattern=include_pattern, max_results=max_results)
            artifact = self._write_artifact(
                workspace,
                run,
                index,
                action.tool_name,
                "workspace-search.json",
                json.dumps([match.to_dict() for match in matches], indent=2, sort_keys=True),
                f"Workspace search results for {query!r}",
                "workspace.search",
            )
            summary = f"Found {len(matches)} matches for {query!r}."
            return (
                ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                artifact,
            )

        if action.tool_name == "workspace.edit":
            path = str(action.arguments.get("path", "")).strip()
            if path:
                if "old" in action.arguments and "new" in action.arguments:
                    result = workspace.replace_text(path, str(action.arguments["old"]), str(action.arguments["new"]))
                    artifact = self._write_artifact(
                        workspace,
                        run,
                        index,
                        action.tool_name,
                        "workspace-edit.json",
                        json.dumps(result.to_dict(), indent=2, sort_keys=True),
                        f"Workspace edit result for {path}",
                        "workspace.edit",
                    )
                    summary = f"Updated {path} with a targeted replacement."
                    return (
                        ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                        artifact,
                    )

                if "content" in action.arguments:
                    written_path = workspace.write_text(path, str(action.arguments["content"]))
                    artifact = self._write_artifact(
                        workspace,
                        run,
                        index,
                        action.tool_name,
                        "workspace-write.json",
                        json.dumps({"path": str(written_path.relative_to(workspace.root))}, indent=2, sort_keys=True),
                        f"Workspace write result for {path}",
                        "workspace.write",
                    )
                    summary = f"Wrote {path}."
                    return (
                        ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                        artifact,
                    )

            instruction = str(action.arguments.get("instruction", "")).strip()
            if instruction:
                artifact = self._write_artifact(
                    workspace,
                    run,
                    index,
                    action.tool_name,
                    "workspace-edit-proposal.json",
                    json.dumps(
                        {
                            "instruction": instruction,
                            "goal_id": action.arguments.get("goal_id"),
                            "plan_id": action.arguments.get("plan_id"),
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    "Workspace edit proposal",
                    "workspace.edit.proposal",
                )
                summary = "Recorded a proposed edit for later review."
                return (
                    ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                    artifact,
                )

            raise ValueError("workspace.edit requires either old/new or content arguments.")

        if action.tool_name == "workspace.validate":
            snapshot = workspace.inspect()
            artifact = self._write_artifact(
                workspace,
                run,
                index,
                action.tool_name,
                "workspace-validation.json",
                json.dumps(
                    {
                        "validated": True,
                        "file_count": snapshot.file_count,
                        "directory_count": snapshot.directory_count,
                        "root": snapshot.root,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                "Workspace validation report",
                "workspace.validation",
            )
            summary = f"Validated workspace root with {snapshot.file_count} files."
            return (
                ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                artifact,
            )

        if action.tool_name == "shell.run":
            command = str(action.arguments.get("command", "")).strip()
            if not command:
                raise ValueError("shell.run requires a command argument.")

            timeout_seconds = int(action.arguments.get("timeout_seconds", 300))
            shell = ShellToolset(workspace.root)
            result = shell.run(command, timeout_seconds=timeout_seconds)
            artifact = self._write_artifact(
                workspace,
                run,
                index,
                action.tool_name,
                "shell-run.json",
                json.dumps(result.to_dict(), indent=2, sort_keys=True),
                f"Shell command output for {command}",
                "shell.run",
            )
            status = ExecutionStatus.SUCCESS if result.returncode == 0 and not result.timed_out else ExecutionStatus.FAILED
            summary = f"Ran shell command with exit code {result.returncode}."
            if result.timed_out:
                summary = f"Shell command timed out after {timeout_seconds} seconds."
            return (
                ActionExecution.create(action.id, action.tool_name, status, summary, artifacts=[artifact.path]),
                artifact,
            )

        if action.tool_name == "memory.inspect":
            state = self.state_store.load()
            memory_items = state.get("memory", [])
            artifact = self._write_artifact(
                workspace,
                run,
                index,
                action.tool_name,
                "memory-inspection.json",
                json.dumps({"memory_count": len(memory_items), "memory": memory_items}, indent=2, sort_keys=True),
                "Current memory snapshot",
                "memory.snapshot",
            )
            summary = f"Observed {len(memory_items)} stored memory items."
            return (
                ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                artifact,
            )

        if action.tool_name == "memory.write":
            content = str(action.arguments.get("content", run.goal.text))
            tags = [str(tag) for tag in action.arguments.get("tags", [])]
            item = self.remember(content, tags=tags)
            artifact = self._write_artifact(
                workspace,
                run,
                index,
                action.tool_name,
                "memory-write.json",
                json.dumps(item.to_dict(), indent=2, sort_keys=True),
                "Stored memory item",
                "memory.write",
            )
            summary = f"Stored memory item {item.id}."
            return (
                ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                artifact,
            )

        if action.tool_name == "memory.verify":
            expected_content = str(action.arguments.get("expected_content", ""))
            state = self.state_store.load()
            memory_items = state.get("memory", [])
            found = any(isinstance(item, dict) and item.get("content") == expected_content for item in memory_items)
            if not found:
                raise ValueError(f"Expected memory content was not found: {expected_content}")
            artifact = self._write_artifact(
                workspace,
                run,
                index,
                action.tool_name,
                "memory-verification.json",
                json.dumps({"expected_content": expected_content, "verified": True}, indent=2, sort_keys=True),
                "Memory verification result",
                "memory.verify",
            )
            summary = "Verified the expected memory content is present."
            return (
                ActionExecution.create(action.id, action.tool_name, ExecutionStatus.SUCCESS, summary, artifacts=[artifact.path]),
                artifact,
            )

        raise ValueError(f"Unsupported action tool: {action.tool_name}")

    def _write_artifact(
        self,
        workspace: WorkspaceToolset,
        run: RunRecord,
        index: int,
        tool_name: str,
        filename: str,
        content: str,
        description: str,
        kind: str,
    ) -> Artifact:
        artifact_path = f".nexus-agi/artifacts/{run.id}/{index:02d}-{tool_name}/{filename}"
        written_path = workspace.write_text(artifact_path, content)
        relative_path = written_path.relative_to(workspace.root).as_posix()
        return Artifact.create(kind=kind, path=relative_path, description=description)

    def _approval_for_run_action(self, run: RunRecord, action: Action | str) -> Approval | None:
        action_id = action if isinstance(action, str) else action.id
        for approval in run.approvals:
            if approval.action_id == action_id:
                return approval
        return None

    def _ensure_pending_approval(self, run: RunRecord, action: Action, reason: str) -> Approval:
        approval = self._approval_for_run_action(run, action)
        if approval is not None:
            return approval

        approval = Approval.create(action_id=action.id, comment=reason)
        run.approvals.append(approval)
        return approval

    @staticmethod
    def _append_note(run: RunRecord, note: str) -> None:
        normalized_note = note.strip()
        if not normalized_note:
            return
        if run.notes and run.notes[-1] == normalized_note:
            return
        run.notes.append(normalized_note)

    def _checkpoint_run(self, run: RunRecord) -> None:
        self._refresh_evaluation(run)
        run.touch()
        self.state_store.upsert_run(run)

    def _refresh_evaluation(self, run: RunRecord) -> None:
        successful_actions = sum(1 for event in run.events if event.status == ExecutionStatus.SUCCESS)
        failed_actions = sum(1 for event in run.events if event.status == ExecutionStatus.FAILED)
        blocked_actions = sum(1 for event in run.events if event.status == ExecutionStatus.BLOCKED)
        retry_count = sum(1 for note in run.notes if note.lower().startswith("retrying "))
        planned_actions = len(run.next_actions)
        completion_ratio = run.next_action_index / planned_actions if planned_actions else 1.0

        run.evaluation = {
            "status": run.status.value,
            "planned_actions": planned_actions,
            "executed_actions": len(run.events),
            "successful_actions": successful_actions,
            "failed_actions": failed_actions,
            "blocked_actions": blocked_actions,
            "retry_count": retry_count,
            "approval_count": len(run.approvals),
            "completion_ratio": completion_ratio,
            "remaining_actions": max(planned_actions - run.next_action_index, 0),
        }

    def _approval_state_text(self, run: RunRecord, action: Action) -> str:
        requires_approval, _ = self.approval_policy.requires_approval(action)
        if not requires_approval:
            return "no approval required"

        approval = self._approval_for_run_action(run, action)
        if approval is None:
            return "approval required"
        return approval.decision.value

