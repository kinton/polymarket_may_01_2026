"""
Health check HTTP endpoint for the trading bot.

Exposes a lightweight HTTP server on a configurable port so external
monitoring tools (Docker, systemd, Prometheus, uptimerobot, etc.) can
verify that the bot process is alive and functioning.

Endpoints:
    GET /health  — 200 OK with JSON status payload
    GET /ready   — 200 if bot is actively polling, 503 otherwise

Environment variables:
    HEALTH_PORT  — port to listen on (default 8080)
    HEALTH_HOST  — bind address (default 127.0.0.1)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from aiohttp import web

from src.metrics import MetricsCollector

logger = logging.getLogger("healthcheck")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


class HealthCheckServer:
    """Async HTTP health-check server backed by aiohttp."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.host = host if host is not None else os.getenv("HEALTH_HOST", DEFAULT_HOST)
        self.port = port if port is not None else int(os.getenv("HEALTH_PORT", str(DEFAULT_PORT)))

        # Mutable status — updated by the bot runner
        self._started_at: float = time.time()
        self._status: str = "starting"
        self._active_traders: int = 0
        self._total_polls: int = 0
        self._last_poll_ts: Optional[float] = None
        self._extra: Dict[str, Any] = {}

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    # ------------------------------------------------------------------
    # Status setters (called by TradingBotRunner)
    # ------------------------------------------------------------------

    def set_status(self, status: str) -> None:
        self._status = status

    def set_active_traders(self, count: int) -> None:
        self._active_traders = count

    def record_poll(self) -> None:
        self._total_polls += 1
        self._last_poll_ts = time.time()

    def set_extra(self, key: str, value: Any) -> None:
        self._extra[key] = value

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_health(self, _request: web.Request) -> web.Response:
        """Always returns 200 — process is alive."""
        payload = {
            "status": self._status,
            "uptime_s": round(time.time() - self._started_at, 1),
            "active_traders": self._active_traders,
            "total_polls": self._total_polls,
            "last_poll_ts": self._last_poll_ts,
            **self._extra,
        }
        return web.json_response(payload)

    async def _handle_ready(self, _request: web.Request) -> web.Response:
        """Returns 200 only when bot is actively polling."""
        if self._status == "running":
            return web.json_response({"ready": True})
        return web.json_response({"ready": False, "status": self._status}, status=503)

    async def _handle_metrics_json(self, _request: web.Request) -> web.Response:
        """Return all metrics as JSON."""
        return web.json_response(MetricsCollector.get().snapshot())

    async def _handle_metrics_prom(self, _request: web.Request) -> web.Response:
        """Return metrics in Prometheus text exposition format."""
        text = MetricsCollector.get().prometheus_text()
        return web.Response(text=text, content_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/ready", self._handle_ready)
        app.router.add_get("/metrics", self._handle_metrics_json)
        app.router.add_get("/metrics/prometheus", self._handle_metrics_prom)
        return app

    async def start(self) -> None:
        """Start the HTTP server (non-blocking)."""
        self._app = self._build_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("Health check server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        """Gracefully shut down the HTTP server."""
        if self._runner is not None:
            await self._runner.cleanup()
            logger.info("Health check server stopped")
