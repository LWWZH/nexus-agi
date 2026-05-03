---
description: "Workspace instructions for working on nexus-agi"
---

# Nexus AGI Instructions

- Treat this as a small, stdlib-first Python package. Avoid adding third-party dependencies unless there is a clear payoff.
- The main runtime surface is [nexus_agi/agent.py](nexus_agi/agent.py); the CLI entrypoint is `python -m nexus_agi` and `nexus-agi`.
- The local dashboard lives in [nexus_agi/dashboard.py](nexus_agi/dashboard.py); keep it wired to the shared runtime/store surface instead of duplicating core logic.
- Local state is persisted under `.nexus-agi/`, with `state.json`, `config.json`, `runs/`, and `artifacts/` as the important paths.
- Prefer the existing dataclass/enum style and keep provider, planning, and persistence changes small and explicit.
- Tests use `unittest`; run `python -m unittest discover -s tests -p "test_*.py"` for validation.
- For roadmap or larger design context, link to [PLAN.md](PLAN.md) instead of repeating it here.

## When Changing Code

- Keep CLI, dashboard, and storage changes compatible with the existing `nexus_agi.agent` runtime facade.
- If you touch provider adapters, verify request shapes and response parsing with focused tests in [tests/test_core.py](tests/test_core.py).
- If you touch dashboard behavior, validate the rendered state and HTML paths covered by [tests/test_dashboard.py](tests/test_dashboard.py).
