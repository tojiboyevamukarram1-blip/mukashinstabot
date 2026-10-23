import logging
from typing import Any, Awaitable, Callable, Dict, List
from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from config import CHANNELS

logger = logging.getLogger(__name__)


async def is_user_subscribed(bot: Bot, user_id: int, channel: str) -> bool:
    """Foydalanuvchi kanalga a'zo ekanligini tekshiradi."""
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status in [
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED,
        ]
    except Exception as e:
        logger.error(f"Kanal obunasini tekshirishda xatolik ({channel}): {e}")
        # Agar bot kanalga admin qilinmagan bo'lsa yoki xato yuz bersa,
        # bot butunlay to'xtab qolmasligi uchun xatolikni log qilib False qaytaramiz
        return False


async def get_unsubscribed_channels(bot: Bot, user_id: int) -> List[Dict[str, str]]:
    """Foydalanuvchi hali a'zo bo'lmagan kanallar ro'yxatini qaytaradi."""
    unsubscribed = []
    for channel in CHANNELS:
        subscribed = await is_user_subscribed(bot, user_id, channel)
        if not subscribed:
            title = channel
            url = f"https://t.me/{channel.lstrip('@')}"
            try:
                chat = await bot.get_chat(channel)
                if chat.title:
                    title = chat.title
                if chat.username:
                    url = f"https://t.me/{chat.username}"
                elif chat.invite_link:
                    url = chat.invite_link
            except Exception as e:
                logger.warning(f"Kanal ma'lumotini olishda ogohlantirish ({channel}): {e}")

            unsubscribed.append({
                "id": channel,
                "title": title,
                "url": url
            })
    return unsubscribed


def build_subscription_keyboard(channels: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    """A'zo bo'lish va tekshirish tugmalarini yasash."""
    inline_keyboard = []
    for ch in channels:
        inline_keyboard.append([
            InlineKeyboardButton(
                text=f"📢 {ch['title']} ga a'zo bo'lish",
                url=ch["url"]
            )
        ])
    
    inline_keyboard.append([
        InlineKeyboardButton(
            text="🔄 Obunani tekshirish",
            callback_data="check_subscription"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


class SubscriptionMiddleware(BaseMiddleware):
    """
    Barcha xabarlarni tutib, kanalga a'zo bo'lganligini tekshiruvchi Middleware.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        bot: Bot = data.get("bot")

        # Agar majburiy kanallar belgilanmagan bo'lsa, tekshirmay o'tkazish
        if not CHANNELS or not bot:
            return await handler(event, data)

        # Agar bu callback_query bo'lsa va 'check_subscription' bo'lsa, uni o'tkazamiz
        if isinstance(event, CallbackQuery):
            if event.data == "check_subscription":
                return await handler(event, data)
            user_id = event.from_user.id
        elif isinstance(event, Message):
            user_id = event.from_user.id
        else:
            return await handler(event, data)

        # Obunani tekshirish
        unsubscribed = await get_unsubscribed_channels(bot, user_id)

        if unsubscribed:
            keyboard = build_subscription_keyboard(unsubscribed)
            warning_text = (
                "⚠️ <b>Botdan foydalanish uchun quyidagi kanalimizga a'zo bo'lishingiz shart!</b>\n\n"
                "Iltimos, kanalga a'zo bo'ling va pastdagi <b>«🔄 Obunani tekshirish»</b> tugmasini bosing:"
            )

            if isinstance(event, Message):
                await event.answer(warning_text, reply_markup=keyboard, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("Siz hali kanalga a'zo bo'lmadingiz!", show_alert=True)
                try:
                    await event.message.edit_text(warning_text, reply_markup=keyboard, parse_mode="HTML")
                except Exception:
                    pass
            return  # Qolgan handlerlar ishga tushmasligi uchun to'xtatish

        return await handler(event, data)
