from __future__ import annotations

import html
import json
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .agent import ApprovalStatus, AgentRuntime, JsonStateStore, LOCAL_PROVIDER_ID, RunStatus, StepStatus


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


class DashboardApp:
    def __init__(
        self,
        workspace_root: Path,
        store: JsonStateStore | None = None,
        runtime: AgentRuntime | None = None,
        *,
        default_provider_id: str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.store = store or JsonStateStore(self.workspace_root)
        self.runtime = runtime or AgentRuntime(self.store)
        self.default_provider_id = default_provider_id or ""

    def build_state(self, *, page: str = "chat", query: str = "", run_id: str = "") -> dict[str, Any]:
      state = self._snapshot_state()
      runs = self._runs_from_state(state)
      normalized_query = query.strip()
      if normalized_query:
        visible_runs = [run for run in runs if self._matches_query(run, normalized_query)]
        selected_run = visible_runs[-1] if visible_runs else None
      else:
        visible_runs = runs
        selected_run = self._select_run(visible_runs, run_id)

      summary = self._summary_for_state(state, visible_runs)
      provider_statuses = self._provider_statuses(state)
      recent_runs = [self._serialize_run_summary(run) for run in reversed(visible_runs[-12:])]

      return {
        "page": page,
        "workspace": {
          "name": self.workspace_root.name or self.workspace_root.as_posix(),
          "root": str(self.workspace_root),
          "data_dir": str(self._data_dir_path()),
          "state_path": str(self._state_path()),
        },
        "query": normalized_query,
        "selected_run_id": self._run_identifier(selected_run) if selected_run else "",
        "selected_run": self._serialize_run(selected_run) if selected_run else None,
        "recent_runs": recent_runs,
        "runs": recent_runs,
        "conversation": self._conversation_messages(selected_run),
        "summary": summary,
        "config": self._config_to_dict(state),
        "provider_statuses": provider_statuses,
        "selected_provider_id": self._selected_provider_id(state),
        "operations": ["run"],
      }

    def build_html(self, *, page: str = "chat", query: str = "", run_id: str = "") -> str:
      return build_dashboard_html(self.build_state(page=page, query=query, run_id=run_id))

    def submit_prompt(self, prompt: str, *, provider_id: str | None = None, operation: str = "plan") -> Any:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Prompt cannot be empty.")

        resolved_provider = provider_id or self.default_provider_id or self._state_default_provider() or LOCAL_PROVIDER_ID
        normalized_operation = operation.strip().lower() or "plan"

        if normalized_operation == "run":
            return self.runtime.run(normalized_prompt, provider_id=resolved_provider)

        return self.runtime.plan(normalized_prompt, provider_id=resolved_provider)

    def approve_run(self, run_id: str, *, step_id: str | None = None) -> Any:
        if not run_id.strip():
            raise ValueError("run_id cannot be empty.")
        return self.runtime.approve(run_id.strip(), step_id=step_id)

    def resume_run(self, run_id: str) -> Any:
        if not run_id.strip():
            raise ValueError("run_id cannot be empty.")
        return self.runtime.resume(run_id.strip())

    def _snapshot_state(self) -> Any:
        snapshot_fn = getattr(self.runtime, "snapshot", None)
        if callable(snapshot_fn):
            return snapshot_fn()
        return self.store.load_state()

    def _runs_from_state(self, state: Any) -> list[Any]:
        runs = getattr(state, "runs", None)
        if isinstance(runs, list):
            return runs
        list_runs = getattr(self.runtime, "list_runs", None)
        if callable(list_runs):
            return list(list_runs())
        return []

    def _provider_statuses(self, state: Any) -> list[dict[str, Any]]:
        provider_statuses_fn = getattr(self.runtime, "list_provider_statuses", None)
        if callable(provider_statuses_fn):
            statuses = provider_statuses_fn()
            if isinstance(statuses, list):
                return [dict(status) for status in statuses]

        providers = getattr(self.runtime, "providers", None)
        list_statuses = getattr(providers, "list_statuses", None)
        config = getattr(state, "config", None)
        if callable(list_statuses) and config is not None:
            return [status.to_dict() for status in list_statuses(config)]
        return []

    def _summary_for_state(self, state: Any, runs: list[Any]) -> dict[str, Any]:
        summary_fn = getattr(self.store, "summary", None)
        if callable(summary_fn):
            try:
                summary = summary_fn()
            except Exception:
                summary = None
            if isinstance(summary, dict):
                return summary

        run_status_counts: dict[str, int] = {}
        event_count = 0
        tool_error_count = 0
        approval_count = 0
        runs_with_approvals = 0
        total_actions_planned = 0

        for run in runs:
            status = self._enum_value(getattr(run, "status", ""))
            if status:
                run_status_counts[status] = run_status_counts.get(status, 0) + 1

            approvals = self._run_approvals(run)
            if approvals:
                approval_count += len(approvals)
                runs_with_approvals += 1

            events = self._run_events(run)
            event_count += len(events)
            tool_error_count += sum(1 for event in events if self._event_status(event) == "failed")

            total_actions_planned += len(self._run_steps(run))

        total_runs = len(runs)
        success_rate = run_status_counts.get(RunStatus.COMPLETED.value, 0) / total_runs if total_runs else 0.0
        approval_frequency = approval_count / total_actions_planned if total_actions_planned else 0.0

        return {
            "path": str(self._state_path()),
            "run_count": len(runs),
            "memory_count": len(getattr(state, "memory", [])) if getattr(state, "memory", None) is not None else 0,
            "latest_run_id": self._run_identifier(runs[-1]) if runs else "",
            "updated_at": getattr(state, "updated_at", ""),
            "metrics": {
                "run_status_counts": run_status_counts,
                "success_rate": success_rate,
                "tool_error_count": tool_error_count,
                "approval_count": approval_count,
                "approval_frequency": approval_frequency,
                "runs_with_approvals": runs_with_approvals,
                "event_count": event_count,
            },
        }

    def _selected_provider_id(self, state: Any) -> str:
        if self.default_provider_id:
            return self.default_provider_id
        config = getattr(state, "config", None)
        default_provider = getattr(config, "default_provider", "") if config is not None else ""
        return default_provider or LOCAL_PROVIDER_ID

    def _state_default_provider(self) -> str:
        config = self._config_from_state(self._snapshot_state())
        return getattr(config, "default_provider", "") or LOCAL_PROVIDER_ID

    def _config_from_state(self, state: Any) -> Any:
        return getattr(state, "config", None)

    def _config_to_dict(self, state: Any) -> dict[str, Any]:
        config = self._config_from_state(state)
        if config is None:
            return {}
        to_dict = getattr(config, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        return {}

    def _select_run(self, runs: list[Any], run_id: str) -> Any:
        normalized_run_id = run_id.strip()
        if normalized_run_id:
            selected = self._find_run(runs, normalized_run_id)
            if selected is not None:
                return selected

        return runs[-1] if runs else None

    def _conversation_messages(self, run: Any) -> list[dict[str, str]]:
      if run is None:
        return []

      prompt = self._string(getattr(run, "prompt", ""))
      assistant_text = self._string(getattr(run, "result", ""))
      if not assistant_text:
        assistant_text = self._string(getattr(run, "blocked_reason", ""))
      if not assistant_text:
        assistant_text = self._string(getattr(getattr(run, "plan", None), "summary", ""))
      if not assistant_text:
        assistant_text = "Ready."

      conversation = [
        {"role": "user", "label": "You", "content": prompt},
        {"role": "assistant", "label": "Model", "content": assistant_text},
      ]

      if self._string(getattr(run, "error", "")):
        conversation.append({"role": "assistant", "label": "Model", "content": self._string(getattr(run, "error", ""))})

      return conversation

    def _find_run(self, runs: list[Any], run_id: str) -> Any:
        for run in runs:
            if self._run_identifier(run) == run_id:
                return run
        return None

    def _matches_query(self, run: Any, query: str) -> bool:
        normalized = query.strip().lower()
        if not normalized:
            return True

        haystacks: list[str] = [
            self._run_identifier(run),
            self._enum_value(getattr(run, "status", "")),
            self._string(getattr(run, "provider_id", "")),
            self._string(getattr(run, "prompt", "")),
            self._string(getattr(getattr(run, "plan", None), "summary", "")),
            self._string(getattr(getattr(run, "plan", None), "source_prompt", "")),
            self._string(getattr(run, "blocked_reason", "")),
            self._string(getattr(run, "result", "")),
            self._string(getattr(run, "error", "")),
        ]

        for step in self._run_steps(run):
            haystacks.extend(
                [
                    self._string(getattr(step, "title", "")),
                    self._string(getattr(step, "detail", "")),
                    self._string(getattr(step, "notes", "")),
                    self._enum_value(getattr(step, "status", "")),
                    self._enum_value(getattr(step, "approval_status", "")),
                ]
            )

        for event in self._run_events(run):
          event_data = self._serialize_event(event)
          haystacks.append(" ".join(str(value) for value in event_data.values() if value is not None))

        for artifact in self._run_artifacts(run):
          artifact_data = self._serialize_artifact(artifact)
          haystacks.append(" ".join(str(value) for value in artifact_data.values() if value is not None))

        return any(normalized in haystack.lower() for haystack in haystacks if haystack)

    def _serialize_run_summary(self, run: Any) -> dict[str, Any]:
        steps = [self._serialize_step(step) for step in self._run_steps(run)]
        events = [self._serialize_event(event) for event in self._run_events(run)]
        artifacts = [self._serialize_artifact(artifact) for artifact in self._run_artifacts(run)]
        current_step_index = int(getattr(run, "current_step_index", 0) or 0)
        current_step_title = steps[current_step_index]["title"] if 0 <= current_step_index < len(steps) else ""

        return {
            "id": self._run_identifier(run),
            "prompt": self._string(getattr(run, "prompt", "")),
            "provider_id": self._string(getattr(run, "provider_id", LOCAL_PROVIDER_ID)),
            "status": self._enum_value(getattr(run, "status", "")),
            "plan_summary": self._string(getattr(getattr(run, "plan", None), "summary", "")),
            "current_step_index": current_step_index,
            "current_step_title": current_step_title,
            "step_count": len(steps),
            "event_count": len(events),
            "artifact_count": len(artifacts),
            "progress_label": f"{min(current_step_index, len(steps))}/{len(steps)}",
            "blocked_reason": self._string(getattr(run, "blocked_reason", "")),
            "updated_at": self._string(getattr(run, "updated_at", "")),
        }

    def _serialize_run(self, run: Any) -> dict[str, Any]:
        steps = [self._serialize_step(step) for step in self._run_steps(run)]
        events = [self._serialize_event(event) for event in self._run_events(run)]
        artifacts = [self._serialize_artifact(artifact) for artifact in self._run_artifacts(run)]
        current_step_index = int(getattr(run, "current_step_index", 0) or 0)
        current_step_title = steps[current_step_index]["title"] if 0 <= current_step_index < len(steps) else ""
        approval_target_step_id = self._approval_target_step_id(run, steps)
        status = self._enum_value(getattr(run, "status", ""))

        return {
            "id": self._run_identifier(run),
            "prompt": self._string(getattr(run, "prompt", "")),
            "provider_id": self._string(getattr(run, "provider_id", LOCAL_PROVIDER_ID)),
            "status": status,
            "plan": {
                "summary": self._string(getattr(getattr(run, "plan", None), "summary", "")),
                "source_prompt": self._string(getattr(getattr(run, "plan", None), "source_prompt", "")),
                "created_at": self._string(getattr(getattr(run, "plan", None), "created_at", "")),
                "steps": steps,
            },
            "created_at": self._string(getattr(run, "created_at", "")),
            "updated_at": self._string(getattr(run, "updated_at", "")),
            "current_step_index": current_step_index,
            "current_step_title": current_step_title,
            "blocked_step_id": self._string(getattr(run, "blocked_step_id", "")),
            "blocked_reason": self._string(getattr(run, "blocked_reason", "")),
            "result": self._string(getattr(run, "result", "")),
            "error": self._string(getattr(run, "error", "")),
            "events": events,
            "artifacts": artifacts,
            "step_count": len(steps),
            "event_count": len(events),
            "artifact_count": len(artifacts),
            "progress_label": f"{min(current_step_index, len(steps))}/{len(steps)}",
            "approval_target_step_id": approval_target_step_id,
            "can_approve": bool(approval_target_step_id),
            "can_resume": status in {RunStatus.BLOCKED.value, RunStatus.PAUSED.value},
        }

    def _serialize_step(self, step: Any) -> dict[str, Any]:
        return {
            "id": self._step_identifier(step),
            "title": self._string(getattr(step, "title", "")),
            "detail": self._string(getattr(step, "detail", "")),
            "status": self._enum_value(getattr(step, "status", "")),
            "approval_status": self._enum_value(getattr(step, "approval_status", "not_required")),
            "requires_approval": bool(getattr(step, "requires_approval", False)),
            "notes": self._string(getattr(step, "notes", "")),
            "dependencies": [self._string(value) for value in getattr(step, "dependencies", []) or []],
            "evidence": [self._string(value) for value in getattr(step, "evidence", []) or []],
        }

    def _serialize_event(self, event: Any) -> dict[str, Any]:
        if isinstance(event, dict):
            return {str(key): value for key, value in event.items()}

        to_dict = getattr(event, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())

        payload: dict[str, Any] = {}
        for key in ("id", "event_type", "message", "timestamp", "step_id", "artifact_path", "action_id", "tool_name", "summary", "status"):
            value = getattr(event, key, None)
            if value is not None:
                payload[key] = value
        return payload

    def _serialize_artifact(self, artifact: Any) -> dict[str, Any]:
        if isinstance(artifact, dict):
            return {str(key): value for key, value in artifact.items()}

        to_dict = getattr(artifact, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())

        payload: dict[str, Any] = {}
        for key in ("artifact_id", "id", "kind", "title", "path", "description", "created_at"):
            value = getattr(artifact, key, None)
            if value is not None:
                payload[key] = value
        return payload

    def _run_steps(self, run: Any) -> list[Any]:
        plan = getattr(run, "plan", None)
        steps = getattr(plan, "steps", None)
        if isinstance(steps, list):
            return steps
        tasks = getattr(plan, "tasks", None)
        if isinstance(tasks, list):
            return tasks
        return []

    def _run_events(self, run: Any) -> list[Any]:
        events = getattr(run, "events", None)
        if isinstance(events, list):
            return list(events)
        return []

    def _run_artifacts(self, run: Any) -> list[Any]:
        artifacts = getattr(run, "artifacts", None)
        if isinstance(artifacts, list):
            return list(artifacts)
        return []

    def _run_approvals(self, run: Any) -> list[Any]:
        approvals = getattr(run, "approvals", None)
        if isinstance(approvals, list):
            return list(approvals)
        return []

    def _approval_target_step_id(self, run: Any, steps: list[dict[str, Any]]) -> str:
        blocked_step_id = self._string(getattr(run, "blocked_step_id", ""))
        if blocked_step_id:
            return blocked_step_id

        for step in steps:
            if step.get("requires_approval") and step.get("approval_status") != ApprovalStatus.APPROVED.value:
                return self._string(step.get("id", ""))

        return ""

    def _event_status(self, event: Any) -> str:
        status = self._string(getattr(event, "status", ""))
        if status:
            return status.lower()

        if isinstance(event, dict):
            raw_status = self._string(event.get("status", ""))
            if raw_status:
                return raw_status.lower()
            event_type = self._string(event.get("event_type", "")).lower()
            if event_type.endswith("failed") or event_type.endswith("blocked"):
                return "failed" if event_type.endswith("failed") else "blocked"
            if event_type.endswith("completed"):
                return "completed"
            if event_type.endswith("started"):
                return "running"
            return event_type

        return ""

    def _run_identifier(self, run: Any) -> str:
        if run is None:
            return ""
        for attribute in ("run_id", "id"):
            value = getattr(run, attribute, None)
            if value:
                return str(value)
        return ""

    def _step_identifier(self, step: Any) -> str:
        if step is None:
            return ""
        for attribute in ("step_id", "id"):
            value = getattr(step, attribute, None)
            if value:
                return str(value)
        return ""

    def _data_dir_path(self) -> Path:
        data_dir = getattr(self.store, "data_dir", None)
        if data_dir is not None:
            return Path(data_dir)

        state_path = self._state_path()
        return state_path.parent

    def _state_path(self) -> Path:
        state_path = getattr(self.store, "state_path", None)
        if state_path is not None:
            return Path(state_path)

        path = getattr(self.store, "path", None)
        if path is not None:
            return Path(path)

        return self._data_dir_path() / "state.json"

    @staticmethod
    def _string(value: Any) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _enum_value(value: Any) -> str:
        raw_value = getattr(value, "value", value)
        return "" if raw_value is None else str(raw_value)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    app: DashboardApp


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query_params = self._query_params(parsed.query)

        if parsed.path in {"/", "/index.html"}:
            self._redirect("/chat")
            return

        page = self._page_for_path(parsed.path)
        if page is not None:
            html_text = self.server.app.build_html(
                page=page,
                query=query_params.get("q", ""),
                run_id=query_params.get("run_id", ""),
            )
            self._send_html(html_text)
            return

        if parsed.path == "/api/state":
            payload = self.server.app.build_state(
                query=query_params.get("q", ""),
                run_id=query_params.get("run_id", ""),
            )
            self._send_json(payload)
            return

        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return

        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_body()

        try:
            if parsed.path == "/api/submit":
                run = self.server.app.submit_prompt(
                    self._string_value(payload.get("prompt")),
                    provider_id=self._string_value(payload.get("provider_id")) or None,
                    operation=self._string_value(payload.get("operation")) or "plan",
                )
                self._redirect_to_selected_run(run)
                return

            if parsed.path == "/api/approve":
                run = self.server.app.approve_run(
                    self._string_value(payload.get("run_id")),
                    step_id=self._string_value(payload.get("step_id")) or None,
                )
                self._redirect_to_selected_run(run)
                return

            if parsed.path == "/api/resume":
                run = self.server.app.resume_run(self._string_value(payload.get("run_id")))
                self._redirect_to_selected_run(run)
                return

            self._send_text("Not found", HTTPStatus.NOT_FOUND)
        except (FileNotFoundError, ValueError) as exc:
            self._send_text(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - defensive dashboard guard
            self._send_text(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003 - http.server hook
        return

    def _redirect_to_selected_run(self, run: Any) -> None:
        run_id = getattr(run, "run_id", getattr(run, "id", ""))
        location = "/chat"
        if run_id:
            location = f"/chat?{urlencode({'run_id': run_id})}"

        self._redirect(location)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"), "application/json; charset=utf-8", status)

    def _send_text(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(body.encode("utf-8"), "text/plain; charset=utf-8", status)

    def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length).decode("utf-8")
        content_type = self.headers.get("Content-Type", "")

        if "application/json" in content_type:
            if not raw_body.strip():
                return {}
            data = json.loads(raw_body)
            return data if isinstance(data, dict) else {}

        parsed = parse_qs(raw_body, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    @staticmethod
    def _query_params(query: str) -> dict[str, str]:
        parsed = parse_qs(query, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    @staticmethod
    def _page_for_path(path: str) -> str | None:
      if path in {"/chat", "/chat/"}:
        return "chat"
      if path in {"/runs", "/runs/"}:
        return "runs"
      if path in {"/providers", "/providers/"}:
        return "providers"
      return None

    @staticmethod
    def _string_value(value: Any) -> str:
        if value is None:
            return ""
        return str(value)


def run_dashboard(
    workspace_root: Path,
    *,
    data_dir_name: str = ".nexus-agi",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    provider_id: str | None = None,
    open_browser: bool = False,
) -> int:
    store = JsonStateStore(Path(workspace_root), data_dir_name=data_dir_name)
    app = DashboardApp(Path(workspace_root), store=store, default_provider_id=provider_id)
    server = DashboardServer((host, port), DashboardRequestHandler)
    server.app = app

    actual_host, actual_port = server.server_address[:2]
    display_host = "localhost" if str(actual_host) in {"0.0.0.0", "::", ""} else str(actual_host)
    url = f"http://{display_host}:{actual_port}/chat"

    print(f"Nexus AGI dashboard available at {url}")
    print("Press Ctrl+C to stop the server.")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping dashboard.")
    finally:
        server.shutdown()
        server.server_close()

    return 0


def build_dashboard_html(state: dict[str, Any]) -> str:
  page = str(state.get("page") or "chat")
  if page == "runs":
    return _render_runs_page(state)
  if page == "providers":
    return _render_providers_page(state)
  return _render_chat_page(state)


def _render_sidebar(workspace: dict[str, Any]) -> str:
    workspace_name = html.escape(str(workspace.get("name") or "Workspace"))
    workspace_root = html.escape(str(workspace.get("root") or ""))
    state_path = html.escape(str(workspace.get("state_path") or ""))

    return "".join(
        [
            '<aside class="sidebar">',
            '<div class="brand">',
            '<div class="brand-mark">NA</div>',
            '<div>',
            '<div class="brand-kicker">Control</div>',
            '<div class="brand-name">Nexus AGI</div>',
            '</div>',
            '</div>',
            '<nav class="nav-group">',
            '<div class="nav-title">Chat</div>',
            _nav_item("Chat", "/", active=True),
            _nav_item("Overview", "#summary"),
            '</nav>',
            '<nav class="nav-group">',
            '<div class="nav-title">Control</div>',
            _nav_item("Runs", "#runs"),
            _nav_item("Providers", "#providers"),
            _nav_item("Sessions", "#recent-runs"),
            '</nav>',
            '<nav class="nav-group">',
            '<div class="nav-title">Agent</div>',
            _nav_item("Composer", "#composer"),
            _nav_item("State", "/api/state"),
            '</nav>',
            '<nav class="nav-group">',
            '<div class="nav-title">Settings</div>',
            _nav_item("Workspace", "#summary"),
            _nav_item("Docs", "/api/state"),
            '</nav>',
            '<div class="sidebar-footer">',
            f'<div class="sidebar-label">{workspace_name}</div>',
            f'<div class="sidebar-meta">{workspace_root}</div>',
            f'<div class="sidebar-meta">{state_path}</div>',
            '</div>',
            '</aside>',
        ]
    )


def _render_topbar(workspace: dict[str, Any], query: str, selected_run: dict[str, Any] | None) -> str:
    selected_run_id = html.escape(str(selected_run.get("id") if selected_run else ""))
    workspace_name = html.escape(str(workspace.get("name") or "Workspace"))
    refresh_url = _build_link("/", q=query, run_id=selected_run_id)

    return "".join(
        [
            '<header class="topbar">',
            '<div class="crumbs">',
            '<span class="crumb">Nexus AGI</span>',
            '<span class="crumb-sep">/</span>',
            '<span class="crumb crumb-current">Chat</span>',
            '</div>',
            '<form class="search" method="get" action="/">',
            f'<input type="hidden" name="run_id" value="{selected_run_id}">',
            f'<input type="search" name="q" value="{html.escape(query)}" placeholder="Search runs, steps, and events">',
            '<button type="submit">Search</button>',
            '</form>',
            '<div class="top-actions">',
            f'<a class="top-action" href="{html.escape(refresh_url)}">Refresh</a>',
            '<a class="top-action" href="/api/state">State</a>',
            '<a class="top-action" href="#composer">Compose</a>',
            '</div>',
            '</header>',
            '<div class="top-meta">',
            f'<span class="workspace-chip">{workspace_name}</span>',
            f'<span class="workspace-chip workspace-chip-soft">{html.escape("/".join(filter(None, [str(workspace.get("name") or ""), "dashboard"])))}</span>',
            '</div>',
        ]
    )


def _render_control_strip(
    workspace: dict[str, Any],
    provider_statuses: list[dict[str, Any]],
    selected_provider_id: str,
    operations: list[str],
    selected_run: dict[str, Any] | None,
    query: str,
) -> str:
    del query
    provider_statuses = sorted(provider_statuses, key=lambda status: (not bool(status.get("ready", False)), str(status.get("display_name", "")).lower()))
    if selected_provider_id and not any(str(status.get("provider_id", "")) == selected_provider_id for status in provider_statuses):
        provider_statuses = [
            {
                "provider_id": selected_provider_id,
                "display_name": selected_provider_id.title(),
                "default_model": "",
                "ready": False,
                "details": "Selected from the current workspace configuration.",
            },
            *provider_statuses,
        ]

    provider_options = []
    for status in provider_statuses:
        provider_value = html.escape(str(status.get("provider_id") or ""))
        display_name = html.escape(str(status.get("display_name") or provider_value))
        default_model = html.escape(str(status.get("default_model") or ""))
        ready = bool(status.get("ready", False))
        suffix = "ready" if ready else "not ready"
        selected = " selected" if str(status.get("provider_id") or "") == selected_provider_id else ""
        label = display_name
        if default_model:
            label = f"{label} ({default_model})"
        if not ready:
            label = f"{label} - {suffix}"
        provider_options.append(f'<option value="{provider_value}"{selected}>{label}</option>')

    operation_options = []
    for operation in operations or ["plan", "run"]:
        label = operation.title()
        selected = " selected" if operation == "plan" else ""
        operation_options.append(f'<option value="{html.escape(operation)}"{selected}>{html.escape(label)}</option>')

    workspace_name = html.escape(str(workspace.get("name") or "Workspace"))
    status_chip = html.escape(_render_state_chip(selected_run)) if selected_run else "Idle"

    return "".join(
        [
            '<section class="control-strip">',
            f'<span class="workspace-pill">{workspace_name}</span>',
            '<label class="control-select">',
            '<span>Provider</span>',
            '<select name="provider_id" form="composer-form">',
            *provider_options,
            '</select>',
            '</label>',
            '<label class="control-select">',
            '<span>Mode</span>',
            '<select name="operation" form="composer-form">',
            *operation_options,
            '</select>',
            '</label>',
            f'<span class="state-chip">{status_chip}</span>',
            '</section>',
        ]
    )


def _render_feed(selected_run: dict[str, Any] | None, query: str, recent_runs: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    cards.append('<section class="feed" id="runs">')
    cards.append('<div class="section-head">')
    cards.append('<div>')
    cards.append('<div class="section-kicker">Chat</div>')
    cards.append('<h2>Run canvas</h2>')
    cards.append('</div>')
    cards.append(f'<div class="section-meta">{html.escape("No runs yet" if not selected_run else _render_state_chip(selected_run))}</div>')
    cards.append('</div>')

    if selected_run is None:
        cards.append(_render_empty_state(query))
    else:
        cards.extend(_render_run_cards(selected_run))

    cards.append('</section>')
    return "".join(cards)


def _render_run_cards(selected_run: dict[str, Any]) -> list[str]:
    cards: list[str] = []
    plan = dict(selected_run.get("plan") or {})
    steps = list(plan.get("steps") or [])
    events = list(selected_run.get("events") or [])
    artifacts = list(selected_run.get("artifacts") or [])

    cards.append(
        _render_card(
            kicker="Tool output",
            title="Plan summary",
            badge=_render_state_chip(selected_run),
            body_html=_render_plan_body(selected_run),
            variant=_state_variant(selected_run.get("status")),
            open_card=True,
        )
    )

    blocked_reason = str(selected_run.get("blocked_reason") or "")
    if blocked_reason:
        cards.append(
            _render_card(
                kicker="Tool output",
                title="Blocked",
                badge="approval required",
                body_html=_render_text_block(blocked_reason),
                variant="warning",
            )
        )

    for index, step in enumerate(steps, start=1):
        cards.append(
            _render_card(
                kicker="Tool call",
                title=f"Step {index}: {str(step.get('title') or 'Untitled step')}",
                badge=str(step.get("status") or "pending"),
                body_html=_render_step_body(step, index, selected_run),
                variant=_state_variant(step.get("status")),
            )
        )

    for event in events:
        cards.append(
            _render_card(
                kicker="Tool output",
                title=str(event.get("message") or event.get("event_type") or "Event"),
                badge=str(event.get("event_type") or event.get("status") or "event"),
                body_html=_render_event_body(event),
                variant=_event_variant(event),
            )
        )

    if selected_run.get("result"):
        cards.append(
            _render_card(
                kicker="Tool output",
                title="Result",
                badge="completed",
                body_html=_render_text_block(str(selected_run.get("result") or "")),
                variant="success",
            )
        )

    if selected_run.get("error"):
        cards.append(
            _render_card(
                kicker="Tool output",
                title="Error",
                badge="failed",
                body_html=_render_text_block(str(selected_run.get("error") or "")),
                variant="danger",
            )
        )

    if artifacts:
        cards.append(
            _render_card(
                kicker="Tool output",
                title="Artifacts",
                badge=f"{len(artifacts)} files",
                body_html=_render_artifacts_body(artifacts),
                variant="neutral",
            )
        )

    return cards


def _render_empty_state(query: str) -> str:
    if query.strip():
        title = f'No runs matched "{html.escape(query)}"'
        body = "Try a broader search or clear the filter to show the full run history."
        badge = "empty"
    else:
        title = "No runs yet"
        body = "Use the composer to plan or run a prompt and the feed will populate here."
        badge = "start"

    return _render_card(
        kicker="Tool output",
        title=title,
        badge=badge,
        body_html=_render_text_block(body),
        variant="neutral",
        open_card=True,
    )


def _render_summary_panel(summary: dict[str, Any], workspace: dict[str, Any], selected_run: dict[str, Any] | None) -> str:
    metrics = dict(summary.get("metrics") or {})
    success_rate = float(metrics.get("success_rate", 0.0) or 0.0)

    rows = [
        ("Runs", summary.get("run_count", 0)),
        ("Memory", summary.get("memory_count", 0)),
        ("Success", f"{success_rate:.0%}"),
        ("Events", metrics.get("event_count", 0)),
    ]

    current_step = selected_run.get("current_step_title") if selected_run else ""
    current_step_index = selected_run.get("current_step_index") if selected_run else 0
    progress_label = selected_run.get("progress_label") if selected_run else "0/0"

    return "".join(
        [
            '<section class="rail-card" id="summary">',
            '<div class="section-head">',
            '<div>',
            '<div class="section-kicker">Overview</div>',
            '<h3>State snapshot</h3>',
            '</div>',
            f'<div class="section-meta">{html.escape(str(summary.get("latest_run_id") or "No active run"))}</div>',
            '</div>',
            '<div class="stat-grid">',
            *[
                f'<div class="stat"><strong>{html.escape(str(value))}</strong><span>{html.escape(label)}</span></div>'
                for label, value in rows
            ],
            '</div>',
            '<div class="rail-note">',
            f'<div><span class="rail-label">State file</span><div class="rail-value">{html.escape(str(summary.get("path") or workspace.get("state_path") or ""))}</div></div>',
            f'<div><span class="rail-label">Selected run</span><div class="rail-value">{html.escape(str(selected_run.get("id") if selected_run else "None"))}</div></div>',
            f'<div><span class="rail-label">Progress</span><div class="rail-value">{html.escape(str(progress_label))}</div></div>',
            f'<div><span class="rail-label">Current step</span><div class="rail-value">{html.escape(str(current_step or "Idle"))}</div></div>',
            f'<div><span class="rail-label">Step index</span><div class="rail-value">{html.escape(str(current_step_index or 0))}</div></div>',
            '</div>',
            '</section>',
        ]
    )


def _render_actions_panel(selected_run: dict[str, Any] | None) -> str:
    if not selected_run:
        return ""

    approval_target = str(selected_run.get("approval_target_step_id") or "")
    can_approve = bool(selected_run.get("can_approve"))
    can_resume = bool(selected_run.get("can_resume"))

    if not (can_approve or can_resume):
        return ""

    selected_run_id = html.escape(str(selected_run.get("id") or ""))
    step_input = f'<input type="hidden" name="step_id" value="{html.escape(approval_target)}">' if approval_target else ""

    return "".join(
        [
            '<section class="rail-card" id="actions">',
            '<div class="section-head">',
            '<div>',
            '<div class="section-kicker">Control</div>',
            '<h3>Run actions</h3>',
            '</div>',
            '<div class="section-meta">Human-in-the-loop</div>',
            '</div>',
            '<div class="action-stack">',
            can_approve
            and "".join(
                [
                    '<form class="action-form" method="post" action="/api/approve">',
                    f'<input type="hidden" name="run_id" value="{selected_run_id}">',
                    step_input,
                    '<button type="submit" class="action-button action-button-primary">Approve blocked step</button>',
                    '</form>',
                ]
            )
            or "",
            can_resume
            and "".join(
                [
                    '<form class="action-form" method="post" action="/api/resume">',
                    f'<input type="hidden" name="run_id" value="{selected_run_id}">',
                    '<button type="submit" class="action-button">Resume run</button>',
                    '</form>',
                ]
            )
            or "",
            '</div>',
            '</section>',
        ]
    )


def _render_provider_panel(provider_statuses: list[dict[str, Any]]) -> str:
    provider_cards: list[str] = []
    for status in provider_statuses:
        ready = bool(status.get("ready", False))
        provider_cards.append(
            '<div class="provider-row">'
            f'<div class="provider-dot provider-dot-{"ready" if ready else "not-ready"}"></div>'
            '<div class="provider-copy">'
            f'<div class="provider-name">{html.escape(str(status.get("display_name") or status.get("provider_id") or "Provider"))}</div>'
            f'<div class="provider-meta">{html.escape(str(status.get("default_model") or ""))}</div>'
            f'<div class="provider-meta">{html.escape(str(status.get("details") or ""))}</div>'
            '</div>'
            f'<span class="provider-badge provider-badge-{"ready" if ready else "not-ready"}">{"ready" if ready else "not ready"}</span>'
            '</div>'
        )

    return "".join(
        [
            '<section class="rail-card" id="providers">',
            '<div class="section-head">',
            '<div>',
            '<div class="section-kicker">Agent</div>',
            '<h3>Providers</h3>',
            '</div>',
            f'<div class="section-meta">{len(provider_statuses)} available</div>',
            '</div>',
            '<div class="provider-list">',
            *(provider_cards or ['<div class="empty-copy">No provider status information is available.</div>']),
            '</div>',
            '</section>',
        ]
    )


def _render_recent_runs_panel(recent_runs: list[dict[str, Any]], query: str, selected_run: dict[str, Any] | None) -> str:
    items: list[str] = []
    selected_id = str(selected_run.get("id") or "") if selected_run else ""

    for run in recent_runs:
        run_id = str(run.get("id") or "")
        link = _build_link("/", run_id=run_id, q=query)
        active_class = " recent-run active" if run_id == selected_id else " recent-run"
        items.append(
            f'<a class="{active_class.strip()}" href="{html.escape(link)}">'
            '<div class="recent-run-top">'
            f'<strong>{html.escape(str(run.get("provider_id") or "Run"))}</strong>'
            f'<span class="recent-run-status status-{_status_slug(str(run.get("status") or "draft"))}">{html.escape(_display_status(str(run.get("status") or "draft")))}</span>'
            '</div>'
            f'<div class="recent-run-title">{html.escape(str(run.get("prompt") or "Untitled prompt"))}</div>'
            '<div class="recent-run-meta">'
            f'<span>{html.escape(str(run.get("progress_label") or "0/0"))}</span>'
            f'<span>{html.escape(str(run.get("updated_at") or ""))}</span>'
            '</div>'
            '</a>'
        )

    if not items:
        if query.strip():
            items.append('<div class="empty-copy">No runs matched the current search.</div>')
        else:
            items.append('<div class="empty-copy">Start a prompt in the composer to populate recent runs.</div>')

    return "".join(
        [
            '<section class="rail-card" id="recent-runs">',
            '<div class="section-head">',
            '<div>',
            '<div class="section-kicker">Control</div>',
            '<h3>Recent runs</h3>',
            '</div>',
            f'<div class="section-meta">{len(recent_runs)} shown</div>',
            '</div>',
            '<div class="recent-run-list">',
            *items,
            '</div>',
            '</section>',
        ]
    )


def _render_composer(selected_provider_id: str, operations: list[str], selected_run: dict[str, Any] | None) -> str:
  del selected_provider_id, operations, selected_run

  return "".join(
    [
      '<form class="composer" id="composer-form" method="post" action="/api/submit">',
      '<div class="composer-header">',
      '<div>',
      '<div class="section-kicker">Input</div>',
      '<h3>Message Nexus AGI</h3>',
      '</div>',
      '<div class="composer-hint">Enter a prompt and use the top controls to choose the provider and mode.</div>',
      '</div>',
      '<textarea name="prompt" rows="4" placeholder="Describe the next task, goal, or change you want to make."></textarea>',
      '<div class="composer-actions">',
      '<div class="composer-selects">',
      '<span class="workspace-chip workspace-chip-soft">Mode and provider are set above</span>',
      '</div>',
      '<button type="submit" class="send-button">Send</button>',
      '</div>',
      '</form>',
    ]
  )


def _render_plan_body(run: dict[str, Any]) -> str:
    plan = dict(run.get("plan") or {})
    steps = list(plan.get("steps") or [])
    rows = [
        ("Prompt", run.get("prompt", "")),
        ("Summary", plan.get("summary", "")),
        ("Provider", run.get("provider_id", "")),
        ("Status", run.get("status", "")),
        ("Progress", run.get("progress_label", "0/0")),
        ("Updated", run.get("updated_at", "")),
    ]
    body = [_render_kv_grid(rows)]
    if steps:
        body.append('<div class="mini-header">Steps</div>')
        body.append('<div class="step-strip">')
        for index, step in enumerate(steps, start=1):
            body.append(
                '<div class="step-chip">'
                f'<span class="step-chip-index">{index}</span>'
                f'<span class="step-chip-title">{html.escape(str(step.get("title") or "Untitled step"))}</span>'
                f'<span class="step-chip-status status-{_status_slug(str(step.get("status") or "pending"))}">{html.escape(_display_status(str(step.get("status") or "pending")))}</span>'
                '</div>'
            )
        body.append('</div>')
    return "".join(body)


def _render_step_body(step: dict[str, Any], index: int, selected_run: dict[str, Any]) -> str:
    rows = [
        ("Step", step.get("id", f"step-{index}")),
        ("Detail", step.get("detail", "")),
        ("Status", step.get("status", "pending")),
        ("Approval", step.get("approval_status", "not_required")),
        ("Requires approval", "yes" if step.get("requires_approval") else "no"),
        ("Notes", step.get("notes", "")),
        ("Dependencies", ", ".join(step.get("dependencies") or [])),
        ("Evidence", ", ".join(step.get("evidence") or [])),
    ]
    if selected_run.get("current_step_index") == index - 1:
        rows.append(("Current", "yes"))
    if self_status := str(step.get("status") or "").lower() == StepStatus.BLOCKED.value:
        rows.append(("Blocked", "yes"))
    return _render_kv_grid(rows)


def _render_event_body(event: dict[str, Any]) -> str:
    rows = [
        ("Timestamp", event.get("timestamp", "")),
        ("Event", event.get("event_type", "")),
        ("Step", event.get("step_id", "")),
        ("Artifact", event.get("artifact_path", "")),
        ("Action", event.get("action_id", "")),
        ("Tool", event.get("tool_name", "")),
        ("Status", event.get("status", "")),
        ("Summary", event.get("summary", "")),
    ]
    extra = {key: value for key, value in event.items() if key not in {"timestamp", "event_type", "step_id", "artifact_path", "action_id", "tool_name", "summary", "status"} and value not in {None, ""}}
    for key, value in extra.items():
        rows.append((str(key).replace("_", " ").title(), value))
    return _render_kv_grid(rows)


def _render_artifacts_body(artifacts: list[dict[str, Any]]) -> str:
    rows = []
    for artifact in artifacts:
        rows.append(
            _render_kv_grid(
                [
                    ("Title", artifact.get("title", "")),
                    ("Kind", artifact.get("kind", "")),
                    ("Path", artifact.get("path", "")),
                    ("Created", artifact.get("created_at", "")),
                ]
            )
        )
    return "".join(rows)


def _render_kv_grid(items: list[tuple[str, Any]]) -> str:
    rendered_items = []
    for label, value in items:
        if value is None:
            continue
        text = str(value)
        if not text and text != "0":
            continue
        rendered_items.append(
            '<div class="kv-item">'
            f'<span class="kv-label">{html.escape(label)}</span>'
            f'<span class="kv-value">{html.escape(text)}</span>'
            '</div>'
        )
    if not rendered_items:
        rendered_items.append('<div class="empty-copy">No additional details.</div>')
    return '<div class="kv-grid">' + "".join(rendered_items) + '</div>'


def _render_text_block(text: str) -> str:
    return f'<div class="text-block">{html.escape(text)}</div>'


def _render_card(*, kicker: str, title: str, badge: str, body_html: str, variant: str = "neutral", open_card: bool = False) -> str:
    open_attribute = " open" if open_card else ""
    return "".join(
        [
            f'<details class="feed-card feed-card-{_status_slug(variant)}"{open_attribute}>',
            '<summary>',
            '<div class="card-left">',
            f'<span class="card-kicker">{html.escape(kicker)}</span>',
            f'<span class="card-title">{html.escape(title)}</span>',
            '</div>',
            '<div class="card-right">',
            f'<span class="badge badge-{_status_slug(variant)}">{html.escape(badge)}</span>',
            '</div>',
            '</summary>',
            f'<div class="feed-body">{body_html}</div>',
            '</details>',
        ]
    )


def _render_state_chip(run: dict[str, Any] | None) -> str:
    if not run:
        return "Idle"
    status = str(run.get("status") or "draft")
    provider_id = str(run.get("provider_id") or "")
    return f'{_display_status(status)} · {provider_id}' if provider_id else _display_status(status)


def _state_variant(value: Any) -> str:
    status = _status_slug(str(value or ""))
    if status in {"completed", "done", "approved", "success"}:
        return "success"
    if status in {"blocked", "pending", "warning"}:
        return "warning"
    if status in {"failed", "error"}:
        return "danger"
    if status in {"running", "in-progress"}:
        return "accent"
    return "neutral"


def _event_variant(event: dict[str, Any]) -> str:
    status = _status_slug(str(event.get("status") or ""))
    event_type = _status_slug(str(event.get("event_type") or ""))
    if "fail" in status or "fail" in event_type:
        return "danger"
    if "block" in status or "block" in event_type:
        return "warning"
    if "complete" in status or "complete" in event_type:
        return "success"
    if "start" in event_type or "run" in event_type:
        return "accent"
    return "neutral"


def _nav_item(label: str, href: str, *, active: bool = False) -> str:
    active_class = " nav-item-active" if active else ""
    return f'<a class="nav-item{active_class}" href="{html.escape(href)}">{html.escape(label)}</a>'


def _status_slug(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"", "not-required"}:
        return "neutral"
    if normalized in {"pending", "blocked", "paused", "warning"}:
        return "warning"
    if normalized in {"completed", "approved", "success", "done"}:
        return "success"
    if normalized in {"failed", "error", "danger"}:
        return "danger"
    if normalized in {"running", "in-progress", "planned"}:
        return "accent"
    return normalized


def _display_status(value: str) -> str:
    normalized = value.replace("_", " ").strip()
    if not normalized:
        return "Neutral"
    return " ".join(part.capitalize() for part in normalized.split())


def _build_link(path: str, **params: str) -> str:
    filtered = {key: value for key, value in params.items() if value}
    if not filtered:
        return path
    return f"{path}?{urlencode(filtered)}"


DASHBOARD_CSS = """
:root {
  color-scheme: dark;
  --bg: #0a0c12;
  --bg-soft: #0e1118;
  --panel: rgba(16, 18, 26, 0.88);
  --panel-strong: rgba(21, 24, 34, 0.96);
  --panel-soft: rgba(255, 255, 255, 0.03);
  --border: rgba(255, 255, 255, 0.08);
  --border-strong: rgba(255, 255, 255, 0.14);
  --text: #edf1f8;
  --muted: #99a3b5;
  --accent: #ef6c72;
  --accent-soft: rgba(239, 108, 114, 0.16);
  --success: #63d2a4;
  --warning: #ffbf66;
  --danger: #ff7a74;
  --shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
  --shadow-soft: 0 16px 34px rgba(0, 0, 0, 0.28);
  --radius-xl: 28px;
  --radius-lg: 22px;
  --radius-md: 16px;
  --radius-sm: 12px;
}

* {
  box-sizing: border-box;
}

html,
body {
  min-height: 100%;
}

body {
  margin: 0;
  color: var(--text);
  background:
    radial-gradient(circle at 18% 8%, rgba(239, 108, 114, 0.18), transparent 32%),
    radial-gradient(circle at 85% 18%, rgba(99, 210, 164, 0.11), transparent 28%),
    linear-gradient(180deg, #07090d 0%, #0b0d13 55%, #0d1017 100%);
  font-family: "Space Grotesk", "Aptos", "Segoe UI", sans-serif;
  overflow-x: hidden;
}

body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.022) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.022) 1px, transparent 1px);
  background-size: 34px 34px;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.75), transparent 92%);
  opacity: 0.5;
}

a {
  color: inherit;
  text-decoration: none;
}

button,
input,
select,
textarea {
  font: inherit;
}

.shell {
  position: relative;
  display: grid;
  grid-template-columns: 268px minmax(0, 1fr);
  gap: 18px;
  min-height: 100vh;
  padding: 18px;
}

.sidebar {
  position: sticky;
  top: 18px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  height: calc(100vh - 36px);
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  background: linear-gradient(180deg, rgba(13, 16, 23, 0.94), rgba(10, 12, 17, 0.96));
  box-shadow: var(--shadow);
  overflow: auto;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 14px;
  background: linear-gradient(145deg, rgba(239, 108, 114, 0.92), rgba(255, 135, 110, 0.68));
  box-shadow: 0 10px 24px rgba(239, 108, 114, 0.24);
  color: #120e12;
  font-weight: 800;
  letter-spacing: 0.1em;
}

.brand-kicker,
.section-kicker,
.nav-title,
.rail-label,
.sidebar-label {
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.brand-kicker,
.section-kicker,
.nav-title,
.rail-label {
  color: var(--muted);
}

.brand-name {
  margin-top: 3px;
  font-size: 1.05rem;
  font-weight: 700;
}

.nav-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  min-height: 42px;
  padding: 0 14px 0 36px;
  border: 1px solid transparent;
  border-radius: 14px;
  color: var(--muted);
  background: transparent;
  transition: border-color 160ms ease, background 160ms ease, color 160ms ease, transform 160ms ease;
}

.nav-item::before {
  content: "";
  position: absolute;
  left: 14px;
  width: 10px;
  height: 10px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.08);
}

.nav-item:hover {
  color: var(--text);
  border-color: var(--border);
  background: rgba(255, 255, 255, 0.03);
  transform: translateX(2px);
}

.nav-item-active {
  color: var(--text);
  border-color: rgba(239, 108, 114, 0.28);
  background: linear-gradient(90deg, rgba(239, 108, 114, 0.16), rgba(239, 108, 114, 0.05));
}

.nav-item-active::before {
  background: var(--accent);
  box-shadow: 0 0 0 4px rgba(239, 108, 114, 0.12);
}

.sidebar-footer {
  margin-top: auto;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.02);
}

.sidebar-label {
  color: var(--text);
}

.sidebar-meta {
  margin-top: 6px;
  color: var(--muted);
  font-size: 0.82rem;
  word-break: break-word;
}

.workspace {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.topbar,
.top-meta,
.control-strip,
.feed,
.composer,
.rail-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  background: var(--panel);
  box-shadow: var(--shadow-soft);
}

.topbar {
  display: grid;
  grid-template-columns: auto minmax(240px, 1fr) auto;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  backdrop-filter: blur(18px);
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-weight: 600;
}

.crumb-current {
  color: var(--text);
}

.crumb-sep {
  color: rgba(255, 255, 255, 0.22);
}

.search {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.14);
}

.search input {
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text);
}

.search input::placeholder {
  color: rgba(153, 163, 181, 0.72);
}

.search button,
.send-button,
.action-button,
.top-action {
  border: 0;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  color: var(--text);
  cursor: pointer;
  transition: transform 160ms ease, background 160ms ease, border-color 160ms ease;
}

.search button {
  padding: 10px 14px;
}

.search button:hover,
.send-button:hover,
.action-button:hover,
.top-action:hover {
  transform: translateY(-1px);
  background: rgba(255, 255, 255, 0.1);
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.top-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  padding: 0 14px;
  border: 1px solid var(--border);
}

.top-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.02);
}

.workspace-chip,
.state-chip,
.badge,
.provider-badge,
.step-chip-status,
.recent-run-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 600;
}

.workspace-chip {
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.03);
}

.workspace-chip-soft {
  color: var(--muted);
}

.state-chip,
.badge-neutral,
.provider-badge-not-ready,
.step-chip-status-neutral,
.recent-run-status.status-draft,
.recent-run-status.status-planned,
.recent-run-status.status-paused {
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.03);
}

.badge-accent,
.step-chip-status-accent,
.recent-run-status.status-running {
  border: 1px solid rgba(239, 108, 114, 0.32);
  background: rgba(239, 108, 114, 0.12);
  color: #ffd6d8;
}

.badge-success,
.step-chip-status-success,
.recent-run-status.status-completed,
.recent-run-status.status-complete,
.provider-badge-ready {
  border: 1px solid rgba(99, 210, 164, 0.28);
  background: rgba(99, 210, 164, 0.12);
  color: #d7fff0;
}

.badge-warning,
.step-chip-status-warning,
.recent-run-status.status-blocked {
  border: 1px solid rgba(255, 191, 102, 0.26);
  background: rgba(255, 191, 102, 0.12);
  color: #ffe8bf;
}

.badge-danger,
.recent-run-status.status-failed {
  border: 1px solid rgba(255, 122, 116, 0.26);
  background: rgba(255, 122, 116, 0.12);
  color: #ffd4d1;
}

.control-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  flex-wrap: wrap;
}

.workspace-pill {
  display: inline-flex;
  align-items: center;
  min-height: 38px;
  padding: 0 14px;
  border: 1px solid rgba(239, 108, 114, 0.22);
  border-radius: 999px;
  background: rgba(239, 108, 114, 0.1);
  color: #ffd7d8;
  font-weight: 700;
}

.control-select,
.composer-select {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 220px;
}

.control-select span,
.composer-select span {
  color: var(--muted);
  font-size: 0.68rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.control-select select,
.composer-select select,
.composer textarea {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.18);
  color: var(--text);
}

.control-select select,
.composer-select select {
  min-height: 42px;
  padding: 0 12px;
}

.content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  min-width: 0;
}

.feed-column {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.feed,
.composer {
  padding: 18px;
}

.feed {
  display: flex;
  flex-direction: column;
  gap: 14px;
  backdrop-filter: blur(18px);
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.section-head h2,
.section-head h3,
.composer-header h3 {
  margin: 0;
  font-size: 1.22rem;
  line-height: 1.1;
}

.section-meta,
.composer-hint {
  color: var(--muted);
  font-size: 0.84rem;
}

.feed-card {
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.015));
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
  animation: lift-in 260ms ease both;
}

.feed-card:hover {
  transform: translateY(-1px);
  border-color: rgba(255, 255, 255, 0.16);
}

.feed-card summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  padding: 14px 16px;
  cursor: pointer;
  list-style: none;
}

.feed-card summary::-webkit-details-marker {
  display: none;
}

.card-left,
.card-right,
.recent-run-top,
.composer-header,
.composer-actions,
.step-chip,
.provider-row,
.rail-note {
  display: flex;
  align-items: center;
  gap: 10px;
}

.card-left {
  flex-wrap: wrap;
}

.card-kicker,
.card-title {
  font-weight: 600;
}

.card-kicker {
  color: var(--muted);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
}

.card-title {
  font-size: 1rem;
}

.feed-body {
  padding: 0 16px 16px;
}

.kv-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.kv-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.12);
}

.kv-label,
.rail-label {
  color: var(--muted);
  font-size: 0.7rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.kv-value,
.text-block,
.provider-meta,
.recent-run-title,
.rail-value,
.empty-copy,
.composer textarea {
  line-height: 1.55;
  white-space: pre-wrap;
}

.kv-value {
  color: var(--text);
}

.text-block {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.12);
  color: var(--text);
}

.mini-header {
  margin: 16px 0 10px;
  color: var(--muted);
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.step-strip {
  display: grid;
  gap: 8px;
}

.step-chip {
  justify-content: space-between;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.14);
}

.step-chip-index {
  display: inline-grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  color: var(--text);
  font-size: 0.72rem;
  font-weight: 700;
}

.step-chip-title {
  flex: 1;
  min-width: 0;
  color: var(--text);
}

.feed-card-neutral,
.feed-card-accent,
.feed-card-success,
.feed-card-warning,
.feed-card-danger {
  border-left: 3px solid rgba(255, 255, 255, 0.12);
}

.feed-card-accent {
  border-left-color: var(--accent);
}

.feed-card-success {
  border-left-color: var(--success);
}

.feed-card-warning {
  border-left-color: var(--warning);
}

.feed-card-danger {
  border-left-color: var(--danger);
}

.rail {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.rail-card {
  padding: 18px;
  background: var(--panel-strong);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.03);
}

.stat strong {
  font-size: 1.35rem;
}

.rail-note {
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
  margin-top: 14px;
}

.rail-value {
  color: var(--text);
  margin-top: 4px;
  word-break: break-word;
}

.action-stack,
.provider-list,
.recent-run-list {
  display: grid;
  gap: 12px;
}

.action-form {
  display: grid;
}

.action-button {
  min-height: 42px;
  border: 1px solid var(--border);
  padding: 0 14px;
}

.action-button-primary {
  border-color: rgba(239, 108, 114, 0.32);
  background: rgba(239, 108, 114, 0.14);
}

.provider-row {
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.03);
}

.provider-dot {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  margin-top: 6px;
  border-radius: 999px;
}

.provider-dot-ready {
  background: var(--success);
  box-shadow: 0 0 0 4px rgba(99, 210, 164, 0.14);
}

.provider-dot-not-ready {
  background: var(--warning);
  box-shadow: 0 0 0 4px rgba(255, 191, 102, 0.12);
}

.provider-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.provider-name,
.recent-run-title {
  font-weight: 600;
}

.provider-meta,
.recent-run-meta {
  color: var(--muted);
  font-size: 0.84rem;
}

.provider-badge {
  flex: 0 0 auto;
}

.recent-run {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.03);
  transition: border-color 160ms ease, transform 160ms ease, background 160ms ease;
}

.recent-run:hover {
  transform: translateY(-1px);
  border-color: rgba(255, 255, 255, 0.16);
}

.recent-run.active {
  border-color: rgba(239, 108, 114, 0.32);
  background: rgba(239, 108, 114, 0.08);
}

.recent-run-top {
  justify-content: space-between;
}

.recent-run-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.composer {
  display: grid;
  gap: 14px;
}

.composer-header {
  justify-content: space-between;
  align-items: flex-start;
}

.composer textarea {
  min-height: 138px;
  padding: 14px 16px;
  border-radius: 18px;
  resize: vertical;
  outline: none;
  background: rgba(0, 0, 0, 0.16);
  color: var(--text);
}

.composer textarea::placeholder {
  color: rgba(153, 163, 181, 0.72);
}

.composer-actions {
  justify-content: space-between;
  align-items: flex-end;
  gap: 14px;
  flex-wrap: wrap;
}

.composer-selects {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}

.send-button {
  min-height: 46px;
  padding: 0 18px;
  border: 1px solid rgba(239, 108, 114, 0.32);
  background: linear-gradient(180deg, rgba(239, 108, 114, 0.24), rgba(239, 108, 114, 0.14));
  font-weight: 700;
}

.empty-copy {
  padding: 12px;
  border: 1px dashed var(--border-strong);
  border-radius: 14px;
  color: var(--muted);
  background: rgba(255, 255, 255, 0.02);
}

@keyframes lift-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 1280px) {
  .content-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .rail {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .rail-card:last-child {
    grid-column: 1 / -1;
  }
}

@media (max-width: 980px) {
  .shell {
    grid-template-columns: minmax(0, 1fr);
  }

  .sidebar {
    position: relative;
    top: 0;
    height: auto;
  }

  .topbar {
    grid-template-columns: 1fr;
  }

  .control-select,
  .composer-select {
    min-width: 0;
    width: 100%;
  }

  .rail {
    grid-template-columns: minmax(0, 1fr);
  }

  .kv-grid,
  .stat-grid {
    grid-template-columns: 1fr;
  }
}
"""


MODERN_DASHBOARD_CSS = """
:root {
  color-scheme: dark;
  --bg: #0a0d14;
  --panel: rgba(18, 22, 31, 0.92);
  --panel-soft: rgba(255, 255, 255, 0.04);
  --border: rgba(255, 255, 255, 0.08);
  --border-strong: rgba(255, 255, 255, 0.16);
  --text: #eef2f8;
  --muted: #97a3b6;
  --accent: #f06f72;
  --accent-soft: rgba(240, 111, 114, 0.18);
  --assistant: #171c27;
  --shadow: 0 22px 60px rgba(0, 0, 0, 0.42);
}

* {
  box-sizing: border-box;
}

html,
body {
  height: 100%;
}

body {
  margin: 0;
  color: var(--text);
  background:
    radial-gradient(circle at 18% 12%, rgba(240, 111, 114, 0.2), transparent 28%),
    radial-gradient(circle at 82% 18%, rgba(94, 129, 255, 0.12), transparent 24%),
    linear-gradient(180deg, #07090f 0%, #0b0e16 100%);
  font-family: "Space Grotesk", "Aptos", "Segoe UI", sans-serif;
  overflow: hidden;
}

body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.02) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
  background-size: 36px 36px;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.72), transparent 95%);
}

a,
button,
input,
textarea {
  font: inherit;
}

a {
  color: inherit;
  text-decoration: none;
}

.app {
  position: relative;
  height: 100dvh;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
}

.app.chat-page {
  grid-template-rows: auto minmax(0, 1fr) auto;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px 14px;
}

.page-title {
  margin: 0;
  font-size: 1.25rem;
  line-height: 1.1;
}

.page-subtitle {
  margin-top: 6px;
  color: var(--muted);
  font-size: 0.88rem;
}

.tab-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.03);
  box-shadow: var(--shadow);
}

.tab,
.tab-active {
  display: inline-flex;
  align-items: center;
  min-height: 38px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1px solid transparent;
  font-weight: 600;
}

.tab {
  border-color: transparent;
  color: var(--muted);
  background: transparent;
  cursor: pointer;
}

.tab:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.06);
}

.tab-active {
  border-color: rgba(240, 111, 114, 0.28);
  background: rgba(240, 111, 114, 0.14);
  color: var(--text);
}

.tab[disabled] {
  opacity: 0.75;
  cursor: default;
}

.chat-stage,
.list-stage {
  min-height: 0;
  padding: 0 20px 20px;
}

.chat-stage {
  display: grid;
  grid-template-rows: minmax(0, 1fr);
}

.conversation-panel {
  min-height: 0;
  overflow: auto;
  padding-right: 4px;
}

.conversation-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 920px;
  margin: 0 auto;
  padding-bottom: 20px;
}

.bubble {
  display: grid;
  gap: 8px;
  max-width: min(720px, 92%);
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 24px;
  box-shadow: var(--shadow);
}

.bubble-user {
  margin-left: auto;
  border-color: rgba(240, 111, 114, 0.22);
  background: linear-gradient(180deg, rgba(240, 111, 114, 0.22), rgba(240, 111, 114, 0.12));
  border-bottom-right-radius: 10px;
}

.bubble-assistant {
  margin-right: auto;
  background: linear-gradient(180deg, rgba(24, 28, 39, 0.98), rgba(20, 24, 34, 0.98));
  border-bottom-left-radius: 10px;
}

.bubble-label {
  color: var(--muted);
  font-size: 0.72rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.bubble-content {
  white-space: pre-wrap;
  line-height: 1.6;
}

.bubble-empty {
  margin: auto;
  max-width: 520px;
  text-align: center;
  border: 1px dashed var(--border-strong);
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--muted);
}

.composer-bar {
  display: grid;
  gap: 12px;
  padding: 16px 20px 20px;
  border-top: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(11, 14, 20, 0.18), rgba(11, 14, 20, 0.88));
  backdrop-filter: blur(18px);
}

.composer-shell {
  display: grid;
  gap: 10px;
  max-width: 920px;
  width: 100%;
  margin: 0 auto;
}

.composer-input {
  width: 100%;
  min-height: 96px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
  resize: vertical;
  outline: none;
}

.composer-input::placeholder {
  color: rgba(151, 163, 182, 0.72);
}

.composer-actions {
  display: flex;
  justify-content: flex-end;
}

.send-button {
  min-height: 44px;
  padding: 0 18px;
  border: 1px solid rgba(240, 111, 114, 0.3);
  border-radius: 999px;
  background: linear-gradient(180deg, rgba(240, 111, 114, 0.24), rgba(240, 111, 114, 0.14));
  color: var(--text);
  font-weight: 700;
  cursor: pointer;
}

.page-list {
  min-height: 0;
  overflow: auto;
}

.page-list-inner {
  max-width: 1040px;
  margin: 0 auto;
  padding: 0 20px 20px;
  display: grid;
  gap: 14px;
}

.run-card,
.provider-card {
  display: grid;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.03);
  box-shadow: var(--shadow);
}

.run-card-top,
.provider-card-top {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
}

.run-card-title,
.provider-card-title {
  font-weight: 700;
}

.run-card-meta,
.provider-card-meta {
  color: var(--muted);
  font-size: 0.84rem;
}

.chip {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.04);
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 600;
}

.chip-success {
  border-color: rgba(96, 209, 161, 0.25);
  background: rgba(96, 209, 161, 0.12);
  color: #dfffee;
}

.chip-warning {
  border-color: rgba(255, 191, 102, 0.25);
  background: rgba(255, 191, 102, 0.12);
  color: #fff0ce;
}

.chip-danger {
  border-color: rgba(255, 122, 116, 0.25);
  background: rgba(255, 122, 116, 0.12);
  color: #ffe2e0;
}

.open-run-button {
  justify-self: start;
  min-height: 40px;
  padding: 0 14px;
  border: 1px solid rgba(240, 111, 114, 0.28);
  border-radius: 999px;
  background: rgba(240, 111, 114, 0.14);
  color: var(--text);
  cursor: pointer;
}

.provider-grid {
  max-width: 1040px;
  margin: 0 auto;
  padding: 0 20px 20px;
  display: grid;
  gap: 14px;
}

.empty-state {
  max-width: 620px;
  margin: 24px auto 0;
  padding: 18px;
  border: 1px dashed var(--border-strong);
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--muted);
  text-align: center;
}

@media (max-width: 860px) {
  .page-header {
    flex-direction: column;
  }

  .tab-strip {
    width: 100%;
    justify-content: space-between;
    overflow-x: auto;
  }

  .bubble {
    max-width: 100%;
  }

  .composer-actions {
    justify-content: stretch;
  }

  .send-button {
    width: 100%;
  }
}
"""


def _render_page(*, title: str, page: str, body_html: str) -> str:
    return (
        "<!doctype html>"
        "<html lang='en'>"
        "<head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='color-scheme' content='dark'>"
        f"<title>{html.escape(title)}</title>"
        f"<style>{MODERN_DASHBOARD_CSS}</style>"
        "</head>"
        f"<body class='app {html.escape(page)}-page' data-page='{html.escape(page)}'>"
        f"{body_html}"
        "</body>"
        "</html>"
    )


def _render_tabs(active_page: str) -> str:
    tabs = [
        ("chat", "Chat", "/chat"),
        ("runs", "Runs", "/runs"),
        ("providers", "Providers", "/providers"),
    ]
    parts = ['<form class="tab-strip" method="get" autocomplete="off">']
    for page, label, path in tabs:
        if page == active_page:
            parts.append(f'<span class="tab-active" aria-current="page">{html.escape(label)}</span>')
        else:
            parts.append(
                f'<button class="tab" type="submit" formaction="{html.escape(path)}" formtarget="_blank">{html.escape(label)}</button>'
            )
    parts.append("</form>")
    return "".join(parts)


def _render_chat_page(state: dict[str, Any]) -> str:
    workspace = dict(state.get("workspace") or {})
    selected_run = dict(state.get("selected_run") or {}) if state.get("selected_run") else None
    selected_provider_id = str(state.get("selected_provider_id") or LOCAL_PROVIDER_ID)
    conversation = list(state.get("conversation") or [])
    query = str(state.get("query") or "")

    subtitle = (
        f'Showing runs matching "{html.escape(query)}".'
        if query.strip()
        else "A clean chat surface for the latest run in the current workspace."
    )

    header = "".join(
        [
            '<header class="page-header">',
            '<div>',
            '<h1 class="page-title">Conversation</h1>',
            f'<div class="page-subtitle">{subtitle}</div>',
            '</div>',
            _render_tabs("chat"),
            '</header>',
        ]
    )

    run_controls = _render_run_controls(selected_run)
    if selected_run is None or not conversation:
        if query.strip():
            conversation_html = f'<div class="bubble bubble-empty">No runs matched "{html.escape(query)}". Try a broader search or open a different run.</div>'
        else:
            conversation_html = '<div class="bubble bubble-empty">Start a conversation to see the exchange here.</div>'
    else:
        conversation_html = _render_chat_bubbles(conversation)

    body = "".join(
        [
            header,
            run_controls,
            '<main class="chat-stage">',
            '<section class="conversation-panel">',
            f'<div class="conversation-list" data-workspace="{html.escape(str(workspace.get("name") or "Workspace"))}">',
            conversation_html,
            '</div>',
            '</section>',
            '</main>',
            _render_chat_composer(selected_provider_id),
        ]
    )
    return _render_page(title="Nexus AGI - Chat", page="chat", body_html=body)


def _render_chat_bubbles(conversation: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for message in conversation:
        role = str(message.get("role") or "assistant")
        label = html.escape(str(message.get("label") or ("You" if role == "user" else "Model")))
        content = _format_message_content(str(message.get("content") or ""))
        parts.append(
            "".join(
                [
                    f'<article class="bubble bubble-{"user" if role == "user" else "assistant"}">',
                    f'<div class="bubble-label">{label}</div>',
                    f'<div class="bubble-content">{content}</div>',
                    '</article>',
                ]
            )
        )
    return "".join(parts)


def _render_chat_composer(selected_provider_id: str) -> str:
    return "".join(
        [
            '<form class="composer-bar" method="post" action="/api/submit">',
            '<div class="composer-shell">',
            '<input type="hidden" name="operation" value="run">',
            f'<input type="hidden" name="provider_id" value="{html.escape(selected_provider_id)}">',
            '<textarea class="composer-input" name="prompt" rows="4" placeholder="Send a message..."></textarea>',
            '<div class="composer-actions">',
            '<button class="send-button" type="submit">Send</button>',
            '</div>',
            '</div>',
            '</form>',
        ]
    )


def _render_run_controls(selected_run: dict[str, Any] | None) -> str:
    if not selected_run:
        return ""

    can_approve = bool(selected_run.get("can_approve"))
    can_resume = bool(selected_run.get("can_resume"))
    if not (can_approve or can_resume):
        return ""

    status = str(selected_run.get("status") or "draft")
    status_class = _status_chip_class(status)
    status_label = _display_status(status)
    details = (
        str(selected_run.get("blocked_reason") or "")
        or str(selected_run.get("current_step_title") or "")
        or str(selected_run.get("plan", {}).get("summary") or "")
        or "Review the current run state."
    )
    run_id = html.escape(str(selected_run.get("id") or ""))
    step_id = html.escape(str(selected_run.get("approval_target_step_id") or ""))

    buttons: list[str] = []
    if can_approve:
        buttons.append(
            "".join(
                [
                    '<form method="post" action="/api/approve">',
                    f'<input type="hidden" name="run_id" value="{run_id}">',
                    f'<input type="hidden" name="step_id" value="{step_id}">',
                    '<button class="send-button" type="submit">Approve blocked step</button>',
                    '</form>',
                ]
            )
        )
    if can_resume:
        buttons.append(
            "".join(
                [
                    '<form method="post" action="/api/resume">',
                    f'<input type="hidden" name="run_id" value="{run_id}">',
                    '<button class="open-run-button" type="submit">Resume run</button>',
                    '</form>',
                ]
            )
        )

    return "".join(
        [
            '<section class="run-card">',
            '<div class="run-card-top">',
            '<div class="run-card-title">Run controls</div>',
            f'<span class="chip {status_class}">{html.escape(status_label)}</span>',
            '</div>',
            f'<div class="run-card-meta">{html.escape(details)}</div>',
            '<div class="composer-actions">',
            *buttons,
            '</div>',
            '</section>',
        ]
    )


def _render_runs_page(state: dict[str, Any]) -> str:
    runs = list(state.get("runs") or [])
    query = str(state.get("query") or "")
    subtitle = (
        f'Showing runs matching "{html.escape(query)}".'
        if query.strip()
        else "Open any run in a separate chat window."
    )
    header = "".join(
        [
            '<header class="page-header">',
            '<div>',
            '<h1 class="page-title">Runs</h1>',
            f'<div class="page-subtitle">{subtitle}</div>',
            '</div>',
            _render_tabs("runs"),
            '</header>',
        ]
    )

    if not runs:
        if query.strip():
            body = f'<div class="empty-state">No runs matched "{html.escape(query)}". Try a broader search or clear the filter.</div>'
        else:
            body = '<div class="empty-state">No runs yet. Create one from the chat tab.</div>'
    else:
        body = ''.join(_render_run_card(run) for run in runs)

    return _render_page(
        title="Nexus AGI - Runs",
        page="runs",
        body_html=header + f'<main class="page-list"><div class="page-list-inner">{body}</div></main>',
    )


def _render_run_card(run: dict[str, Any]) -> str:
    status = str(run.get("status") or "draft")
    chip_class = _status_chip_class(status)
    open_label = "Open conversation"
    return "".join(
        [
            '<form class="run-card" method="get" action="/chat">',
            f'<input type="hidden" name="run_id" value="{html.escape(str(run.get("id") or ""))}">',
            '<div class="run-card-top">',
            f'<div class="run-card-title">{html.escape(str(run.get("prompt") or "Untitled run"))}</div>',
            f'<span class="chip {chip_class}">{html.escape(_display_status(status))}</span>',
            '</div>',
            f'<div class="run-card-meta">{html.escape(str(run.get("provider_id") or ""))} · {html.escape(str(run.get("updated_at") or ""))}</div>',
            f'<div class="run-card-meta">{html.escape(str(run.get("plan_summary") or ""))}</div>',
            '<button class="open-run-button" type="submit" formtarget="_blank">',
            html.escape(open_label),
            '</button>',
            '</form>',
        ]
    )


def _render_providers_page(state: dict[str, Any]) -> str:
    provider_statuses = list(state.get("provider_statuses") or [])
    header = "".join(
        [
            '<header class="page-header">',
            '<div>',
            '<h1 class="page-title">Providers</h1>',
            '<div class="page-subtitle">Only the provider readiness summary is shown here.</div>',
            '</div>',
            _render_tabs("providers"),
            '</header>',
        ]
    )

    if not provider_statuses:
        body = '<div class="empty-state">No provider information available.</div>'
    else:
        body = ''.join(_render_provider_card(status) for status in provider_statuses)

    return _render_page(
        title="Nexus AGI - Providers",
        page="providers",
        body_html=header + f'<main class="page-list"><div class="provider-grid">{body}</div></main>',
    )


def _render_provider_card(status: dict[str, Any]) -> str:
    ready = bool(status.get("ready", False))
    chip_class = "chip-success" if ready else "chip-warning"
    return "".join(
        [
            '<section class="provider-card">',
            '<div class="provider-card-top">',
            f'<div class="provider-card-title">{html.escape(str(status.get("display_name") or status.get("provider_id") or "Provider"))}</div>',
            f'<span class="chip {chip_class}">{"Ready" if ready else "Not ready"}</span>',
            '</div>',
            f'<div class="provider-card-meta">{html.escape(str(status.get("default_model") or ""))}</div>',
            f'<div class="provider-card-meta">{html.escape(str(status.get("details") or ""))}</div>',
            '</section>',
        ]
    )


def _format_message_content(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def _status_chip_class(status: str) -> str:
    normalized = status.lower().strip()
    if normalized in {RunStatus.COMPLETED.value, RunStatus.COMPLETE.value if hasattr(RunStatus, "COMPLETE") else "complete"}:
        return "chip-success"
    if normalized in {RunStatus.BLOCKED.value, RunStatus.PAUSED.value, RunStatus.DRAFT.value}:
        return "chip-warning"
    if normalized in {RunStatus.FAILED.value}:
        return "chip-danger"
    return "chip"