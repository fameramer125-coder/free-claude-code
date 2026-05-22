"""
Claude Code Proxy - Entry Point

Builds the ASGI app at import time so uvicorn can load it as ``server:app``:

    uv run uvicorn server:app --host 0.0.0.0 --port 8082 --timeout-graceful-shutdown 5

When run as ``__main__``, host and port come from :class:`config.settings.Settings`.

``create_app`` is re-exported so callers can do ``from server import create_app``
without reaching into ``api.app`` directly.
"""

from api.app import create_app, create_asgi_app

app = create_asgi_app()

__all__ = ["app", "create_app"]

if __name__ == "__main__":
    import uvicorn

    from cli.process_registry import kill_all_best_effort
    from config.settings import get_settings

    settings = get_settings()
    try:
        # timeout_graceful_shutdown ensures uvicorn doesn't hang on task cleanup.
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level="debug",
            timeout_graceful_shutdown=5,
        )
    finally:
        # Safety net: cleanup subprocesses if lifespan shutdown doesn't fully run.
        kill_all_best_effort()
