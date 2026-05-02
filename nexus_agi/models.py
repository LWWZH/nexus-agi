from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"


@dataclass(slots=True)
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(role=str(data["role"]), content=str(data["content"]))


@dataclass(slots=True)
class Artifact:
    artifact_id: str
    kind: str
    title: str
    path: str
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "title": self.title,
            "path": self.path,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            artifact_id=str(data["artifact_id"]),
            kind=str(data["kind"]),
            title=str(data["title"]),
            path=str(data["path"]),
            created_at=str(data.get("created_at", utc_now_iso())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class PlanStep:
    step_id: str
    title: str
    detail: str = ""
    status: StepStatus = StepStatus.PENDING
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    requires_approval: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "detail": self.detail,
            "status": self.status.value,
            "approval_status": self.approval_status.value,
            "requires_approval": self.requires_approval,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        return cls(
            step_id=str(data["step_id"]),
            title=str(data["title"]),
            detail=str(data.get("detail", "")),
            status=StepStatus(str(data.get("status", StepStatus.PENDING.value))),
            approval_status=ApprovalStatus(str(data.get("approval_status", ApprovalStatus.NOT_REQUIRED.value))),
            requires_approval=bool(data.get("requires_approval", False)),
            notes=str(data.get("notes", "")),
        )


@dataclass(slots=True)
class Plan:
    summary: str
    steps: list[PlanStep] = field(default_factory=list)
    source_prompt: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "source_prompt": self.source_prompt,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        return cls(
            summary=str(data.get("summary", "")),
            steps=[PlanStep.from_dict(item) for item in data.get("steps", [])],
            source_prompt=str(data.get("source_prompt", "")),
            created_at=str(data.get("created_at", utc_now_iso())),
        )


@dataclass(slots=True)
class RunRecord:
    run_id: str
    prompt: str
    provider_id: str
    status: RunStatus = RunStatus.DRAFT
    plan: Plan = field(default_factory=lambda: Plan(summary=""))
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    current_step_index: int = 0
    blocked_step_id: str = ""
    blocked_reason: str = ""
    result: str = ""
    error: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "prompt": self.prompt,
            "provider_id": self.provider_id,
            "status": self.status.value,
            "plan": self.plan.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_step_index": self.current_step_index,
            "blocked_step_id": self.blocked_step_id,
            "blocked_reason": self.blocked_reason,
            "result": self.result,
            "error": self.error,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=str(data["run_id"]),
            prompt=str(data.get("prompt", "")),
            provider_id=str(data.get("provider_id", "local")),
            status=RunStatus(str(data.get("status", RunStatus.DRAFT.value))),
            plan=Plan.from_dict(dict(data.get("plan") or {})),
            created_at=str(data.get("created_at", utc_now_iso())),
            updated_at=str(data.get("updated_at", utc_now_iso())),
            current_step_index=int(data.get("current_step_index", 0)),
            blocked_step_id=str(data.get("blocked_step_id", "")),
            blocked_reason=str(data.get("blocked_reason", "")),
            result=str(data.get("result", "")),
            error=str(data.get("error", "")),
            artifacts=[Artifact.from_dict(item) for item in data.get("artifacts", [])],
            events=[dict(item) for item in data.get("events", [])],
        )


@dataclass(slots=True)
class AppConfig:
    default_provider: str = "local"
    provider_settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_provider": self.default_provider,
            "provider_settings": self.provider_settings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        return cls(
            default_provider=str(data.get("default_provider", "local")),
            provider_settings={
                str(key): dict(value)
                for key, value in dict(data.get("provider_settings") or {}).items()
            },
        )


@dataclass(slots=True)
class AppState:
    config: AppConfig = field(default_factory=AppConfig)
    runs: list[RunRecord] = field(default_factory=list)
    active_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "runs": [run.to_dict() for run in self.runs],
            "active_run_id": self.active_run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        return cls(
            config=AppConfig.from_dict(dict(data.get("config") or {})),
            runs=[RunRecord.from_dict(item) for item in data.get("runs", [])],
            active_run_id=str(data.get("active_run_id") or ""),
        )
