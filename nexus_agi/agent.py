from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


LOCAL_PROVIDER_ID = "local"
CUSTOM_PROVIDER_ID = "custom"


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


class JsonStateStore:
    def __init__(self, workspace_root: Path, data_dir_name: str = ".nexus-agi") -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.data_dir = self.workspace_root / data_dir_name
        self.state_path = self.data_dir / "state.json"
        self.config_path = self.data_dir / "config.json"
        self.runs_dir = self.data_dir / "runs"
        self.artifacts_dir = self.data_dir / "artifacts"
        self.ensure_layout()

    def ensure_layout(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> AppState:
        state = AppState()
        if self.state_path.exists():
            raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
            state = AppState.from_dict(raw_state)

        config = self.load_config()
        if config is not None:
            state.config = config
        return state

    def load_config(self) -> AppConfig | None:
        if self.config_path.exists():
            raw_config = json.loads(self.config_path.read_text(encoding="utf-8"))
            return AppConfig.from_dict(dict(raw_config or {}))

        if self.state_path.exists():
            raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(raw_state, dict) and raw_state.get("config") is not None:
                return AppConfig.from_dict(dict(raw_state.get("config") or {}))

        return None

    def save_state(self, state: AppState) -> None:
        self.ensure_layout()
        self._atomic_write(self.state_path, json.dumps(state.to_dict(), indent=2, sort_keys=True))

    def get_config(self) -> AppConfig:
        return self.load_state().config

    def save_config(self, config: AppConfig) -> AppConfig:
        self.ensure_layout()
        self._atomic_write(self.config_path, json.dumps(config.to_dict(), indent=2, sort_keys=True))
        state = self.load_state()
        state.config = config
        self.save_state(state)
        return config

    def list_runs(self) -> list[RunRecord]:
        return self.load_state().runs

    def get_run(self, run_id: str) -> RunRecord:
        for run in self.list_runs():
            if run.run_id == run_id:
                return run
        raise FileNotFoundError(f"run not found: {run_id}")

    def upsert_run(self, run: RunRecord) -> RunRecord:
        state = self.load_state()
        updated = False
        for index, existing in enumerate(state.runs):
            if existing.run_id == run.run_id:
                state.runs[index] = run
                updated = True
                break
        if not updated:
            state.runs.append(run)
        state.active_run_id = run.run_id
        self.save_state(state)
        self._write_run_snapshot(run)
        return run

    def write_artifact(self, run_id: str, filename: str, content: str, *, kind: str = "text", metadata: dict[str, Any] | None = None) -> Artifact:
        artifact_dir = self.artifacts_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / filename
        artifact_path.write_text(content, encoding="utf-8")
        return Artifact(
            artifact_id=uuid4().hex[:12],
            kind=kind,
            title=filename,
            path=str(artifact_path.relative_to(self.workspace_root)),
            created_at=utc_now_iso(),
            metadata=metadata or {},
        )

    def _write_run_snapshot(self, run: RunRecord) -> None:
        snapshot_path = self.runs_dir / f"{run.run_id}.json"
        self._atomic_write(snapshot_path, json.dumps(run.to_dict(), indent=2, sort_keys=True))

    def _atomic_write(self, path: Path, content: str) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)


class SimplePlanner:
    def build_plan(self, prompt: str) -> Plan:
        normalized = " ".join(prompt.split()).strip()
        if not normalized:
            raise ValueError("prompt cannot be empty")

        summary = self._summarize(normalized)
        slices = self._split_prompt(normalized)
        step_titles = ["Clarify scope and constraints", *[self._title_from_slice(slice_text) for slice_text in slices[:4]], "Validate outcome and capture results"]
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
                    approval_status=ApprovalStatus.PENDING if approval_required else ApprovalStatus.NOT_REQUIRED,
                )
            )

        return Plan(summary=summary, steps=steps, source_prompt=normalized)

    def _summarize(self, prompt: str) -> str:
        candidate = prompt.rstrip(".?!")
        return candidate if len(candidate) <= 80 else candidate[:77].rstrip() + "..."

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
        risky_terms = ("delete", "remove", "overwrite", "destroy", "rm ", "shell", "execute command", "write file")
        return any(term in text for term in risky_terms)


