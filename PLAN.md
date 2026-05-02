# nexus-agi Implementation Plan

## Vision

Build `nexus-agi` as a local-first, personal AGI assistant that can plan, act, observe, and recover across long-running tasks. The product should feel terminal-native and stay focused on CLI workflows.

The system should be provider-agnostic, support ten built-in model providers, and allow users to plug in a custom provider without changing core agent logic.

## Product Goals

- Terminal-first workflow for power users and automation.
- One agent runtime shared across CLI commands.
- Reliable state persistence so runs can resume after interruptions.
- Clear provider abstraction with native support for 10 providers plus custom provider extension.
- Human-in-the-loop controls for approvals, unsafe actions, and recovery.
- Local-first defaults with explicit network and tool permissions.

## Proposed User Experience

### Terminal-Based Experience

The terminal should be the primary command surface for day-to-day usage.

Planned commands:

- `nexus-agi plan` - create or revise a task plan.
- `nexus-agi run` - execute a plan or a user request.
- `nexus-agi status` - inspect current and past runs.
- `nexus-agi resume` - continue an interrupted run.
- `nexus-agi approve` - approve blocked actions.
- `nexus-agi providers` - list and test configured providers.
- `nexus-agi config` - manage models, tools, and runtime settings.

Terminal output should be concise and operational: current step, tool calls, model used, blocking conditions, artifacts produced, and next action.

## System Architecture

### 1. Core Runtime

A single agent runtime should own planning, execution, memory, tool use, and recovery.

Primary responsibilities:

- Maintain the current run state.
- Convert user intent into a structured plan.
- Select a model provider and prompt strategy.
- Execute tool calls.
- Persist checkpoints and artifacts.
- Resume from failures and blocked actions.
- Emit events for CLI consumers.

### 2. Provider Layer

All model access should pass through one provider interface.

The provider contract should standardize:

- Chat/completions request and response handling.
- Streaming token support when available.
- Tool/function calling.
- Structured outputs.
- Model metadata and capability discovery.
- Retry, timeout, and rate-limit policies.

### 3. Tool Layer

Tools should be explicitly registered and permissioned.

Initial tool groups:

- File and workspace operations.
- Shell and command execution.
- Web fetch and browser automation.
- Memory read/write.
- Task and run lifecycle operations.
- Approvals for sensitive actions.

### 4. Persistence Layer

Persist state locally so work is recoverable.

Persist:

- Runs and steps.
- Provider configuration.
- Tool call traces.
- User approvals.
- Artifacts and generated outputs.
- Memory snapshots and summaries.

Recommended local storage layout:

- `.nexus-agi/state.json` for current runtime state.
- `.nexus-agi/runs/` for run history.
- `.nexus-agi/artifacts/` for generated files and logs.
- `.nexus-agi/config.json` for app settings.

### 5. API Layer

Expose a local API used by the CLI.

The API should support:

- Starting and stopping runs.
- Streaming run events.
- Reading run and artifact state.
- Updating settings.
- Managing approvals.
- Listing providers and models.

## Model Provider Strategy

### Built-In Providers

Ship 10 built-in providers in the first supported release.

Recommended built-in set:

1. OpenAI
2. Anthropic
3. Google Gemini
4. Azure OpenAI
5. Mistral
6. Cohere
7. xAI
8. Groq
9. OpenRouter
10. Ollama

### Provider Design Requirements

Each built-in provider should support, where available:

- Chat and instruction following.
- Streaming responses.
- Tool calling.
- Model listing or model alias mapping.
- Per-provider authentication.
- Per-provider timeout and retry policies.

Provider capability differences should be normalized at the adapter layer so the agent core does not branch on provider-specific behavior.

### Custom Model Provider

Add a custom provider mechanism that allows users to define a model backend without modifying core code.

Custom provider options should include:

- OpenAI-compatible HTTP endpoint.
- Header-based authentication.
- Custom request/response mapping.
- Custom streaming transport.
- Model aliasing and capability declaration.

The custom provider should be validated at startup with a connection test and a capability probe.

## Execution Phases

### Phase 0: Repository Foundation

Deliverables:

- Project layout and packaging.
- Core docs and configuration files.
- Local storage conventions.
- Test harness and CI entry points.

