from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nexus_agi.dashboard import DashboardApp, build_dashboard_html
from nexus_agi.agent import AgentRuntime, JsonStateStore, RunStatus


class DashboardTests(unittest.TestCase):
    def test_chat_page_renders_conversation_and_composer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            run = runtime.plan("Build a dashboard similar to the chat layout")

            app = DashboardApp(workspace, store=store, runtime=runtime)
            state = app.build_state(page="chat")
            html = build_dashboard_html(state)

            self.assertIsNotNone(state["selected_run"])
            self.assertEqual(state["selected_run"]["id"], getattr(run, "run_id", getattr(run, "id", "")))
            self.assertTrue(state["updated_at"])
            self.assertGreaterEqual(len(state["recent_runs"]), 1)
            self.assertEqual(len(state["conversation"]), 2)
            self.assertIn('<aside class="sidebar">', html)
            self.assertIn('<div class="shell">', html)
            self.assertNotIn('tab-strip', html)
            self.assertIn('Conversation', html)
            self.assertIn('History', html)
            self.assertIn('Runtime State', html)
            self.assertIn("Conversation", html)
            self.assertIn("bubble-user", html)
            self.assertIn("bubble-assistant", html)
            self.assertIn("composer-bar", html)
            self.assertIn("data-state-updated-at", html)
            self.assertIn("window.setInterval(refreshShell, refreshIntervalMs)", html)
            self.assertIn("window.scrollTo(scrollX, scrollY)", html)
            self.assertIn("/history", html)
            self.assertIn("/config", html)

    def test_query_filters_to_matching_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            runtime.plan("Draft a release checklist")
            matched = runtime.plan("Implement login flow")

            app = DashboardApp(workspace, store=store, runtime=runtime)
            state = app.build_state(page="chat", query="login")

            self.assertEqual(state["query"], "login")
            self.assertEqual(len(state["runs"]), 1)
            self.assertEqual(state["selected_run"]["id"], getattr(matched, "run_id", getattr(matched, "id", "")))
            self.assertIn("login", state["selected_run"]["prompt"].lower())

    def test_history_page_opens_conversations_in_new_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            runtime.plan("Draft a release checklist")

            app = DashboardApp(workspace, store=store, runtime=runtime)
            state = app.build_state(page="history")
            html = build_dashboard_html(state)

            self.assertGreaterEqual(len(state["runs"]), 1)
            self.assertIn("History", html)
            self.assertIn("run-card", html)
            self.assertIn("Open conversation", html)
            self.assertIn('formtarget="_blank"', html)

    def test_config_page_is_compact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            app = DashboardApp(workspace, store=store, runtime=runtime)

            state = app.build_state(page="config")
            html = build_dashboard_html(state)

            self.assertIn("Config", html)
            self.assertIn("provider-card", html)
            self.assertNotIn('<form class="composer-bar"', html)

            legacy_html = build_dashboard_html(app.build_state(page="providers"))
            self.assertIn("Config", legacy_html)

    def test_blocked_run_exposes_approval_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            run = runtime.run("Delete the stale draft and write a short report")

            self.assertEqual(run.status, RunStatus.BLOCKED)

            app = DashboardApp(workspace, store=store, runtime=runtime)
            state = app.build_state(page="chat", run_id=run.run_id)
            html = build_dashboard_html(state)

            self.assertIn("Approve blocked step", html)
            self.assertIn("/api/approve", html)

            runtime.approve(run.run_id)
            resumed_state = app.build_state(page="chat", run_id=run.run_id)
            resumed_html = build_dashboard_html(resumed_state)

            self.assertIn("Resume run", resumed_html)
            self.assertIn("/api/resume", resumed_html)


if __name__ == "__main__":
    unittest.main()