@dataclass(slots=True)
class ProviderSpec:
    provider_id: str
    display_name: str
    adapter_kind: str
    default_model: str
    env_vars: tuple[str, ...] = ()
    default_base_url: str = ""
    chat_path: str = "/v1/chat/completions"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    notes: str = ""


@dataclass(slots=True)
class ProviderStatus:
    provider_id: str
    display_name: str
    adapter_kind: str
    ready: bool
    details: str
    default_model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "adapter_kind": self.adapter_kind,
            "ready": self.ready,
            "details": self.details,
            "default_model": self.default_model,
        }


@dataclass(slots=True)
class ProviderResponse:
    provider_id: str
    model: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        raise NotImplementedError

    def _request_json(self, request: urllib.request.Request, *, timeout_seconds: int) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - exercised with live providers
            raise ProviderError(f"{self.spec.provider_id} request failed: {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - exercised with live providers
            raise ProviderError(f"{self.spec.provider_id} request failed: {exc.reason}") from exc

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.spec.provider_id} returned invalid JSON") from exc


class LocalEchoProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(ProviderSpec("local", "Local Echo", "local", "nexus-local", notes="Deterministic offline provider."))

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        user_messages = [message.content for message in messages if message.role == "user"]
        prompt = user_messages[-1] if user_messages else (messages[-1].content if messages else "")
        fragments = [fragment.strip() for fragment in prompt.replace("\n", ". ").split(".") if fragment.strip()]
        if not fragments:
            fragments = ["No prompt was provided."]
        body = "\n".join(f"- {fragment}" for fragment in fragments[:5])
        return ProviderResponse(provider_id=self.spec.provider_id, model=self.spec.default_model, text=f"Local execution summary:\n{body}", raw={"messages": [message.to_dict() for message in messages]})


class OpenAICompatibleProvider(BaseProvider):
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        headers: Mapping[str, str] | None = None,
        chat_path: str | None = None,
        auth_header: str | None = None,
        auth_prefix: str | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        super().__init__(spec)
        self.base_url = base_url.rstrip("/")
        self.model = model or spec.default_model
        self.api_key = api_key
        self.headers = dict(headers or {})
        self.chat_path = chat_path or spec.chat_path
        self.auth_header = auth_header or spec.auth_header
        self.auth_prefix = auth_prefix if auth_prefix is not None else spec.auth_prefix
        self.timeout_seconds = timeout_seconds

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        payload = {"model": self.model, "messages": [message.to_dict() for message in messages], "temperature": temperature, "stream": False}
        request = urllib.request.Request(f"{self.base_url}/{self.chat_path.lstrip('/')}", data=json.dumps(payload).encode("utf-8"), method="POST")
        request.add_header("Content-Type", "application/json")
        if self.api_key:
            header_value = f"{self.auth_prefix}{self.api_key}" if self.auth_prefix else self.api_key
            if self.auth_header:
                request.add_header(self.auth_header, header_value)
        for key, value in self.headers.items():
            request.add_header(key, value)

        raw_payload = self._request_json(request, timeout_seconds=self.timeout_seconds)
        text = self._extract_text(raw_payload)
        return ProviderResponse(provider_id=self.spec.provider_id, model=self.model, text=text, raw=raw_payload)

    def _extract_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if isinstance(message, dict) and message.get("content") is not None:
                    return str(message["content"])
                if first_choice.get("text") is not None:
                    return str(first_choice["text"])
        for key in ("output_text", "content", "text"):
            if payload.get(key) is not None:
                return str(payload[key])
        return json.dumps(payload, indent=2, sort_keys=True)


