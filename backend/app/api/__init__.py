from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.contracts import (
    BootstrapResponse,
    ChangesResponse,
    Chat,
    ConnectRequest,
    ContactsResponse,
    CreateChatRequest,
    Message,
    MessagesPage,
    SendMessageRequest,
    SessionResponse,
)
from app.errors import AppError

COOKIE = "chat_session"


async def session(request: Request):
    return await request.app.state.sessions.require(request.cookies.get(COOKIE))


Session = Annotated[SessionResponse, Depends(session)]


def create_router():
    router = APIRouter(prefix="/api")

    @router.post("/session", response_model=SessionResponse)
    async def connect(body: ConnectRequest, request: Request, response: Response):
        ident, result = await request.app.state.sessions.connect(body.bot_token)
        settings = request.app.state.settings
        response.set_cookie(
            COOKIE,
            ident,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/",
        )
        return result

    @router.get("/session", response_model=SessionResponse)
    async def get_session(current: Session):
        return current

    @router.delete("/session", status_code=204)
    async def logout(request: Request):
        await request.app.state.sessions.logout(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE,
            path="/",
            secure=request.app.state.settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.post("/bot/disconnect", status_code=204)
    async def disconnect(request: Request):
        await request.app.state.sessions.disconnect(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE,
            path="/",
            secure=request.app.state.settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("/bootstrap", response_model=BootstrapResponse)
    async def bootstrap(request: Request, current: Session):
        chats, cursor = await request.app.state.store.bootstrap(current.bot.id)
        return BootstrapResponse(
            bot=current.bot, connection=current.connection, chats=chats, cursor=cursor
        )

    @router.get("/contacts", response_model=ContactsResponse)
    async def list_contacts(request: Request, current: Session, q: str = ""):
        return ContactsResponse(
            items=await request.app.state.store.list_contacts(current.bot.id, q)
        )

    @router.post("/chats", response_model=Chat)
    async def create_chat(body: CreateChatRequest, request: Request, current: Session):
        return await request.app.state.store.create_chat(
            current.bot.id, body.telegram_chat_id
        )

    @router.get("/chats/{chat_id}/messages", response_model=MessagesPage)
    async def list_messages(
        chat_id: int,
        request: Request,
        current: Session,
        before_id: Annotated[int | None, Query(ge=1)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        return await request.app.state.store.list_messages(
            current.bot.id, chat_id, before_id, limit
        )

    @router.post("/chats/{chat_id}/messages", response_model=Message, status_code=202)
    async def send_message(
        chat_id: int, body: SendMessageRequest, request: Request, current: Session
    ):
        if body.client_message_id.startswith("system:"):
            raise AppError(
                "VALIDATION_ERROR", "Используйте новый идентификатор сообщения.", 422
            )
        return await request.app.state.store.enqueue_message(
            current.bot.id, chat_id, body.text, body.client_message_id
        )

    @router.get("/changes", response_model=ChangesResponse)
    async def get_changes(
        request: Request,
        current: Session,
        cursor: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        items, next_cursor, has_more = await request.app.state.store.get_changes(
            current.bot.id, cursor, limit
        )
        return ChangesResponse(
            items=items,
            cursor=next_cursor,
            has_more=has_more,
            connection=current.connection,
        )

    return router
