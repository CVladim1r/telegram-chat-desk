from datetime import datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.contracts import BotInfo, ConnectionState
from app.errors import AppError
from app.main import create_app


class Gateway:
    def __init__(self, token):
        self.token = token

    async def get_me(self):
        if self.token == "bad":
            raise AppError("INVALID_BOT_TOKEN", "Неверный токен.", 401)
        return BotInfo(id="1", username="test_bot", first_name="Test")

    async def webhook_url(self):
        return ""

    async def close(self):
        pass


class Runtime:
    def __init__(self, store, gateway, bot):
        self.gateway = gateway
        self.connection = ConnectionState(status="connected")

    async def start(self):
        pass

    async def stop(self):
        await self.gateway.close()


@pytest.fixture
async def api(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}"
    )
    app = create_app(settings, gateway_factory=Gateway, runtime_factory=Runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client:
            yield app, client


async def test_auth_and_cookie_no_secret_in_responses(api):
    app, client = api
    assert (await client.get("/api/bootstrap")).status_code == 401
    token = "private-valid-bot-token"
    result = await client.post("/api/session", json={"bot_token": token})
    assert result.status_code == 200 and token not in result.text
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=strict" in result.headers["set-cookie"]
    assert (await client.get("/api/session")).json()["bot"]["id"] == "1"
    assert (
        await client.post("/api/session", json={"bot_token": "bad"})
    ).status_code == 401
    assert (await client.get("/api/session")).status_code == 200
    assert (await client.delete("/api/session")).status_code == 204
    assert (await client.get("/api/session")).status_code == 401
    assert app.state.sessions.runtime is not None


async def test_authenticated_chat_flow_idempotency_and_cursor(api):
    app, client = api
    await client.post("/api/session", json={"bot_token": "valid"})
    store = app.state.store
    await store.persist_updates("1", [{"update_id": 1}])
    await store.apply_update(
        "1",
        1,
        {
            "contact": {"telegram_chat_id": "42", "name": "Иван"},
            "message": {
                "telegram_message_id": "55",
                "text": "Привет",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
    response = await client.get("/api/contacts?q=ИВАН")
    assert response.json()["items"][0]["telegram_chat_id"] == "42"
    response = await client.post("/api/chats", json={"telegram_chat_id": "42"})
    assert response.status_code == 200
    chat_id = response.json()["id"]
    bootstrap = (await client.get("/api/bootstrap")).json()
    payload = {"text": "Привет <b>text</b> 😀", "client_message_id": "message-1"}
    first = await client.post(f"/api/chats/{chat_id}/messages", json=payload)
    replay = await client.post(f"/api/chats/{chat_id}/messages", json=payload)
    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    changed = (await client.get(f"/api/changes?cursor={bootstrap['cursor']}")).json()
    assert {event["type"] for event in changed["items"]} == {
        "message.upsert",
        "chat.upsert",
    }
    assert changed["connection"]["status"] == "connected"
    empty = (await client.get(f"/api/changes?cursor={changed['cursor']}")).json()
    assert empty["cursor"] == changed["cursor"] and empty["items"] == []
    page = (await client.get(f"/api/chats/{chat_id}/messages?limit=1")).json()
    assert page["items"][0]["text"] == payload["text"]
    older = (
        await client.get(
            f"/api/chats/{chat_id}/messages?before_id={page['next_before_id']}"
        )
    ).json()
    assert older["items"][0]["text"] == "Привет"
    assert (await client.post("/api/bot/disconnect")).status_code == 204
    assert (await client.get("/api/contacts")).status_code == 401


async def test_validation_safe_and_origin_rejected(api):
    _, client = api
    secret = "SECRET-TOKEN-" * 100
    response = await client.post("/api/session", json={"bot_token": secret})
    assert response.status_code == 422 and secret not in response.text
    assert set(response.json()) == {"error", "request_id"}
    response = await client.post(
        "/api/session",
        headers={"origin": "https://evil.example"},
        json={"bot_token": "valid"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_FORBIDDEN"
    await client.post("/api/session", json={"bot_token": "valid"})
    assert (await client.get("/api/changes?cursor=-1")).status_code == 422
    assert (
        await client.post("/api/chats", json={"telegram_chat_id": "+79991234567"})
    ).status_code == 422
    response = await client.post("/api/chats", json={"telegram_chat_id": "42"})
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "RECIPIENT_NOT_REGISTERED"
    )
    response = await client.post(
        "/api/chats/1/messages", json={"text": "hi", "client_message_id": "system:42"}
    )
    assert response.status_code == 422
