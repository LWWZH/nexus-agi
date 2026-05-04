from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus_agi.agent import AppConfig, AgentRuntime, JsonStateStore, LOCAL_PROVIDER_ID, CUSTOM_PROVIDER_ID, Message, ProviderRegistry, RunStatus, SimplePlanner, built_in_provider_specs, main


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class PlannerTests(unittest.TestCase):
    def test_build_plan_creates_multiple_steps(self) -> None:
        plan = SimplePlanner().build_plan("Start implementation for a local-first assistant")
        self.assertGreaterEqual(len(plan.steps), 3)
        self.assertTrue(plan.summary)
        self.assertEqual(plan.source_prompt, "Start implementation for a local-first assistant")


class ProviderRegistryTests(unittest.TestCase):
    def test_built_in_provider_count(self) -> None:
        specs = built_in_provider_specs()
        self.assertEqual(len(specs), 10)
        self.assertEqual(specs[0].provider_id, "openai")
        self.assertEqual(specs[-1].provider_id, "ollama")

    def test_registry_includes_custom_and_local_statuses(self) -> None:
        registry = ProviderRegistry()
        statuses = registry.list_statuses(AppConfig())
        provider_ids = [status.provider_id for status in statuses]
        self.assertIn(CUSTOM_PROVIDER_ID, provider_ids)
        self.assertIn(LOCAL_PROVIDER_ID, provider_ids)

    def test_native_provider_statuses_become_ready_with_api_keys(self) -> None:
        registry = ProviderRegistry()
        config = AppConfig(
            provider_settings={
                "anthropic": {"api_key": "anthropic-key"},
                "google-gemini": {"api_key": "gemini-key"},
                "cohere": {"api_key": "cohere-key"},
            }
        )
        statuses = {status.provider_id: status for status in registry.list_statuses(config)}

        self.assertTrue(statuses["anthropic"].ready)
        self.assertTrue(statuses["google-gemini"].ready)
        self.assertTrue(statuses["cohere"].ready)