'''

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"


@dataclass(slots=True)
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(role=str(data["role"]), content=str(data["content"]))


@dataclass(slots=True)
class Artifact:
    artifact_id: str
    kind: str
    title: str
    path: str
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "title": self.title,
            "path": self.path,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            artifact_id=str(data["artifact_id"]),
            kind=str(data["kind"]),
            title=str(data["title"]),
            path=str(data["path"]),
            created_at=str(data.get("created_at", utc_now_iso())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class PlanStep:
    step_id: str
    title: str
    detail: str = ""
    status: StepStatus = StepStatus.PENDING
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    requires_approval: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "detail": self.detail,
            "status": self.status.value,
            "approval_status": self.approval_status.value,
            "requires_approval": self.requires_approval,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        return cls(
            step_id=str(data["step_id"]),
            title=str(data["title"]),
            detail=str(data.get("detail", "")),
            status=StepStatus(str(data.get("status", StepStatus.PENDING.value))),
            approval_status=ApprovalStatus(str(data.get("approval_status", ApprovalStatus.NOT_REQUIRED.value))),
            requires_approval=bool(data.get("requires_approval", False)),
            notes=str(data.get("notes", "")),
        )


@dataclass(slots=True)
class Plan:
    summary: str
    steps: list[PlanStep] = field(default_factory=list)
    source_prompt: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "source_prompt": self.source_prompt,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        return cls(
            summary=str(data.get("summary", "")),
            steps=[PlanStep.from_dict(item) for item in data.get("steps", [])],
            source_prompt=str(data.get("source_prompt", "")),
            created_at=str(data.get("created_at", utc_now_iso())),
        )


@dataclass(slots=True)
class RunRecord:
    run_id: str
    prompt: str
    provider_id: str
    status: RunStatus = RunStatus.DRAFT
    plan: Plan = field(default_factory=lambda: Plan(summary=""))
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    current_step_index: int = 0
    blocked_step_id: str = ""
    blocked_reason: str = ""
    result: str = ""
    error: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "prompt": self.prompt,
            "provider_id": self.provider_id,
            "status": self.status.value,
            "plan": self.plan.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_step_index": self.current_step_index,
            "blocked_step_id": self.blocked_step_id,
            "blocked_reason": self.blocked_reason,
            "result": self.result,
            "error": self.error,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=str(data["run_id"]),
            prompt=str(data.get("prompt", "")),
            provider_id=str(data.get("provider_id", "local")),
            status=RunStatus(str(data.get("status", RunStatus.DRAFT.value))),
            plan=Plan.from_dict(dict(data.get("plan") or {})),
            created_at=str(data.get("created_at", utc_now_iso())),
            updated_at=str(data.get("updated_at", utc_now_iso())),
            current_step_index=int(data.get("current_step_index", 0)),
            blocked_step_id=str(data.get("blocked_step_id", "")),
            blocked_reason=str(data.get("blocked_reason", "")),
            result=str(data.get("result", "")),
            error=str(data.get("error", "")),
            artifacts=[Artifact.from_dict(item) for item in data.get("artifacts", [])],
            events=[dict(item) for item in data.get("events", [])],
        )
'''



@dataclass(slots=True)
class AppConfig:
    default_provider: str = "local"
    provider_settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_provider": self.default_provider,
            "provider_settings": self.provider_settings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        return cls(
            default_provider=str(data.get("default_provider", "local")),
            provider_settings={
                str(key): dict(value)
                for key, value in dict(data.get("provider_settings") or {}).items()
            },
        )


@dataclass(slots=True)
class AppState:
    config: AppConfig = field(default_factory=AppConfig)
    runs: list[RunRecord] = field(default_factory=list)
    active_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "runs": [run.to_dict() for run in self.runs],
            "active_run_id": self.active_run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        return cls(
            config=AppConfig.from_dict(dict(data.get("config") or {})),
            runs=[RunRecord.from_dict(item) for item in data.get("runs", [])],
            active_run_id=str(data.get("active_run_id") or ""),
        )
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def serialize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: serialize_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [serialize_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


class SerializableRecord:
    def to_dict(self) -> dict[str, Any]:
        serialized = serialize_value(self)
        if not isinstance(serialized, dict):
            raise TypeError(f"Expected dataclass serialization to produce a mapping, got {type(serialized)!r}")
        return serialized


class RunStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class MemoryScope(str, Enum):
    SESSION = "session"
    PERSISTENT = "persistent"
    PROJECT = "project"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class Goal(SerializableRecord):
    id: str
    text: str
    created_at: str

    @classmethod
    def create(cls, text: str, goal_id: str | None = None, created_at: str | None = None) -> "Goal":
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("Goal text cannot be empty.")
        return cls(id=goal_id or make_id("goal"), text=normalized_text, created_at=created_at or utc_now())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Goal":
        return cls(id=str(data["id"]), text=str(data["text"]), created_at=str(data["created_at"]))


@dataclass(slots=True)
class Task(SerializableRecord):
    id: str
    title: str
    detail: str
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        title: str,
        detail: str,
        *,
        task_id: str | None = None,
        status: TaskStatus = TaskStatus.PENDING,
        dependencies: list[str] | None = None,
        evidence: list[str] | None = None,
    ) -> "Task":
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("Task title cannot be empty.")
        return cls(
            id=task_id or make_id("task"),
            title=normalized_title,
            detail=detail.strip(),
            status=status,
            dependencies=list(dependencies or []),
            evidence=list(evidence or []),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            id=str(data["id"]),
            title=str(data["title"]),
            detail=str(data.get("detail", "")),
            status=TaskStatus(str(data.get("status", TaskStatus.PENDING.value))),
            dependencies=[str(item) for item in data.get("dependencies", [])],
            evidence=[str(item) for item in data.get("evidence", [])],
        )


