"""Child-process commands for smoke tests (avoids nested uv run locking on Windows)."""

import sys


def python_exe() -> str:
    return sys.executable


def cmd_python_c(script: str) -> list[str]:
    return [python_exe(), "-c", script]


def cmd_uvicorn_server_app(
    host: str, port: int, *, graceful_shutdown_s: int = 5
) -> list[str]:
    return [
        python_exe(),
        "-m",
        "uvicorn",
        "server:app",
        "--host",
        host,
        "--port",
        str(port),
        "--timeout-graceful-shutdown",
        str(graceful_shutdown_s),
    ]


def cmd_fcc_init() -> list[str]:
    return [python_exe(), "-c", "from cli.entrypoints import init; init()"]


def cmd_free_claude_code_serve() -> list[str]:
    return [python_exe(), "-c", "from cli.entrypoints import serve; serve()"]
