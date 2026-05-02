from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .agent import main as agent_main
from .dashboard import run_dashboard


_FLAGS_WITH_VALUES = {"--workspace", "--data-dir", "--provider", "--host", "--port"}
_BOOLEAN_FLAGS = {"--json", "--open-browser"}


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root used for .nexus-agi state.")
    shared.add_argument("--data-dir", default=".nexus-agi", help="Directory that stores local runtime state.")
    shared.add_argument("--provider", default=None, help="Provider id to use for this command.")
    shared.add_argument("--json", action="store_true", help="Emit JSON output.")

    parser = argparse.ArgumentParser(prog="nexus-agi", description="Local-first personal AGI assistant", parents=[shared])
    subparsers = parser.add_subparsers(dest="command", required=True)

    web = subparsers.add_parser("web", parents=[shared], help="Start the local web dashboard.")
    web.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    web.add_argument("--port", type=int, default=8787, help="Port to listen on.")
    web.add_argument("--open-browser", action="store_true", help="Open the dashboard in a browser.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if _first_command(args) != "web":
        return agent_main(args)

    parser = build_parser()
    parsed = parser.parse_args(args)
    return run_dashboard(
        parsed.workspace,
        data_dir_name=parsed.data_dir,
        host=parsed.host,
        port=parsed.port,
        provider_id=parsed.provider,
        open_browser=parsed.open_browser,
    )


def _first_command(argv: Sequence[str]) -> str | None:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("-"):
            if token in _FLAGS_WITH_VALUES and index + 1 < len(argv):
                index += 2
            else:
                index += 1
            continue
        if token in _BOOLEAN_FLAGS:
            index += 1
            continue
        return token
    return None
