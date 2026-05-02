from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4
from typing import Any

from .models import AppConfig, AppState, Artifact, RunRecord, utc_now_iso


class JsonStateStore:
    def __init__(self, workspace_root: Path, data_dir_name: str = ".nexus-agi") -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.data_dir = self.workspace_root / data_dir_name
        self.state_path = self.data_dir / "state.json"
        self.runs_dir = self.data_dir / "runs"
        self.artifacts_dir = self.data_dir / "artifacts"
        self.ensure_layout()

    def ensure_layout(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> AppState:
        if not self.state_path.exists():
            return AppState()
        raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return AppState.from_dict(raw_state)

    def save_state(self, state: AppState) -> None:
        self.ensure_layout()
        self._atomic_write(self.state_path, json.dumps(state.to_dict(), indent=2, sort_keys=True))

    def get_config(self) -> AppConfig:
        return self.load_state().config

    def save_config(self, config: AppConfig) -> None:
        state = self.load_state()
        state.config = config
        self.save_state(state)

    def list_runs(self) -> list[RunRecord]:
        return self.load_state().runs

    def get_run(self, run_id: str) -> RunRecord:
        for run in self.load_state().runs:
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

    def set_active_run(self, run_id: str | None) -> None:
        state = self.load_state()
        state.active_run_id = run_id or ""
        self.save_state(state)

    def write_artifact(self, run_id: str, filename: str, content: str, *, kind: str = "text", metadata: dict[str, Any] | None = None) -> Artifact:
        artifact_dir = self.artifacts_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / filename
        artifact_path.write_text(content, encoding="utf-8")
        relative_path = artifact_path.relative_to(self.workspace_root)
        artifact = Artifact(
            artifact_id=uuid4().hex[:12],
            kind=kind,
            title=filename,
            path=str(relative_path),
            created_at=utc_now_iso(),
            metadata=metadata or {},
        )
        return artifact

    def _write_run_snapshot(self, run: RunRecord) -> None:
        snapshot_path = self.runs_dir / f"{run.run_id}.json"
        self._atomic_write(snapshot_path, json.dumps(run.to_dict(), indent=2, sort_keys=True))

    def _atomic_write(self, path: Path, content: str) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .models import MemoryItem, RunRecord, utc_now


class JsonStateStore:
    """Persist runs and memory to a small JSON file on disk."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def default(cls) -> "JsonStateStore":
        return cls(Path(".nexus-agi") / "state.json")

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_state()

        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"State file {self.path} is not valid JSON.") from exc

        if not isinstance(state, dict):
            raise ValueError(f"State file {self.path} must contain a JSON object.")

        state.setdefault("version", 1)
        state.setdefault("runs", [])
        state.setdefault("memory", [])
        state.setdefault("updated_at", utc_now())
        return state

    def save(self, state: dict[str, Any]) -> None:
        normalized = dict(state)
        normalized["updated_at"] = utc_now()
        normalized.setdefault("version", 1)
        normalized.setdefault("runs", [])
        normalized.setdefault("memory", [])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")

    def append_run(self, run: RunRecord) -> dict[str, Any]:
        return self.upsert_run(run)

    def upsert_run(self, run: RunRecord) -> dict[str, Any]:
        state = self.load()
        runs = state.setdefault("runs", [])
        run_record = run.to_dict()
        for index, existing in enumerate(runs):
            if isinstance(existing, dict) and existing.get("id") == run.id:
                runs[index] = run_record
                break
        else:
            runs.append(run_record)
        state["latest_run_id"] = run.id
        self.save(state)
        return state

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        state = self.load()
        for run in state.get("runs", []):
            if isinstance(run, dict) and run.get("id") == run_id:
                return run
        return None

    def load_run(self, run_id: str) -> RunRecord | None:
        run_data = self.get_run(run_id)
        if run_data is None:
            return None
        return RunRecord.from_dict(run_data)

    def load_latest_run(self) -> RunRecord | None:
        latest_run_data = self.latest_run()
        if latest_run_data is None:
            return None
        return RunRecord.from_dict(latest_run_data)

    def add_memory(self, item: MemoryItem) -> dict[str, Any]:
        state = self.load()
        memory = state.setdefault("memory", [])
        memory.append(item.to_dict())
        self.save(state)
        return state

    def latest_run(self) -> dict[str, Any] | None:
        state = self.load()
        runs = state.get("runs", [])
        if not runs:
            return None
        return runs[-1]

    def summary(self) -> dict[str, Any]:
        state = self.load()
        runs = [run for run in state.get("runs", []) if isinstance(run, dict)]
        return {
            "path": str(self.path),
            "run_count": len(runs),
            "memory_count": len(state.get("memory", [])),
            "latest_run_id": state.get("latest_run_id"),
            "updated_at": state.get("updated_at"),
            "metrics": self._derive_metrics(runs),
        }

    def _derive_metrics(self, runs: list[dict[str, Any]]) -> dict[str, Any]:
        run_status_counts: Counter[str] = Counter()
        total_events = 0
        failed_event_count = 0
        total_approvals = 0
        runs_with_approvals = 0
        total_actions_planned = 0

        for run in runs:
            status = str(run.get("status", "")).strip().lower()
            if status:
                run_status_counts[status] += 1

            approvals = run.get("approvals", [])
            if isinstance(approvals, list):
                approval_items = [item for item in approvals if isinstance(item, dict)]
                total_approvals += len(approval_items)
                if approval_items:
                    runs_with_approvals += 1

            events = run.get("events", [])
            if isinstance(events, list):
                total_events += len([item for item in events if isinstance(item, dict)])
                failed_event_count += sum(
                    1
                    for item in events
                    if isinstance(item, dict) and str(item.get("status", "")).strip().lower() == "failed"
                )

            next_actions = run.get("next_actions", [])
            if isinstance(next_actions, list):
                total_actions_planned += len([item for item in next_actions if isinstance(item, dict)])

        total_runs = len(runs)
        success_rate = run_status_counts.get("complete", 0) / total_runs if total_runs else 0.0
        approval_frequency = total_approvals / total_actions_planned if total_actions_planned else 0.0

        return {
            "run_status_counts": dict(run_status_counts),
            "success_rate": success_rate,
            "tool_error_count": failed_event_count,
            "approval_count": total_approvals,
            "approval_frequency": approval_frequency,
            "runs_with_approvals": runs_with_approvals,
            "event_count": total_events,
        }

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": 1,
            "runs": [],
            "memory": [],
            "updated_at": utc_now(),
        }