class NativeProviderTests(unittest.TestCase):
    def test_anthropic_request_shape_and_text_extraction(self) -> None:
        registry = ProviderRegistry()
        provider = registry.create_provider(
            "anthropic",
            AppConfig(provider_settings={"anthropic": {"api_key": "anthropic-key", "model": "claude-sonnet-4-6"}}),
        )
        captured_requests: list[object] = []

        def fake_urlopen(request, timeout=None):
            captured_requests.append(request)
            return FakeHTTPResponse({"content": [{"type": "text", "text": "Anthropic says hi"}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = provider.complete([Message("system", "system prompt"), Message("user", "hello"), Message("assistant", "prefill")])

        self.assertEqual(response.text, "Anthropic says hi")
        request = captured_requests[0]
        self.assertTrue(str(request.full_url).endswith("/v1/messages"))
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-api-key"], "anthropic-key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "claude-sonnet-4-6")
        self.assertEqual(body["system"], "system prompt")
        self.assertEqual(body["messages"][0]["role"], "user")

    def test_gemini_request_shape_and_text_extraction(self) -> None:
        registry = ProviderRegistry()
        provider = registry.create_provider(
            "google-gemini",
            AppConfig(provider_settings={"google-gemini": {"api_key": "gemini-key", "model": "gemini-2.0-flash"}}),
        )
        captured_requests: list[object] = []

        def fake_urlopen(request, timeout=None):
            captured_requests.append(request)
            return FakeHTTPResponse({"candidates": [{"content": {"parts": [{"text": "Gemini says hi"}]}}]})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = provider.complete([Message("system", "system prompt"), Message("user", "hello"), Message("assistant", "prefill")], temperature=0.1)

        self.assertEqual(response.text, "Gemini says hi")
        request = captured_requests[0]
        self.assertIn("/v1beta/models/gemini-2.0-flash:generateContent", str(request.full_url))
        self.assertIn("key=gemini-key", str(request.full_url))
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "system prompt")
        self.assertEqual(body["contents"][0]["role"], "user")
        self.assertEqual(body["contents"][1]["role"], "model")

    def test_cohere_request_shape_and_text_extraction(self) -> None:
        registry = ProviderRegistry()
        provider = registry.create_provider(
            "cohere",
            AppConfig(provider_settings={"cohere": {"api_key": "cohere-key", "model": "command-r-plus"}}),
        )
        captured_requests: list[object] = []

        def fake_urlopen(request, timeout=None):
            captured_requests.append(request)
            return FakeHTTPResponse({"message": {"content": [{"type": "text", "text": "Cohere says hi"}]}})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = provider.complete([Message("system", "system prompt"), Message("user", "hello"), Message("assistant", "prefill")], temperature=0.2)

        self.assertEqual(response.text, "Cohere says hi")
        request = captured_requests[0]
        self.assertTrue(str(request.full_url).endswith("/v2/chat"))
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["authorization"], "Bearer cohere-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "command-r-plus")
        self.assertEqual(body["messages"][0]["role"], "system")


class RuntimeTests(unittest.TestCase):
    def test_run_completes_with_local_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            run = runtime.run("Write a short implementation plan for nexus-agi")

            self.assertEqual(run.status, RunStatus.COMPLETED)
            self.assertTrue(run.result)
            persisted = store.get_run(run.run_id)
            self.assertEqual(persisted.status, RunStatus.COMPLETED)
            self.assertTrue((workspace / ".nexus-agi" / "state.json").exists())
            self.assertTrue((workspace / ".nexus-agi" / "artifacts" / run.run_id / "result.txt").exists())

    def test_block_and_resume_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            run = runtime.run("Delete the stale draft and write a short report")

            self.assertEqual(run.status, RunStatus.BLOCKED)
            self.assertTrue(run.blocked_step_id)

            approved = runtime.approve(run.run_id)
            self.assertEqual(approved.status, RunStatus.PAUSED)

            resumed = runtime.resume(run.run_id)
            self.assertEqual(resumed.status, RunStatus.COMPLETED)

    def test_invalid_provider_marks_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            store = JsonStateStore(workspace)
            runtime = AgentRuntime(store)
            runtime.merge_config({"default_provider": "missing-provider"})

            run = runtime.run("Draft a release checklist")

            self.assertEqual(run.status, RunStatus.FAILED)
            self.assertIn("unknown provider", run.error)
            persisted = store.get_run(run.run_id)
            self.assertEqual(persisted.status, RunStatus.FAILED)
            self.assertIn("unknown provider", persisted.error)


class CliTests(unittest.TestCase):
    def test_run_command_creates_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            exit_code = main(["--workspace", str(workspace), "run", "Draft a release checklist"])
            self.assertEqual(exit_code, 0)
            self.assertTrue((workspace / ".nexus-agi" / "state.json").exists())

    def test_ask_command_creates_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            exit_code = main(["--workspace", str(workspace), "ask", "Draft a release checklist"])
            self.assertEqual(exit_code, 0)
            self.assertTrue((workspace / ".nexus-agi" / "state.json").exists())

    def test_config_updates_state_and_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            exit_code = main([
                "--workspace",
                str(workspace),
                "config",
                "--default-provider",
                "custom",
                "--provider-setting",
                "custom.base_url=http://127.0.0.1:11434/v1",
            ])
            self.assertEqual(exit_code, 0)

            store = JsonStateStore(workspace)
            config = store.get_config()
            self.assertEqual(
                config.to_dict(),
                {
                    "default_provider": "custom",
                    "provider_settings": {"custom": {"base_url": "http://127.0.0.1:11434/v1"}},
                },
            )
            self.assertTrue((workspace / ".nexus-agi" / "config.json").exists())

    def test_configure_alias_still_updates_state_and_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            exit_code = main([
                "--workspace",
                str(workspace),
                "configure",
                "--default-provider",
                "custom",
            ])
            self.assertEqual(exit_code, 0)

            store = JsonStateStore(workspace)
            config = store.get_config()
            self.assertEqual(config.default_provider, "custom")

    def test_config_providers_lists_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["--workspace", str(workspace), "config", "providers", "--json"])
            self.assertEqual(exit_code, 0)
            statuses = json.loads(output.getvalue())
            provider_ids = {status["provider_id"] for status in statuses}
            self.assertIn(LOCAL_PROVIDER_ID, provider_ids)
            self.assertIn(CUSTOM_PROVIDER_ID, provider_ids)

    def test_config_providers_updates_settings_and_lists_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "--workspace",
                    str(workspace),
                    "config",
                    "providers",
                    "--default-provider",
                    "custom",
                    "--provider-setting",
                    "custom.base_url=http://127.0.0.1:11434/v1",
                    "--json",
                ])
            self.assertEqual(exit_code, 0)

            statuses = json.loads(output.getvalue())
            provider_ids = {status["provider_id"] for status in statuses}
            self.assertIn(LOCAL_PROVIDER_ID, provider_ids)
            self.assertIn(CUSTOM_PROVIDER_ID, provider_ids)

            store = JsonStateStore(workspace)
            config = store.get_config()
            self.assertEqual(config.default_provider, "custom")
            self.assertEqual(config.provider_settings["custom"]["base_url"], "http://127.0.0.1:11434/v1")


if __name__ == "__main__":
    unittest.main()