Exit criteria:

- The project can be installed and started locally.
- A basic command-line entrypoint exists.
- State can be persisted and read back.

### Phase 1: Terminal Core

Deliverables:

- CLI command structure.
- Run lifecycle management.
- Plan creation and execution flow.
- Status and resume commands.
- Approval gating for risky actions.

Exit criteria:

- A user can start a run from the terminal.
- A run can pause, persist, and resume.
- Basic status reporting works end to end.

### Phase 2: Agent Runtime

Deliverables:

- Single orchestrator loop.
- Step planning and step execution.
- Tool invocation and observation capture.
- Retry and failure recovery.
- Artifact creation and persistence.

Exit criteria:

- The runtime can execute a multi-step task with checkpoints.
- Failures are recoverable without losing prior state.

### Phase 3: Provider Abstraction

Deliverables:

- Shared provider interface.
- Common request/response envelope.
- Streaming adapter.
- Structured output handling.
- Capability metadata.

Exit criteria:

- Core runtime can switch providers without changing agent logic.
- Provider-specific code is isolated to adapter modules.

### Phase 4: 10 Built-In Providers

Deliverables:

- Implement the ten built-in providers.
- Add configuration and credentials handling.
- Add provider validation and health checks.
- Add provider selection and fallback behavior.

Exit criteria:

- Every built-in provider can be configured and tested locally.
- At least one provider can run the full agent loop end to end.

### Phase 5: Custom Provider

Deliverables:

- User-defined provider configuration.
- OpenAI-compatible endpoint support.
- Optional custom HTTP transport and headers.
- Validation and diagnostics for bad configs.

Exit criteria:

- A user can point the agent at an unsupported backend and use it without changing core code.

### Phase 7: Safety, Memory, and Recovery

Deliverables:

- Approval system for sensitive actions.
- Memory summarization and retrieval.
- Workspace and shell safety controls.
- Checkpointing after each completed step.
- Recovery after interruption, crash, or failed tool call.

Exit criteria:

- Unsafe actions require explicit confirmation.
- Interrupted runs continue from the latest checkpoint.

### Phase 8: Quality, Observability, and Packaging

Deliverables:

- Unit and integration tests.
- Provider contract tests.
- CLI smoke tests.
- Structured logs and trace IDs.
- Release packaging and upgrade path.

Exit criteria:

- Core workflows are covered by automated tests.
- The app is installable and usable without repo knowledge.

## Recommended Milestones

### Milestone 1: Terminal MVP

Scope:

- CLI entrypoint.
- Basic plan/run/status commands.
- Local persistence.
- One working provider.

Outcome:

- A usable terminal agent with persistent state.

### Milestone 2: Provider Expansion

Scope:

- Provider abstraction.
- 10 built-in providers.
- Custom provider support.

Outcome:

- Broad model compatibility with a consistent agent core.

### Milestone 3: Terminal Release

Scope:

- CLI command coverage.
- Local API.
- Run and approval management.

Outcome:

- The agent is fully usable from the terminal without browser dependencies.

### Milestone 4: Hardening Release

Scope:

- Recovery polish.
- Test coverage.
- Logging and diagnostics.
- Packaging and docs.

Outcome:

- Stable personal AGI foundation for everyday use.

## Non-Goals For the First Release

- Multi-user SaaS hosting.
- Distributed execution across remote workers.
- Unrestricted autonomous shell access by default.
- Heavy plugin marketplace complexity.
- Provider-specific logic leaking into the core agent loop.

## Implementation Principles

- Keep the agent local-first and deterministic where possible.
- Treat the CLI and local services as views over one runtime.
- Isolate provider differences behind adapters.
- Persist early and often.
- Prefer explicit approvals over implicit side effects.
- Make failures resumable, not fatal.
- Keep the system inspectable through logs, state, and artifacts.

## Definition Of Done

The initial `nexus-agi` implementation is complete when:

- The terminal workflow can create, run, pause, approve, and resume tasks.
- The terminal workflow can inspect and control active runs.
- Ten built-in providers are available and documented.
- A custom provider can be configured without code changes.
- State persists locally and survives restarts.
- Core flows are covered by automated tests.
- The architecture remains simple enough to extend with more tools and models later.
