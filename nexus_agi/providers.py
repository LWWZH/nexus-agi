from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .models import AppConfig, Message


LOCAL_PROVIDER_ID = "local"
CUSTOM_PROVIDER_ID = "custom"


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
    supports_streaming: bool = True
    supports_tool_calls: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "adapter_kind": self.adapter_kind,
            "default_model": self.default_model,
            "env_vars": list(self.env_vars),
            "default_base_url": self.default_base_url,
            "chat_path": self.chat_path,
            "auth_header": self.auth_header,
            "auth_prefix": self.auth_prefix,
            "supports_streaming": self.supports_streaming,
            "supports_tool_calls": self.supports_tool_calls,
            "notes": self.notes,
        }


@dataclass(slots=True)
class ProviderResponse:
    provider_id: str
    model: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "text": self.text,
            "raw": self.raw,
        }


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


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    def __init__(self, spec: ProviderSpec):
        self.spec = spec

    @property
    def provider_id(self) -> str:
        return self.spec.provider_id

    def validate(self) -> list[str]:
        return []

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        raise NotImplementedError


class LocalEchoProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(
            ProviderSpec(
                provider_id=LOCAL_PROVIDER_ID,
                display_name="Local Echo",
                adapter_kind="local",
                default_model="nexus-local",
                supports_streaming=False,
                supports_tool_calls=False,
                notes="Deterministic offline provider used for local testing.",
            )
        )

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        user_messages = [message.content for message in messages if message.role == "user"]
        prompt = user_messages[-1] if user_messages else (messages[-1].content if messages else "")
        fragments = [fragment.strip() for fragment in prompt.replace("\n", ". ").split(".") if fragment.strip()]
        if not fragments:
            fragments = ["No prompt was provided."]
        body = "\n".join(f"- {fragment}" for fragment in fragments[:5])
        text = f"Local execution summary:\n{body}"
        return ProviderResponse(provider_id=self.provider_id, model=self.spec.default_model, text=text, raw={"messages": [message.to_dict() for message in messages]})


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

    def validate(self) -> list[str]:
        missing: list[str] = []
        if not self.base_url:
            missing.append("base_url")
        if not self.api_key and self.auth_header.lower() != "x-api-key" and self.provider_id != "ollama":
            missing.extend(self.spec.env_vars[:1])
        return missing

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        payload = {
            "model": self.model,
            "messages": [message.to_dict() for message in messages],
            "temperature": temperature,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/{self.chat_path.lstrip('/')}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        if self.api_key:
            header_value = f"{self.auth_prefix}{self.api_key}" if self.auth_prefix else self.api_key
            request.add_header(self.auth_header, header_value)
        for key, value in self.headers.items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - exercised in real provider use
            raise ProviderError(f"{self.provider_id} request failed: {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - exercised in real provider use
            raise ProviderError(f"{self.provider_id} request failed: {exc.reason}") from exc

        raw_payload = json.loads(raw_text)
        text = self._extract_text(raw_payload)
        return ProviderResponse(provider_id=self.provider_id, model=self.model, text=text, raw=raw_payload)

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


class NativePlaceholderProvider(BaseProvider):
    def __init__(self, spec: ProviderSpec, *, reason: str) -> None:
        super().__init__(spec)
        self.reason = reason

    def complete(self, messages: Sequence[Message], *, temperature: float = 0.2) -> ProviderResponse:
        raise ProviderError(f"{self.spec.display_name} is scaffolded but not wired yet: {self.reason}")


def built_in_provider_specs() -> tuple[ProviderSpec, ...]:
    return (
        ProviderSpec(
            provider_id="openai",
            display_name="OpenAI",
            adapter_kind="openai_compatible",
            default_model="gpt-4o-mini",
            env_vars=("OPENAI_API_KEY",),
            default_base_url="https://api.openai.com",
            notes="OpenAI chat completions endpoint.",
        ),
        ProviderSpec(
            provider_id="anthropic",
            display_name="Anthropic",
            adapter_kind="native_stub",
            default_model="claude-3-7-sonnet-latest",
            env_vars=("ANTHROPIC_API_KEY",),
            notes="Native adapter planned; scaffolded for configuration and registry work.",
        ),
        ProviderSpec(
            provider_id="google-gemini",
            display_name="Google Gemini",
            adapter_kind="native_stub",
            default_model="gemini-2.0-flash",
            env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            notes="Native adapter planned; scaffolded for configuration and registry work.",
        ),
        ProviderSpec(
            provider_id="azure-openai",
            display_name="Azure OpenAI",
            adapter_kind="openai_compatible",
            default_model="gpt-4o-mini",
            env_vars=("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
            default_base_url="",
            auth_header="api-key",
            auth_prefix="",
            notes="Azure-hosted OpenAI-compatible endpoint.",
        ),
        ProviderSpec(
            provider_id="mistral",
            display_name="Mistral",
            adapter_kind="openai_compatible",
            default_model="mistral-large-latest",
            env_vars=("MISTRAL_API_KEY",),
            default_base_url="https://api.mistral.ai",
            notes="OpenAI-compatible chat endpoint.",
        ),
        ProviderSpec(
            provider_id="cohere",
            display_name="Cohere",
            adapter_kind="native_stub",
            default_model="command-r-plus",
            env_vars=("COHERE_API_KEY",),
            notes="Native adapter planned; scaffolded for configuration and registry work.",
        ),
        ProviderSpec(
            provider_id="xai",
            display_name="xAI",
            adapter_kind="openai_compatible",
            default_model="grok-2-latest",
            env_vars=("XAI_API_KEY",),
            default_base_url="https://api.x.ai",
            notes="OpenAI-compatible chat endpoint.",
        ),
        ProviderSpec(
            provider_id="groq",
            display_name="Groq",
            adapter_kind="openai_compatible",
            default_model="llama-3.3-70b-versatile",
            env_vars=("GROQ_API_KEY",),
            default_base_url="https://api.groq.com/openai",
            notes="OpenAI-compatible chat endpoint.",
        ),
        ProviderSpec(
            provider_id="openrouter",
            display_name="OpenRouter",
            adapter_kind="openai_compatible",
            default_model="openai/gpt-4o-mini",
            env_vars=("OPENROUTER_API_KEY",),
            default_base_url="https://openrouter.ai/api",
            notes="Routing layer for many models through one OpenAI-compatible endpoint.",
        ),
        ProviderSpec(
            provider_id="ollama",
            display_name="Ollama",
            adapter_kind="openai_compatible",
            default_model="llama3.1",
            env_vars=("OLLAMA_HOST",),
            default_base_url="http://127.0.0.1:11434",
            auth_header="",
            auth_prefix="",
            notes="Local model server using the chat/completions compatible endpoint.",
        ),
    )


def custom_provider_spec() -> ProviderSpec:
    return ProviderSpec(
        provider_id=CUSTOM_PROVIDER_ID,
        display_name="Custom Provider",
        adapter_kind="openai_compatible",
        default_model="custom-model",
        notes="User-defined OpenAI-compatible provider configuration.",
    )


def list_provider_specs(include_custom: bool = True) -> list[ProviderSpec]:
    specs = list(built_in_provider_specs())
    if include_custom:
        specs.append(custom_provider_spec())
    return specs


class ProviderRegistry:
    def __init__(self) -> None:
        self._specs = {spec.provider_id: spec for spec in list_provider_specs(include_custom=True)}

    def list_specs(self, *, include_custom: bool = True) -> list[ProviderSpec]:
        specs = list(built_in_provider_specs())
        if include_custom:
            specs.append(custom_provider_spec())
        return specs

    def get_spec(self, provider_id: str) -> ProviderSpec:
        if provider_id == LOCAL_PROVIDER_ID:
            return LocalEchoProvider().spec
        try:
            return self._specs[provider_id]
        except KeyError as exc:
            raise ProviderError(f"unknown provider: {provider_id}") from exc

    def list_statuses(self, config: AppConfig) -> list[ProviderStatus]:
        statuses: list[ProviderStatus] = [
            ProviderStatus(
                provider_id=LOCAL_PROVIDER_ID,
                display_name="Local Echo",
                adapter_kind="local",
                ready=True,
                details="Always ready for offline testing.",
                default_model="nexus-local",
            )
        ]
        for spec in built_in_provider_specs():
            statuses.append(self.describe(spec.provider_id, config))
        statuses.append(self.describe(CUSTOM_PROVIDER_ID, config))
        return statuses

    def describe(self, provider_id: str, config: AppConfig) -> ProviderStatus:
        spec = self.get_spec(provider_id)
        settings = dict(config.provider_settings.get(provider_id, {}))
        if spec.adapter_kind == "openai_compatible":
            base_url = str(settings.get("base_url") or spec.default_base_url)
            api_key_env = str(settings.get("api_key_env") or (spec.env_vars[0] if spec.env_vars else ""))
            has_api_key = bool(settings.get("api_key") or (api_key_env and os.getenv(api_key_env)))
            ready = bool(base_url) and (has_api_key or not spec.env_vars or provider_id == "ollama")
            details = base_url or "no base URL configured"
            if not has_api_key and provider_id != "ollama":
                details = f"missing credentials: {api_key_env or 'api key'}"
            if provider_id == CUSTOM_PROVIDER_ID and not base_url:
                ready = False
                details = "configure base_url and credentials in provider_settings['custom']"
            return ProviderStatus(provider_id=spec.provider_id, display_name=spec.display_name, adapter_kind=spec.adapter_kind, ready=ready, details=details, default_model=str(settings.get("model") or spec.default_model))
        return ProviderStatus(
            provider_id=spec.provider_id,
            display_name=spec.display_name,
            adapter_kind=spec.adapter_kind,
            ready=False,
            details=spec.notes,
            default_model=str(settings.get("model") or spec.default_model),
        )

    def create_provider(self, provider_id: str, config: AppConfig) -> BaseProvider:
        if provider_id == LOCAL_PROVIDER_ID:
            return LocalEchoProvider()

        spec = self.get_spec(provider_id)
        settings = dict(config.provider_settings.get(provider_id, {}))
        if spec.adapter_kind == "openai_compatible":
            base_url = str(settings.get("base_url") or spec.default_base_url)
            if not base_url and provider_id != CUSTOM_PROVIDER_ID:
                raise ProviderError(f"{provider_id} is missing base_url configuration")
            api_key_env = str(settings.get("api_key_env") or (spec.env_vars[0] if spec.env_vars else ""))
            api_key = settings.get("api_key") or (os.getenv(api_key_env) if api_key_env else None)
            headers = dict(settings.get("headers", {}))
            model = str(settings.get("model") or spec.default_model)
            chat_path = str(settings.get("chat_path") or spec.chat_path)
            auth_header = str(settings.get("auth_header") or spec.auth_header)
            auth_prefix = str(settings.get("auth_prefix") if settings.get("auth_prefix") is not None else spec.auth_prefix)
            return OpenAICompatibleProvider(
                spec,
                base_url=base_url,
                model=model,
                api_key=str(api_key) if api_key is not None else None,
                headers=headers,
                chat_path=chat_path,
                auth_header=auth_header,
                auth_prefix=auth_prefix,
            )

        if spec.adapter_kind == "native_stub":
            return NativePlaceholderProvider(spec, reason=spec.notes)

        raise ProviderError(f"no provider factory available for {provider_id}")
