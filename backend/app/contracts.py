from typing import Literal

from pydantic import BaseModel, Field


class BotInfo(BaseModel):
    id: str
    username: str
    first_name: str


class ConnectionState(BaseModel):
    status: Literal[
        "disconnected", "connecting", "connected", "reconnecting", "error"
    ] = "disconnected"
    error_code: str | None = None
    message: str | None = None


class SessionResponse(BaseModel):
    bot: BotInfo
    connection: ConnectionState


class Contact(BaseModel):
    telegram_chat_id: str
    name: str
    username: str | None = None
    available: bool = True


class Chat(BaseModel):
    id: int
    telegram_chat_id: str
    name: str
    username: str | None = None
    available: bool = True
    last_message: str | None = None
    updated_at: str


MessageStatus = Literal["queued", "sending", "sent", "failed", "unknown", "received"]


class Message(BaseModel):
    id: int
    chat_id: int
    telegram_chat_id: str
    direction: Literal["incoming", "outgoing"]
    text: str
    source: Literal["user", "system"] = "user"
    created_at: str
    telegram_message_id: str | None = None
    client_message_id: str | None = None
    status: MessageStatus
    error_code: str | None = None
    error_message: str | None = None


class MessagesPage(BaseModel):
    items: list[Message]
    next_before_id: int | None = None


class ContactsResponse(BaseModel):
    items: list[Contact]


class ChangeEvent(BaseModel):
    id: int
    type: Literal["contact.upsert", "chat.upsert", "message.upsert"]
    data: dict


class BootstrapResponse(BaseModel):
    bot: BotInfo
    connection: ConnectionState
    chats: list[Chat]
    cursor: int


class ChangesResponse(BaseModel):
    items: list[ChangeEvent]
    cursor: int
    has_more: bool = False
    connection: ConnectionState


class ConnectRequest(BaseModel):
    bot_token: str = Field(min_length=1, max_length=200, repr=False)


class CreateChatRequest(BaseModel):
    telegram_chat_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    client_message_id: str = Field(min_length=1, max_length=100)


class ErrorDetail(BaseModel):
    code: str
    message: str
    retry_after: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str