class AnthropicProvider(BaseProvider):
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        version: str = "2023-06-01",
        max_tokens: int = 1024,
        timeout_seconds: int = 60,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(spec)
        self.api_key = api_key
        self.model = model or spec.default_model
        self.base_url = (base_url or spec.default_base_url or "https://api.anthropic.com").rstrip("/")
        self.version = version
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.headers = dict(headers or {})

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        system_prompt, chat_messages = self._split_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [message.to_dict() for message in chat_messages],
            "stream": False,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        request = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("anthropic-version", self.version)
        request.add_header("X-Api-Key", self.api_key)
        for key, value in self.headers.items():
            request.add_header(key, value)

        raw_payload = self._request_json(request, timeout_seconds=self.timeout_seconds)
        text = self._extract_text(raw_payload)
        return ProviderResponse(provider_id=self.spec.provider_id, model=self.model, text=text, raw=raw_payload)

    def _split_messages(self, messages: Sequence[Message]) -> tuple[str, list[Message]]:
        system_parts: list[str] = []
        chat_messages: list[Message] = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            chat_messages.append(message)
        return "\n\n".join(system_parts), chat_messages

    def _extract_text(self, payload: dict[str, Any]) -> str:
        content = payload.get("content")
        if isinstance(content, list):
            text_parts = [str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") == "text" and item.get("text")]
            if text_parts:
                return "".join(text_parts)
        if isinstance(content, str):
            return content
        if payload.get("text") is not None:
            return str(payload["text"])
        return json.dumps(payload, indent=2, sort_keys=True)


class GeminiProvider(BaseProvider):
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        max_output_tokens: int = 1024,
        timeout_seconds: int = 60,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(spec)
        self.api_key = api_key
        self.model = model or spec.default_model
        self.base_url = (base_url or spec.default_base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.headers = dict(headers or {})

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        system_prompt, chat_messages = self._split_messages(messages)
        payload: dict[str, Any] = {
            "contents": [self._to_content(message) for message in chat_messages],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": self.max_output_tokens,
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}key={urllib.parse.quote(self.api_key)}"
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
        request.add_header("Content-Type", "application/json")
        for key, value in self.headers.items():
            request.add_header(key, value)

        raw_payload = self._request_json(request, timeout_seconds=self.timeout_seconds)
        text = self._extract_text(raw_payload)
        return ProviderResponse(provider_id=self.spec.provider_id, model=self.model, text=text, raw=raw_payload)

    def _split_messages(self, messages: Sequence[Message]) -> tuple[str, list[Message]]:
        system_parts: list[str] = []
        chat_messages: list[Message] = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            chat_messages.append(message)
        return "\n\n".join(system_parts), chat_messages

    def _to_content(self, message: Message) -> dict[str, Any]:
        role = "model" if message.role == "assistant" else "user"
        return {"role": role, "parts": [{"text": message.content}]}

    def _extract_text(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates:
            first_candidate = candidates[0]
            if isinstance(first_candidate, dict):
                content = first_candidate.get("content")
                if isinstance(content, dict):
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        text_parts = [str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("text")]
                        if text_parts:
                            return "".join(text_parts)
        if payload.get("text") is not None:
            return str(payload["text"])
        return json.dumps(payload, indent=2, sort_keys=True)


class CohereProvider(BaseProvider):
    def __init__(
        self,
        spec: ProviderSpec,
        *,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 1024,
        timeout_seconds: int = 60,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(spec)
        self.api_key = api_key
        self.model = model or spec.default_model
        self.base_url = (base_url or spec.default_base_url or "https://api.cohere.com").rstrip("/")
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.headers = dict(headers or {})

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_dict() for message in messages],
            "stream": False,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        request = urllib.request.Request(
            f"{self.base_url}/v2/chat",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {self.api_key}")
        for key, value in self.headers.items():
            request.add_header(key, value)

        raw_payload = self._request_json(request, timeout_seconds=self.timeout_seconds)
        text = self._extract_text(raw_payload)
        return ProviderResponse(provider_id=self.spec.provider_id, model=self.model, text=text, raw=raw_payload)

    def _extract_text(self, payload: dict[str, Any]) -> str:
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                text_parts = [str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("text")]
                if text_parts:
                    return "".join(text_parts)
            if isinstance(content, str):
                return content
        if payload.get("text") is not None:
            return str(payload["text"])
        return json.dumps(payload, indent=2, sort_keys=True)


def built_in_provider_specs() -> tuple[ProviderSpec, ...]:
    return (
        ProviderSpec("openai", "OpenAI", "openai_compatible", "gpt-4o-mini", ("OPENAI_API_KEY",), "https://api.openai.com", notes="OpenAI chat completions endpoint."),
        ProviderSpec("anthropic", "Anthropic", "anthropic_native", "claude-3-7-sonnet-latest", ("ANTHROPIC_API_KEY",), "https://api.anthropic.com", notes="Anthropic Messages API."),
        ProviderSpec("google-gemini", "Google Gemini", "gemini_native", "gemini-2.0-flash", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "https://generativelanguage.googleapis.com", notes="Gemini generateContent API."),
        ProviderSpec("azure-openai", "Azure OpenAI", "openai_compatible", "gpt-4o-mini", ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"), "", auth_header="api-key", auth_prefix="", notes="Azure-hosted OpenAI-compatible endpoint."),
        ProviderSpec("mistral", "Mistral", "openai_compatible", "mistral-large-latest", ("MISTRAL_API_KEY",), "https://api.mistral.ai", notes="OpenAI-compatible chat endpoint."),
        ProviderSpec("cohere", "Cohere", "cohere_native", "command-r-plus", ("COHERE_API_KEY",), "https://api.cohere.com", notes="Cohere v2 chat API."),
        ProviderSpec("xai", "xAI", "openai_compatible", "grok-2-latest", ("XAI_API_KEY",), "https://api.x.ai", notes="OpenAI-compatible chat endpoint."),
        ProviderSpec("groq", "Groq", "openai_compatible", "llama-3.3-70b-versatile", ("GROQ_API_KEY",), "https://api.groq.com/openai", notes="OpenAI-compatible chat endpoint."),
        ProviderSpec("openrouter", "OpenRouter", "openai_compatible", "openai/gpt-4o-mini", ("OPENROUTER_API_KEY",), "https://openrouter.ai/api", notes="Routing layer for many models through one endpoint."),
        ProviderSpec("ollama", "Ollama", "openai_compatible", "llama3.1", ("OLLAMA_HOST",), "http://127.0.0.1:11434", auth_header="", auth_prefix="", notes="Local OpenAI-compatible model server."),
    )


def custom_provider_spec() -> ProviderSpec:
    return ProviderSpec("custom", "Custom Provider", "openai_compatible", "custom-model", notes="User-defined OpenAI-compatible provider configuration.")


class ProviderRegistry:
    def __init__(self) -> None:
        self._specs = {spec.provider_id: spec for spec in [*built_in_provider_specs(), custom_provider_spec()]}

    def list_specs(self, *, include_custom: bool = True) -> list[ProviderSpec]:
        specs = list(built_in_provider_specs())
        if include_custom:
            specs.append(custom_provider_spec())
        return specs

    def list_statuses(self, config: AppConfig) -> list[ProviderStatus]:
        statuses = [ProviderStatus("local", "Local Echo", "local", True, "Always ready for offline testing.", "nexus-local")]
        for spec in self.list_specs(include_custom=True):
            statuses.append(self.describe(spec.provider_id, config))
        return statuses

    def describe(self, provider_id: str, config: AppConfig) -> ProviderStatus:
        if provider_id == "local":
            return ProviderStatus("local", "Local Echo", "local", True, "Always ready for offline testing.", "nexus-local")

        spec = self._specs[provider_id]
        settings = dict(config.provider_settings.get(provider_id, {}))
        if spec.adapter_kind == "openai_compatible":
            base_url = str(settings.get("base_url") or spec.default_base_url)
            api_key_env = str(settings.get("api_key_env") or (spec.env_vars[0] if spec.env_vars else ""))
            has_api_key = self._resolve_api_key(spec, settings) is not None
            ready = bool(base_url) and (has_api_key or not spec.env_vars or provider_id == "ollama")
            details = base_url or "no base URL configured"
            if not has_api_key and provider_id != "ollama":
                details = f"missing credentials: {api_key_env or 'api key'}"
            if provider_id == "custom" and not base_url:
                ready = False
                details = "configure base_url and credentials in provider_settings['custom']"
            return ProviderStatus(spec.provider_id, spec.display_name, spec.adapter_kind, ready, details, str(settings.get("model") or spec.default_model))
        if spec.adapter_kind in {"anthropic_native", "gemini_native", "cohere_native"}:
            base_url = str(settings.get("base_url") or spec.default_base_url)
            has_api_key = self._resolve_api_key(spec, settings) is not None
            ready = bool(base_url) and has_api_key
            details = base_url if has_api_key else f"missing credentials: {spec.env_vars[0] if spec.env_vars else 'api key'}"
            return ProviderStatus(spec.provider_id, spec.display_name, spec.adapter_kind, ready, details, str(settings.get("model") or spec.default_model))
        return ProviderStatus(spec.provider_id, spec.display_name, spec.adapter_kind, False, spec.notes, str(settings.get("model") or spec.default_model))

    def create_provider(self, provider_id: str, config: AppConfig) -> BaseProvider:
        if provider_id == "local":
            return LocalEchoProvider()

        try:
            spec = self._specs[provider_id]
        except KeyError as exc:
            raise ProviderError(f"unknown provider: {provider_id}") from exc

        settings = dict(config.provider_settings.get(provider_id, {}))
        if spec.adapter_kind == "openai_compatible":
            base_url = str(settings.get("base_url") or spec.default_base_url)
            if not base_url and provider_id != "custom":
                raise ProviderError(f"{provider_id} is missing base_url configuration")
            api_key = self._resolve_api_key(spec, settings)
            headers = dict(settings.get("headers", {}))
            return OpenAICompatibleProvider(
                spec,
                base_url=base_url,
                model=str(settings.get("model") or spec.default_model),
                api_key=str(api_key) if api_key is not None else None,
                headers=headers,
                chat_path=str(settings.get("chat_path") or spec.chat_path),
                auth_header=str(settings.get("auth_header") or spec.auth_header),
                auth_prefix=str(settings.get("auth_prefix") if settings.get("auth_prefix") is not None else spec.auth_prefix),
            )

        if spec.adapter_kind == "anthropic_native":
            api_key = self._resolve_api_key(spec, settings)
            if not api_key:
                raise ProviderError("anthropic is missing api_key configuration")
            return AnthropicProvider(
                spec,
                api_key=str(api_key),
                model=str(settings.get("model") or spec.default_model),
                base_url=str(settings.get("base_url") or spec.default_base_url),
                version=str(settings.get("version") or "2023-06-01"),
                max_tokens=int(settings.get("max_tokens") or 1024),
                headers=dict(settings.get("headers", {})),
            )

        if spec.adapter_kind == "gemini_native":
            api_key = self._resolve_api_key(spec, settings)
            if not api_key:
                raise ProviderError("google-gemini is missing api_key configuration")
            return GeminiProvider(
                spec,
                api_key=str(api_key),
                model=str(settings.get("model") or spec.default_model),
                base_url=str(settings.get("base_url") or spec.default_base_url),
                max_output_tokens=int(settings.get("max_output_tokens") or 1024),
                headers=dict(settings.get("headers", {})),
            )

        if spec.adapter_kind == "cohere_native":
            api_key = self._resolve_api_key(spec, settings)
            if not api_key:
                raise ProviderError("cohere is missing api_key configuration")
            return CohereProvider(
                spec,
                api_key=str(api_key),
                model=str(settings.get("model") or spec.default_model),
                base_url=str(settings.get("base_url") or spec.default_base_url),
                max_tokens=int(settings.get("max_tokens") or 1024),
                headers=dict(settings.get("headers", {})),
            )

        raise ProviderError(f"no provider factory available for {provider_id}")

    def _resolve_api_key(self, spec: ProviderSpec, settings: dict[str, Any]) -> str | None:
        api_key = settings.get("api_key")
        if api_key:
            return str(api_key)

        api_key_env = str(settings.get("api_key_env") or "")
        if api_key_env:
            env_value = os.getenv(api_key_env)
            if env_value:
                return env_value

        for env_var in spec.env_vars:
            env_value = os.getenv(env_var)
            if env_value:
                return env_value

        return None


class AgentRuntime:
    def __init__(self, store: JsonStateStore, planner: SimplePlanner | None = None, providers: ProviderRegistry | None = None) -> None:
        self.store = store
        self.planner = planner or SimplePlanner()
        self.providers = providers or ProviderRegistry()

    def snapshot(self) -> AppState:
        return self.store.load_state()

    def get_config(self) -> AppConfig:
        return self.store.get_config()

    def update_config(self, config: AppConfig) -> AppConfig:
        self.store.save_config(config)
        return config

    def merge_config(self, updates: dict[str, Any]) -> AppConfig:
        current = self.get_config()
        merged = AppConfig.from_dict(current.to_dict())
        for key, value in updates.items():
            if key == "provider_settings" and isinstance(value, dict):
                merged.provider_settings = self._merge_provider_settings(merged.provider_settings, value)
            elif hasattr(merged, key):
                setattr(merged, key, value)
        return self.update_config(merged)

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
        resolved_provider = provider_id or state.config.default_provider or "local"
        plan = self.planner.build_plan(prompt)
        run = RunRecord(run_id=uuid4().hex[:12], prompt=prompt, provider_id=resolved_provider, status=RunStatus.PLANNED, plan=plan)
        self._record_event(run, "run.created", "Created new run from prompt.")
        self._persist_plan_artifact(run)
        return self.store.upsert_run(run)

    def run(self, prompt: str, *, provider_id: str | None = None) -> RunRecord:
        created = self.plan(prompt, provider_id=provider_id)
        return self.execute(created.run_id)

    def execute(self, run_id: str) -> RunRecord:
        run = self.store.get_run(run_id)
        state = self.snapshot()
        try:
            provider = self._resolve_provider(run.provider_id, state.config)
        except ProviderError as exc:
            return self._mark_run_failed(run, str(exc))

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

            try:
                response = provider.complete(
                    [
                        Message("system", "You are nexus-agi, a local-first personal agent."),
                        Message("user", run.prompt),
                        Message("assistant", step.detail),
                    ]
                )
            except Exception as exc:
                return self._mark_run_failed(run, str(exc), step=step, step_index=index)

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

    def _resolve_provider(self, provider_id: str, config: AppConfig) -> BaseProvider:
        if provider_id == "local":
            return self.providers.create_provider("local", config)
        return self.providers.create_provider(provider_id, config)

    def _persist_plan_artifact(self, run: RunRecord) -> None:
        artifact = self.store.write_artifact(run.run_id, "plan.txt", self._plan_summary(run.plan), metadata={"type": "plan"})
        run.artifacts.append(artifact)
        run.updated_at = utc_now_iso()
        self._record_event(run, "plan.created", "Plan created and stored.", artifact_path=artifact.path)
        self.store.upsert_run(run)

    def _plan_summary(self, plan: Plan) -> str:
        return "\n".join([f"summary: {plan.summary}", f"created_at: {plan.created_at}", "steps:", *[f"- {step.step_id}: {step.title} ({step.status.value})" for step in plan.steps]])

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
        run.events.append({"event_type": event_type, "message": message, "timestamp": utc_now_iso(), **{key: value for key, value in data.items() if value is not None}})

    def _mark_run_failed(self, run: RunRecord, message: str, *, step: PlanStep | None = None, step_index: int | None = None) -> RunRecord:
        if step is not None:
            step.status = StepStatus.FAILED
            step.notes = message
        if step_index is not None:
            run.current_step_index = step_index
        run.status = RunStatus.FAILED
        run.blocked_step_id = ""
        run.blocked_reason = ""
        run.result = ""
        run.error = message
        run.updated_at = utc_now_iso()
        if step is not None:
            self._record_event(run, "step.failed", message, step_id=step.step_id)
        self._record_event(run, "run.failed", message, step_id=step.step_id if step is not None else None)
        return self.store.upsert_run(run)

    def _merge_provider_settings(self, current: dict[str, dict[str, Any]], updates: dict[str, Any]) -> dict[str, dict[str, Any]]:
        merged = copy.deepcopy(current)
        for provider_id, provider_updates in updates.items():
            if not isinstance(provider_updates, dict):
                continue
            provider_settings = merged.setdefault(str(provider_id), {})
            if not isinstance(provider_settings, dict):
                provider_settings = {}
                merged[str(provider_id)] = provider_settings
            self._deep_merge_dict(provider_settings, provider_updates)
        return merged

    def _deep_merge_dict(self, target: dict[str, Any], updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_merge_dict(target[key], value)
            else:
                target[key] = value


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--workspace", type=Path, default=argparse.SUPPRESS, help="Workspace root used for .nexus-agi state.")
    shared.add_argument("--data-dir", default=argparse.SUPPRESS, help="Directory that stores local runtime state.")
    shared.add_argument("--provider", default=argparse.SUPPRESS, help="Provider id to use for this command.")
    shared.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON output.")

    parser = argparse.ArgumentParser(prog="nexus-agi", description="Ask nexus-agi from the terminal.", parents=[shared])
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", parents=[shared], help="Create a plan without executing it.")
    plan_parser.add_argument("prompt", help="Task prompt to plan.")

    ask_parser = subparsers.add_parser("ask", aliases=["run", "chat"], parents=[shared], help="Ask the agent and print a chat-style response.")
    ask_parser.add_argument("prompt", help="Task prompt to work on.")

    status_parser = subparsers.add_parser("status", parents=[shared], help="Show the latest run or a specific run.")
    status_parser.add_argument("run_id", nargs="?", help="Optional run id.")

    resume_parser = subparsers.add_parser("resume", parents=[shared], help="Resume a run.")
    resume_parser.add_argument("run_id", help="Run id to resume.")

    approve_parser = subparsers.add_parser("approve", parents=[shared], help="Approve a blocked step.")
    approve_parser.add_argument("run_id", help="Run id to approve.")
    approve_parser.add_argument("step_id", nargs="?", help="Optional step id to approve.")

    config_parser = subparsers.add_parser("config", aliases=["configure"], parents=[shared], help="View or update configuration.")
    config_parser.add_argument("--default-provider", default=argparse.SUPPRESS, help="Set the default provider id.")
    config_parser.add_argument(
        "--provider-setting",
        action="append",
        default=argparse.SUPPRESS,
        help="Update an advanced provider setting using provider.path=value syntax. Can be repeated.",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("providers", parents=[shared], help="List built-in and custom providers.")

    subparsers.add_parser("providers", parents=[shared], help="Alias for config providers.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "workspace"):
        args.workspace = Path.cwd()
    if not hasattr(args, "data_dir"):
        args.data_dir = ".nexus-agi"
    if not hasattr(args, "provider"):
        args.provider = None
    if not hasattr(args, "json"):
        args.json = False
    if not hasattr(args, "provider_setting"):
        args.provider_setting = []
    store = JsonStateStore(args.workspace, data_dir_name=args.data_dir)
    runtime = AgentRuntime(store)

    try:
        if args.command == "plan":
            return _emit_run(runtime.plan(args.prompt, provider_id=args.provider), args.json)
        if args.command in {"ask", "run", "chat"}:
            return _emit_run(runtime.run(args.prompt, provider_id=args.provider), args.json)
        if args.command == "status":
            run = runtime.store.get_run(args.run_id) if args.run_id else runtime.latest_run()
            if run is None:
                print("No runs found.")
                return 0
            return _emit_run(run, args.json)
        if args.command == "resume":
            return _emit_run(runtime.resume(args.run_id), args.json)
        if args.command == "approve":
            return _emit_run(runtime.approve(args.run_id, step_id=args.step_id), args.json)
        if args.command in {"configure", "config"}:
            if getattr(args, "config_command", None) == "providers":
                return _emit_provider_statuses(runtime.list_provider_statuses(), args.json)
            if _has_config_updates(args):
                config = runtime.get_config()
                if hasattr(args, "default_provider"):
                    config.default_provider = args.default_provider
                if args.provider_setting:
                    config.provider_settings = runtime._merge_provider_settings(config.provider_settings, _provider_setting_updates(args.provider_setting))
                runtime.update_config(config)

            config = runtime.get_config()
            if args.json:
                print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
            else:
                _render_config(config)
            return 0
        if args.command == "providers":
            return _emit_provider_statuses(runtime.list_provider_statuses(), args.json)
        parser.error(f"unknown command: {args.command}")
        return 2
    except (FileNotFoundError, ValueError, ProviderError) as exc:
        print(str(exc))
        return 1


def _emit_run(run: RunRecord, json_output: bool) -> int:
    if json_output:
        print(json.dumps(run.to_dict(), indent=2, sort_keys=True))
        return 0

    print(f"Run: {run.run_id}")
    print(f"You: {run.prompt}")
    print(f"nexus-agi: {run.plan.summary}")
    print(f"Status: {run.status.value}")
    print(f"Provider: {run.provider_id}")
    print("Steps:")
    for step in run.plan.steps:
        print(f"  - {step.step_id} [{step.status.value}] {step.title}")
        if step.detail:
            print(f"      {step.detail}")
        if step.notes:
            print(f"      note: {step.notes}")
    if run.blocked_reason:
        print(f"Blocked: {run.blocked_reason}")
    if run.result:
        print("Result:")
        print(run.result)
    if run.error:
        print(f"Error: {run.error}")
    if run.artifacts:
        print("Artifacts:")
        for artifact in run.artifacts:
            print(f"  - {artifact.path} ({artifact.kind})")
    return 0


def _render_config(config: AppConfig) -> None:
    print(f"Default provider: {config.default_provider}")
    print("Persisted in: .nexus-agi/config.json")
    if not config.provider_settings:
        print("Provider settings: (none)")
        return

    print("Provider settings:")
    for provider_id, settings in sorted(config.provider_settings.items()):
        if not settings:
            print(f"  - {provider_id}: (none)")
            continue
        entries = ", ".join(f"{key}={_format_config_value(value)}" for key, value in sorted(settings.items()))
        print(f"  - {provider_id}: {entries}")


def _emit_provider_statuses(statuses: list[dict[str, Any]], json_output: bool) -> int:
    if json_output:
        print(json.dumps(statuses, indent=2, sort_keys=True))
        return 0

    for status in statuses:
        readiness = "ready" if status["ready"] else "not ready"
        print(f"{status['provider_id']}: {status['display_name']} [{readiness}] - {status['details']}")
    return 0


def _has_config_updates(args: argparse.Namespace) -> bool:
    return hasattr(args, "default_provider") or bool(getattr(args, "provider_setting", []))


def _provider_setting_updates(assignments: list[str]) -> dict[str, dict[str, Any]]:
    updates: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        if "=" not in assignment or "." not in assignment:
            raise ValueError(f"invalid provider setting: {assignment}")
        target, raw_value = assignment.split("=", 1)
        provider_id, path = target.split(".", 1)
        value = _parse_config_value(raw_value)
        provider_updates = updates.setdefault(provider_id, {})
        _assign_nested(provider_updates, path.split("."), value)
    return updates


def _assign_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
    current = target
    for key in path[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[path[-1]] = value


def _parse_config_value(raw_value: str) -> Any:
    text = raw_value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _format_config_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)
