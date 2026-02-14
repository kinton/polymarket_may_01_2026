"""Tests for the health check HTTP endpoint."""


import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.healthcheck import HealthCheckServer


@pytest.fixture
def server() -> HealthCheckServer:
    return HealthCheckServer(host="127.0.0.1", port=0)


@pytest.fixture
async def client(server: HealthCheckServer) -> TestClient:
    app = server._build_app()
    srv = TestServer(app)
    cli = TestClient(srv)
    await cli.start_server()
    yield cli
    await cli.close()


# ---------- /health ----------


async def test_health_returns_200(client: TestClient) -> None:
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert "status" in data
    assert "uptime_s" in data


async def test_health_default_status_starting(server: HealthCheckServer, client: TestClient) -> None:
    resp = await client.get("/health")
    data = await resp.json()
    assert data["status"] == "starting"


async def test_health_reflects_set_status(server: HealthCheckServer, client: TestClient) -> None:
    server.set_status("running")
    resp = await client.get("/health")
    data = await resp.json()
    assert data["status"] == "running"


async def test_health_active_traders(server: HealthCheckServer, client: TestClient) -> None:
    server.set_active_traders(3)
    resp = await client.get("/health")
    data = await resp.json()
    assert data["active_traders"] == 3


async def test_health_poll_count(server: HealthCheckServer, client: TestClient) -> None:
    server.record_poll()
    server.record_poll()
    resp = await client.get("/health")
    data = await resp.json()
    assert data["total_polls"] == 2
    assert data["last_poll_ts"] is not None


async def test_health_extra_fields(server: HealthCheckServer, client: TestClient) -> None:
    server.set_extra("dry_run", True)
    server.set_extra("trade_size", 1.1)
    resp = await client.get("/health")
    data = await resp.json()
    assert data["dry_run"] is True
    assert data["trade_size"] == 1.1


async def test_health_uptime_positive(server: HealthCheckServer, client: TestClient) -> None:
    resp = await client.get("/health")
    data = await resp.json()
    assert data["uptime_s"] >= 0


# ---------- /ready ----------


async def test_ready_503_when_starting(server: HealthCheckServer, client: TestClient) -> None:
    resp = await client.get("/ready")
    assert resp.status == 503
    data = await resp.json()
    assert data["ready"] is False


async def test_ready_200_when_running(server: HealthCheckServer, client: TestClient) -> None:
    server.set_status("running")
    resp = await client.get("/ready")
    assert resp.status == 200
    data = await resp.json()
    assert data["ready"] is True


async def test_ready_503_when_stopped(server: HealthCheckServer, client: TestClient) -> None:
    server.set_status("stopped")
    resp = await client.get("/ready")
    assert resp.status == 503


# ---------- Lifecycle ----------


async def test_start_and_stop() -> None:
    server = HealthCheckServer(host="127.0.0.1", port=0)
    # start uses a real port; just test no exceptions
    await server.start()
    await server.stop()


async def test_stop_without_start() -> None:
    """Stopping a server that was never started should not raise."""
    server = HealthCheckServer()
    await server.stop()  # _runner is None — should be a no-op


# ---------- Env config ----------


def test_default_host_and_port() -> None:
    server = HealthCheckServer()
    assert server.host == "127.0.0.1"
    assert server.port == 8080


def test_custom_host_and_port() -> None:
    server = HealthCheckServer(host="0.0.0.0", port=9090)
    assert server.host == "0.0.0.0"
    assert server.port == 9090
