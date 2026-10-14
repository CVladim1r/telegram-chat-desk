from datetime import timezone

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Chat, ChatMemberUpdated, Message


def contact(chat: Chat, *, available: bool = True) -> dict:
    name = " ".join(part for part in (chat.first_name, chat.last_name) if part)
    return {
        "telegram_chat_id": str(chat.id),
        "name": name or chat.username or str(chat.id),
        "username": chat.username,
        "available": available,
    }


def text_action(message: Message, reply: str | None = None) -> dict:
    action = {
        "contact": contact(message.chat),
        "message": {
            "telegram_message_id": str(message.message_id),
            "text": message.text,
            "created_at": message.date.astimezone(timezone.utc).isoformat(),
        },
    }
    if reply:
        action["system_reply"] = reply
    return action


def create_dispatcher() -> Dispatcher:
    router = Router(name="private_text_chat")
    router.message.filter(F.chat.type == "private")
    router.my_chat_member.filter(F.chat.type == "private")

    @router.message(CommandStart())
    async def start(message: Message) -> dict:
        return text_action(
            message,
            "Вы подключены к чату. Напишите текстовое сообщение, и оператор увидит его.",
        )

    @router.message(Command("help"))
    async def help_message(message: Message) -> dict:
        return text_action(
            message,
            "Отправьте текстовое сообщение в этот чат. Оператор сможет ответить вам здесь. Поддерживаются только текстовые сообщения.",
        )

    @router.message(F.text)
    async def text_message(message: Message) -> dict:
        return text_action(message)

    @router.my_chat_member()
    async def membership(event: ChatMemberUpdated) -> dict:
        return {
            "contact": contact(
                event.chat,
                available=event.new_chat_member.status not in {"kicked", "left"},
            )
        }

    dispatcher = Dispatcher(disable_fsm=True)
    dispatcher.include_router(router)
    return dispatcher