@dataclass(slots=True)
class Plan(SerializableRecord):
    id: str
    goal: Goal
    summary: str
    tasks: list[Task]
    created_at: str

    @classmethod
    def create(
        cls,
        goal: Goal,
        summary: str,
        tasks: list[Task],
        *,
        plan_id: str | None = None,
        created_at: str | None = None,
    ) -> "Plan":
        return cls(
            id=plan_id or make_id("plan"),
            goal=goal,
            summary=summary.strip(),
            tasks=list(tasks),
            created_at=created_at or utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        return cls(
            id=str(data["id"]),
            goal=Goal.from_dict(data["goal"]),
            summary=str(data.get("summary", "")),
            tasks=[Task.from_dict(item) for item in data.get("tasks", [])],
            created_at=str(data["created_at"]),
        )


@dataclass(slots=True)
class Action(SerializableRecord):
    id: str
    tool_name: str
    description: str
    arguments: dict[str, Any]
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        tool_name: str,
        description: str,
        arguments: dict[str, Any] | None = None,
        *,
        action_id: str | None = None,
        risk: RiskLevel = RiskLevel.LOW,
        requires_approval: bool = False,
        created_at: str | None = None,
    ) -> "Action":
        normalized_tool_name = tool_name.strip()
        if not normalized_tool_name:
            raise ValueError("Action tool name cannot be empty.")
        return cls(
            id=action_id or make_id("action"),
            tool_name=normalized_tool_name,
            description=description.strip(),
            arguments=dict(arguments or {}),
            risk=risk,
            requires_approval=requires_approval,
            created_at=created_at or utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        return cls(
            id=str(data["id"]),
            tool_name=str(data["tool_name"]),
            description=str(data.get("description", "")),
            arguments=dict(data.get("arguments", {})),
            risk=RiskLevel(str(data.get("risk", RiskLevel.LOW.value))),
            requires_approval=bool(data.get("requires_approval", False)),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass(slots=True)
class Artifact(SerializableRecord):
    id: str
    kind: str
    path: str
    description: str
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        kind: str,
        path: str | Path,
        description: str,
        *,
        artifact_id: str | None = None,
        created_at: str | None = None,
    ) -> "Artifact":
        return cls(
            id=artifact_id or make_id("artifact"),
            kind=kind.strip(),
            path=str(path),
            description=description.strip(),
            created_at=created_at or utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            id=str(data["id"]),
            kind=str(data.get("kind", "artifact")),
            path=str(data.get("path", "")),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass(slots=True)
class MemoryItem(SerializableRecord):
    id: str
    scope: MemoryScope
    content: str
    tags: list[str]
    created_at: str

    @classmethod
    def create(
        cls,
        scope: MemoryScope,
        content: str,
        tags: list[str] | None = None,
        *,
        memory_id: str | None = None,
        created_at: str | None = None,
    ) -> "MemoryItem":
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Memory content cannot be empty.")
        return cls(
            id=memory_id or make_id("memory"),
            scope=scope,
            content=normalized_content,
            tags=list(tags or []),
            created_at=created_at or utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryItem":
        return cls(
            id=str(data["id"]),
            scope=MemoryScope(str(data.get("scope", MemoryScope.PERSISTENT.value))),
            content=str(data.get("content", "")),
            tags=[str(item) for item in data.get("tags", [])],
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass(slots=True)
class Approval(SerializableRecord):
    id: str
    action_id: str
    decision: ApprovalDecision = ApprovalDecision.PENDING
    comment: str = ""
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        action_id: str,
        *,
        approval_id: str | None = None,
        decision: ApprovalDecision = ApprovalDecision.PENDING,
        comment: str = "",
        created_at: str | None = None,
    ) -> "Approval":
        return cls(
            id=approval_id or make_id("approval"),
            action_id=action_id,
            decision=decision,
            comment=comment.strip(),
            created_at=created_at or utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Approval":
        return cls(
            id=str(data["id"]),
            action_id=str(data["action_id"]),
            decision=ApprovalDecision(str(data.get("decision", ApprovalDecision.PENDING.value))),
            comment=str(data.get("comment", "")),
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass(slots=True)
class ActionExecution(SerializableRecord):
    id: str
    action_id: str
    tool_name: str
    status: ExecutionStatus
    summary: str
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        action_id: str,
        tool_name: str,
        status: ExecutionStatus,
        summary: str,
        *,
        artifacts: list[str] | None = None,
        execution_id: str | None = None,
        created_at: str | None = None,
    ) -> "ActionExecution":
        return cls(
            id=execution_id or make_id("execution"),
            action_id=action_id,
            tool_name=tool_name,
            status=status,
            summary=summary.strip(),
            artifacts=list(artifacts or []),
            created_at=created_at or utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionExecution":
        return cls(
            id=str(data["id"]),
            action_id=str(data["action_id"]),
            tool_name=str(data.get("tool_name", "")),
            status=ExecutionStatus(str(data.get("status", ExecutionStatus.SUCCESS.value))),
            summary=str(data.get("summary", "")),
            artifacts=[str(item) for item in data.get("artifacts", [])],
            created_at=str(data.get("created_at", utc_now())),
        )


@dataclass(slots=True)
class RunRecord(SerializableRecord):
    id: str
    goal: Goal
    plan: Plan
    status: RunStatus = RunStatus.DRAFT
    next_actions: list[Action] = field(default_factory=list)
    next_action_index: int = 0
    approvals: list[Approval] = field(default_factory=list)
    events: list[ActionExecution] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evaluation: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        goal: Goal,
        plan: Plan,
        *,
        run_id: str | None = None,
        status: RunStatus = RunStatus.DRAFT,
        next_actions: list[Action] | None = None,
        next_action_index: int = 0,
        approvals: list[Approval] | None = None,
        events: list[ActionExecution] | None = None,
        artifacts: list[Artifact] | None = None,
        notes: list[str] | None = None,
        evaluation: dict[str, Any] | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> "RunRecord":
        timestamp = created_at or utc_now()
        return cls(
            id=run_id or make_id("run"),
            goal=goal,
            plan=plan,
            status=status,
            next_actions=list(next_actions or []),
            next_action_index=next_action_index,
            approvals=list(approvals or []),
            events=list(events or []),
            artifacts=list(artifacts or []),
            notes=list(notes or []),
            evaluation=dict(evaluation or {}),
            created_at=timestamp,
            updated_at=updated_at or timestamp,
        )

    def touch(self) -> None:
        self.updated_at = utc_now()

    @staticmethod
    def _infer_next_action_index(events: list[ActionExecution]) -> int:
        for index, event in enumerate(events):
            if event.status not in {ExecutionStatus.SUCCESS, ExecutionStatus.SKIPPED}:
                return index
        return len(events)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        events = [ActionExecution.from_dict(item) for item in data.get("events", [])]
        return cls(
            id=str(data["id"]),
            goal=Goal.from_dict(data["goal"]),
            plan=Plan.from_dict(data["plan"]),
            status=RunStatus(str(data.get("status", RunStatus.DRAFT.value))),
            next_actions=[Action.from_dict(item) for item in data.get("next_actions", [])],
            next_action_index=int(data.get("next_action_index", cls._infer_next_action_index(events))),
            approvals=[Approval.from_dict(item) for item in data.get("approvals", [])],
            events=events,
            artifacts=[Artifact.from_dict(item) for item in data.get("artifacts", [])],
            notes=[str(item) for item in data.get("notes", [])],
            evaluation=dict(data.get("evaluation", {})),
            created_at=str(data.get("created_at", utc_now())),
            updated_at=str(data.get("updated_at", utc_now())),
        )
