import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings
from app.contracts import BotInfo, ConnectionState
from app.errors import AppError
from app.main import create_app
from app.runtime_lock import RuntimeLock


class LocalGateway:
    instances = []

    def __init__(self, token):
        self.token = token
        self.closed = 0
        self.instances.append(self)

    async def get_me(self):
        if self.token == "bad":
            raise AppError("INVALID_BOT_TOKEN", "Неверный токен бота.", 401)
        return BotInfo(id="12345", username="local_test_bot", first_name="Test")

    async def webhook_url(self):
        return ""

    async def close(self):
        self.closed += 1


class LocalRuntime:
    instances = []

    def __init__(self, store, gateway, bot):
        self.store, self.gateway, self.bot = store, gateway, bot
        self.connection = ConnectionState()
        self.started = self.stopped = 0
        self.instances.append(self)

    async def start(self):
        await self.store.recover_sending(self.bot.id)
        self.started += 1
        self.connection = ConnectionState(status="connected")

    async def stop(self):
        self.stopped += 1
        self.connection = ConnectionState(status="disconnected")
        await self.gateway.close()


def make_app(tmp_path):
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text(
        "<!doctype html><title>Chat Desk</title>", encoding="utf-8"
    )
    return create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'db' / 'chat.db').as_posix()}",
            frontend_dir=site,
        ),
        gateway_factory=LocalGateway,
        runtime_factory=LocalRuntime,
    )


def test_single_process_lock_is_released(tmp_path):
    one, two = (
        RuntimeLock(tmp_path / "runtime.lock"),
        RuntimeLock(tmp_path / "runtime.lock"),
    )
    one.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another Chat Desk"):
            two.acquire()
    finally:
        one.release()
    two.acquire()
    two.release()
    two.release()


def test_composition_start_stop_reconnect_and_safe_boundaries(tmp_path):
    LocalGateway.instances.clear()
    LocalRuntime.instances.clear()
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 200
        assert client.get("/assets/not-found.js").status_code == 404
        assert client.get("/api/not-found").json()["error"]["code"] == "NOT_FOUND"
        assert client.get("/api/bootstrap").status_code == 401
        denied = client.post(
            "/api/session",
            json={"bot_token": "safe-placeholder"},
            headers={"Origin": "https://untrusted.invalid"},
        )
        assert denied.status_code == 403
        assert len(LocalGateway.instances) == 0
        marker = "token-must-not-be-echoed-" * 20
        invalid = client.post("/api/session", json={"bot_token": marker})
        assert invalid.status_code == 422 and marker not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
        assert client.post("/api/session", json={"bot_token": "bad"}).status_code == 401
        logged_in = client.post("/api/session", json={"bot_token": "safe-placeholder"})
        assert logged_in.status_code == 200
        assert "HttpOnly" in logged_in.headers["set-cookie"]
        assert "SameSite=strict" in logged_in.headers["set-cookie"]
        assert "safe-placeholder" not in logged_in.text
        snapshot = client.get("/api/bootstrap").json()
        assert snapshot["chats"] == [] and snapshot["cursor"] == 0
        assert (
            client.post(
                "/api/session", json={"bot_token": "safe-placeholder"}
            ).status_code
            == 200
        )
        assert len(LocalRuntime.instances) == 1
        assert client.delete("/api/session").status_code == 204
        assert LocalRuntime.instances[0].stopped == 0
        assert client.get("/api/session").status_code == 401
        client.post("/api/session", json={"bot_token": "safe-placeholder"})
    assert LocalRuntime.instances[0].started == 1
    assert LocalRuntime.instances[0].stopped == 1
    assert all(gateway.closed == 1 for gateway in LocalGateway.instances)

    with TestClient(make_app(tmp_path)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/session").status_code == 401
    assert (tmp_path / "db" / "chat.db").is_file()


def test_configuration_uses_repository_root():
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert Settings().frontend_dir == PROJECT_ROOT / "frontend" / "dist"
