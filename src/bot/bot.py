from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ButtonStyle, ChatMemberStatus, ParseMode
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    User,
)
from dotenv import load_dotenv

SRC_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from database.database import (
    close_db,
    confirm_pending_referrer,
    create_trial_subscription,
    deactivate_subscription,
    get_balance,
    get_or_create_user,
    get_referral_balance,
    get_referrals_count,
    get_referrals_page,
    get_subscription_status,
    init_db,
    subscription_purge_date,
    try_set_pending_referrer,
)

load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")

CB_CHECK_SUB = "check_sub"
CB_TRY_FREE = "try_free"
CB_BUY_SUB = "buy_sub"
CB_MANAGE_SUB = "manage_sub"
CB_BACK_MAIN = "back_main"
CB_REFERRAL = "referral"
CB_REF_LIST = "ref_list"
REFERRALS_PER_PAGE = 15
CB_INSTRUCTION = "instruction"
CB_EXTEND = "extend"
CB_BUY_TRAFFIC = "buy_traffic"
CB_REISSUE = "reissue"
CB_DELETE = "delete_sub"
CB_DELETE_CONFIRM = "delete_confirm"
CB_DELETE_FINAL = "delete_final"
CB_DELETE_CANCEL = "delete_cancel"

DEVICE_ANDROID = "android"
DEVICE_IOS = "ios"
DEVICE_WINDOWS = "windows"
DEVICE_MACOS = "macos"
DEVICE_LINUX = "linux"
DEVICE_ANDROID_TV = "androidtv"

FLOW_TRIAL = "trial"
FLOW_INSTR = "instr"

EID_STOP = "5260293700088511294"
EID_SUBSCRIBE = "5222291850928362273"
EID_CHECK_OK = "5222331334562715830"
EID_HELLO = "5325547803936572038"
EID_BALANCE = "5287231198098117669"
EID_TRIAL = "5199552030615558774"
EID_NO_SUB = "5210952531676504517"
EID_EXPIRED = "5449449325434266744"
EID_TRY_FREE = "5222394887193790586"
EID_QUESTION = "5436113877181941026"
EID_MANAGE = "5391059537102927631"
EID_REFERRAL = "6033108709213736873"
EID_INVITE = "5443038326535759644"
EID_NEWS = "6021418126061605425"
EID_SUPPORT = "6034831751308644168"
EID_MANAGE_HEADER = "5447410659077661506"
EID_INSTRUCTION = "5222047175231445480"
EID_EXTEND = "5397916757333654639"
EID_BUY_TRAFFIC = "5312361253610475399"
EID_REISSUE = "5433878454078556670"
EID_DELETE = "5210952531676504517"
EID_DEV_ANDROID = "5931415565955503486"
EID_DEV_IOS = "5775870512127283512"
EID_DEV_WINDOWS = "5818956713507689486"
EID_DEV_MACOS = "5884366771913233289"
EID_DEV_LINUX = "5987565374223159187"
EID_DEV_ANDROID_TV = "6044356915029348425"
EID_STEP_APP = "5222150391885500492"
EID_INSTALL = "6039802767931871481"
EID_SUCCESS = "5206607081334906820"

DEVICES: dict[str, dict[str, str]] = {
    DEVICE_ANDROID: {
        "label": "Android",
        "emoji_id": EID_DEV_ANDROID,
        "fallback": "🤖",
        "install_url": (
            "https://play.google.com/store/apps/details?id=com.happproxy"
        ),
    },
    DEVICE_IOS: {
        "label": "iOS",
        "emoji_id": EID_DEV_IOS,
        "fallback": "🍏",
        "install_url": (
            "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"
        ),
    },
    DEVICE_WINDOWS: {
        "label": "Windows",
        "emoji_id": EID_DEV_WINDOWS,
        "fallback": "🪟",
        "install_url": (
            "https://github.com/Happ-proxy/happ-desktop/releases/latest/"
            "download/setup-Happ.x64.exe"
        ),
    },
    DEVICE_MACOS: {
        "label": "MacOS",
        "emoji_id": EID_DEV_MACOS,
        "fallback": "👤",
        "install_url": (
            "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"
        ),
    },
    DEVICE_LINUX: {
        "label": "Linux",
        "emoji_id": EID_DEV_LINUX,
        "fallback": "🍭",
        "install_url": (
            "https://github.com/Happ-proxy/happ-desktop/releases/latest/"
            "download/Happ.linux.x64.deb"
        ),
    },
    DEVICE_ANDROID_TV: {
        "label": "Android TV",
        "emoji_id": EID_DEV_ANDROID_TV,
        "fallback": "📺",
        "install_url": (
            "https://play.google.com/store/apps/details?id=com.happproxy"
        ),
    },
}

NEWS_URL = "https://t.me/lapavpn"
SUPPORT_URL = "https://t.me/lapateambot"

SUBSCRIPTION_COOLDOWN = 7.0
REFERRAL_SHARE_TEXT = (
    "Нашёл качественный VPN. Если перейдёшь по моей ссылке, "
    "скидка 10% на первую покупку тебе"
)

_bot_username: str | None = None


def e(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def format_balance(balance: float) -> str:
    if balance % 1:
        return f"{balance:g}".replace(".", ",")
    return str(int(balance))


def format_traffic_gb(total_gb: float, used_gb: float) -> str:
    remaining = max(total_gb - used_gb, 0)
    if remaining % 1:
        return f"{remaining:g}".replace(".", ",")
    return str(int(remaining))


def format_date(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y")


def format_datetime(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M")


def subscription_type_label(sub_type: str) -> str:
    if sub_type == "trial":
        return "Пробная подписка"
    return "Платная подписка"


def channel_url(channel_id: str) -> str:
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}"
    if channel_id.startswith("https://t.me/"):
        return channel_id
    return NEWS_URL


def subscribe_text() -> str:
    return (
        f"{e(EID_STOP, '⛔️')} <b>Хей!</b> Чтобы использовать бота, "
        "необходимо подписаться на канал."
    )


def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подписаться",
                    url=channel_url(CHANNEL_ID),
                    icon_custom_emoji_id=EID_SUBSCRIBE,
                ),
                InlineKeyboardButton(
                    text="Я подписался",
                    callback_data=CB_CHECK_SUB,
                    style=ButtonStyle.SUCCESS,
                    icon_custom_emoji_id=EID_CHECK_OK,
                ),
            ]
        ]
    )


def main_menu_text(
    name: str,
    balance: float,
    status: str,
    subscription: dict | None,
    *,
    trial_used: bool,
) -> str:
    lines = [
        f"{e(EID_HELLO, '✨')} <b>Привет, {name}!</b>",
        "",
        f"{e(EID_BALANCE, '💰')} <b>Баланс:</b> {format_balance(balance)}₽",
        "",
    ]

    if status == "expired" and subscription is not None:
        purge_date = format_date(subscription_purge_date(subscription))
        lines.extend(
            [
                f"{e(EID_EXPIRED, '❄️')} <b>Подписка истекла</b> и будет удалена {purge_date}",
                "",
                "<b>Используйте кнопки</b>, чтобы продлить подписку",
            ]
        )
    elif status == "active" and subscription is not None:
        expires = format_date(subscription["expires_at"])
        if subscription["type"] == "trial":
            title = f"<b>Пробная подписка</b> до {expires}:"
        else:
            title = f"<b>Подписка</b> до {expires}:"
        traffic = format_traffic_gb(
            subscription["traffic_total_gb"],
            subscription["traffic_used_gb"],
        )
        lines.extend(
            [
                f"{e(EID_TRIAL, '🪙')} {title}",
                f"— Устройств: {subscription['devices_used']}/{subscription['devices_limit']}",
                f"— Осталось трафика: {traffic} ГБ",
                "",
                "<b>Используйте кнопки, чтобы посмотреть нужную информацию</b>",
            ]
        )
    elif trial_used:
        lines.extend(
            [
                f"{e(EID_NO_SUB, '❌')} <b>Нет активной подписки</b>",
                "",
                "<b>Используйте кнопки</b>, чтобы купить подписку",
            ]
        )
    else:
        lines.extend(
            [
                f"{e(EID_NO_SUB, '❌')} <b>Нет активной подписки</b>",
                "",
                "<b>Используйте кнопки</b>, чтобы активировать пробный период",
            ]
        )

    return "\n".join(lines)


def main_menu_keyboard(status: str, *, trial_used: bool) -> InlineKeyboardMarkup:
    if status == "active":
        primary_button = InlineKeyboardButton(
            text="Управлять подпиской",
            callback_data=CB_MANAGE_SUB,
            style=ButtonStyle.PRIMARY,
            icon_custom_emoji_id=EID_MANAGE,
        )
    elif status == "expired":
        primary_button = InlineKeyboardButton(
            text="Продлить подписку",
            callback_data=CB_EXTEND,
            style=ButtonStyle.SUCCESS,
            icon_custom_emoji_id=EID_EXTEND,
        )
    elif trial_used:
        primary_button = InlineKeyboardButton(
            text="Купить подписку [от 99₽]",
            callback_data=CB_BUY_SUB,
            style=ButtonStyle.PRIMARY,
            icon_custom_emoji_id=EID_TRY_FREE,
        )
    else:
        primary_button = InlineKeyboardButton(
            text="Попробовать бесплатно",
            callback_data=CB_TRY_FREE,
            style=ButtonStyle.PRIMARY,
            icon_custom_emoji_id=EID_TRY_FREE,
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [primary_button],
            [
                InlineKeyboardButton(
                    text="Реферальная система",
                    callback_data=CB_REFERRAL,
                    style=ButtonStyle.SUCCESS,
                    icon_custom_emoji_id=EID_REFERRAL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Новости",
                    url=NEWS_URL,
                    icon_custom_emoji_id=EID_NEWS,
                ),
                InlineKeyboardButton(
                    text="Тех. Поддержка",
                    url=SUPPORT_URL,
                    icon_custom_emoji_id=EID_SUPPORT,
                ),
            ],
        ]
    )


def manage_menu_text(subscription: dict) -> str:
    return (
        f"{e(EID_MANAGE_HEADER, '🌐')} <b>Управление подпиской</b>\n"
        f"— <b>Тип:</b> {subscription_type_label(subscription['type'])}\n"
        f"— <b>Заканчивается:</b> {format_datetime(subscription['expires_at'])}\n\n"
        "<b>Используйте кнопки</b>, чтобы управлять подпиской"
    )


def delete_confirm_text() -> str:
    return (
        f"{e(EID_QUESTION, '❓')} <b>Вы уверены, что хотите удалить подписку?</b>"
    )


def delete_confirm_keyboard(confirm_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Удалить",
                    callback_data=confirm_callback,
                    style=ButtonStyle.DANGER,
                    icon_custom_emoji_id=EID_DELETE,
                ),
                InlineKeyboardButton(
                    text="Не удалять",
                    callback_data=CB_DELETE_CANCEL,
                ),
            ]
        ]
    )


def manage_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Инструкция",
                    callback_data=CB_INSTRUCTION,
                    style=ButtonStyle.PRIMARY,
                    icon_custom_emoji_id=EID_INSTRUCTION,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Продлить подписку",
                    callback_data=CB_EXTEND,
                    style=ButtonStyle.SUCCESS,
                    icon_custom_emoji_id=EID_EXTEND,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Докупить трафик",
                    callback_data=CB_BUY_TRAFFIC,
                    icon_custom_emoji_id=EID_BUY_TRAFFIC,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Перевыпустить подписку",
                    callback_data=CB_REISSUE,
                    icon_custom_emoji_id=EID_REISSUE,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Удалить подписку",
                    callback_data=CB_DELETE,
                    style=ButtonStyle.DANGER,
                    icon_custom_emoji_id=EID_DELETE,
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=CB_BACK_MAIN,
                )
            ],
        ]
    )


def setup_device_callback(flow: str, device: str) -> str:
    return f"tdev:{flow}:{device}"


def setup_step_callback(flow: str, device: str, step: int) -> str:
    return f"tstep:{flow}:{device}:{step}"


def setup_add_callback(flow: str, device: str) -> str:
    return f"tadd:{flow}:{device}"


def setup_steps_indicator(current: int) -> str:
    return " > ".join(
        f"<b>Шаг {step}</b>" if step == current else f"Шаг {step}"
        for step in range(1, 4)
    )


def device_selection_text() -> str:
    return "<b>Выберите устройство</b>:"


def device_selection_keyboard(flow: str) -> InlineKeyboardMarkup:
    def btn(device_key: str) -> InlineKeyboardButton:
        device = DEVICES[device_key]
        return InlineKeyboardButton(
            text=device["label"],
            callback_data=setup_device_callback(flow, device_key),
            icon_custom_emoji_id=device["emoji_id"],
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(DEVICE_ANDROID), btn(DEVICE_IOS)],
            [btn(DEVICE_WINDOWS), btn(DEVICE_MACOS)],
            [btn(DEVICE_LINUX), btn(DEVICE_ANDROID_TV)],
        ]
    )


def setup_step1_text() -> str:
    return (
        f"{setup_steps_indicator(1)}\n\n"
        f"{e(EID_STEP_APP, '💰')} <b>Установите приложение</b>"
    )


def setup_step1_keyboard(flow: str, device_key: str) -> InlineKeyboardMarkup:
    device = DEVICES[device_key]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Установить",
                    url=device["install_url"],
                    style=ButtonStyle.PRIMARY,
                    icon_custom_emoji_id=EID_INSTALL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Далее",
                    callback_data=setup_step_callback(flow, device_key, 2),
                )
            ],
        ]
    )


def setup_step2_text() -> str:
    return (
        f"{setup_steps_indicator(2)}\n\n"
        f"{e(EID_EXTEND, '➕')} <b>Добавьте подписку</b>"
    )


def setup_step2_keyboard(flow: str, device_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Добавить подписку",
                    callback_data=setup_add_callback(flow, device_key),
                    icon_custom_emoji_id=EID_EXTEND,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Далее",
                    callback_data=setup_step_callback(flow, device_key, 3),
                )
            ],
        ]
    )


def setup_step3_text(expires_at: datetime, *, flow: str) -> str:
    if flow == FLOW_TRIAL:
        title = "<b>Мы успешно создали подписку</b>"
    else:
        title = "<b>Всё готово!</b>"
    return (
        f"{setup_steps_indicator(3)}\n\n"
        f"{e(EID_SUCCESS, '✔️')} {title}\n\n"
        f"Вы можете пользоваться VPN до {format_datetime(expires_at)}"
    )


def setup_step3_keyboard(flow: str) -> InlineKeyboardMarkup:
    if flow == FLOW_INSTR:
        back_button = InlineKeyboardButton(
            text="← Назад",
            callback_data=CB_MANAGE_SUB,
        )
    else:
        back_button = InlineKeyboardButton(
            text="На главную",
            callback_data=CB_BACK_MAIN,
        )
    return InlineKeyboardMarkup(inline_keyboard=[[back_button]])


async def show_device_selection(
    bot: Bot,
    user: User,
    flow: str,
    *,
    callback: CallbackQuery,
) -> None:
    await edit_or_send(
        bot,
        user,
        device_selection_text(),
        device_selection_keyboard(flow),
        callback=callback,
    )


async def can_activate_trial(telegram_id: int) -> bool:
    user_data = await get_or_create_user(telegram_id)
    status, _ = await get_subscription_status(telegram_id)
    return status == "none" and not bool(user_data.get("trial_used"))


def display_name(user: User) -> str:
    if user.first_name:
        return user.first_name
    if user.username:
        return f"@{user.username}"
    return "друг"


def parse_referrer_id(args: str | None) -> int | None:
    if not args or not args.startswith("ref"):
        return None
    raw_id = args[3:]
    if not raw_id.isdigit():
        return None
    return int(raw_id)


def parse_referrer_from_message(text: str | None) -> int | None:
    if not text:
        return None
    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0]
    if command != "/start":
        return None
    if len(parts) < 2:
        return None
    return parse_referrer_id(parts[1].strip())


async def save_pending_referrer_from_start(user: User, text: str | None) -> None:
    referrer_id = parse_referrer_from_message(text)
    if referrer_id is None:
        return
    await try_set_pending_referrer(
        user.id,
        referrer_id,
        username=user.username,
        first_name=user.first_name,
    )


async def apply_referral_after_channel_subscribe(user: User) -> None:
    await get_or_create_user(
        user.id,
        username=user.username,
        first_name=user.first_name,
    )
    await confirm_pending_referrer(user.id)


async def get_bot_username(bot: Bot) -> str:
    global _bot_username
    if _bot_username is None:
        me = await bot.get_me()
        if not me.username:
            raise RuntimeError("Bot username is not set in BotFather")
        _bot_username = me.username
    return _bot_username


def referral_link(username: str, user_id: int) -> str:
    return f"https://t.me/{username}?start=ref{user_id}"


async def build_referral_share_url(bot: Bot, user_id: int) -> str:
    username = await get_bot_username(bot)
    bot_link = referral_link(username, user_id)
    share_text = f"{REFERRAL_SHARE_TEXT}\n{bot_link}"
    return (
        "https://t.me/share/url?"
        f"url={quote(bot_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )


def referral_menu_text(referral_balance: float) -> str:
    return (
        f"{e(EID_REFERRAL, '➕')} <b>Реферальная система</b>\n\n"
        f"{e(EID_BALANCE, '💰')} <b>Баланс с рефералов:</b> "
        f"{format_balance(referral_balance)}₽\n\n"
        "Приглашайте друзей и получайте 20% от их трат и 5% от трат их друзей. "
        "Просто нажми на кнопку ниже."
    )


def referral_menu_keyboard(share_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пригласить",
                    url=share_url,
                    style=ButtonStyle.SUCCESS,
                    icon_custom_emoji_id=EID_INVITE,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Мои рефералы",
                    callback_data=CB_REF_LIST,
                    icon_custom_emoji_id=EID_REFERRAL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=CB_BACK_MAIN,
                )
            ],
        ]
    )


def referral_display_name(ref: dict) -> str:
    if ref.get("first_name"):
        return ref["first_name"]
    if ref.get("username"):
        return f"@{ref['username']}"
    return f"ID {ref['telegram_id']}"


def referrals_list_text(
    referrals: list[dict],
    page: int,
    total: int,
) -> str:
    lines = [
        f"{e(EID_REFERRAL, '➕')} <b>Мои рефералы</b>",
        "",
    ]
    if total == 0:
        lines.append("У вас пока нет рефералов")
        return "\n".join(lines)

    start_index = page * REFERRALS_PER_PAGE
    for index, ref in enumerate(referrals, start=start_index + 1):
        spent = format_balance(float(ref.get("total_spent") or 0))
        lines.append(f"{index}. {referral_display_name(ref)} — потратили {spent}₽")

    total_pages = (total + REFERRALS_PER_PAGE - 1) // REFERRALS_PER_PAGE
    if total_pages > 1:
        lines.extend(["", f"Страница {page + 1} из {total_pages}"])

    return "\n".join(lines)


def referrals_list_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    total_pages = max(1, (total + REFERRALS_PER_PAGE - 1) // REFERRALS_PER_PAGE)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"reflp:{page - 1}",
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"reflp:{page + 1}",
                )
            )
        if nav:
            rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="← Назад",
                callback_data=CB_REFERRAL,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_referral_menu(
    bot: Bot,
    user: User,
    *,
    callback: CallbackQuery,
) -> None:
    share_url = await build_referral_share_url(bot, user.id)
    referral_balance = await get_referral_balance(user.id)
    await edit_or_send(
        bot,
        user,
        referral_menu_text(referral_balance),
        referral_menu_keyboard(share_url),
        callback=callback,
    )


async def show_referrals_list(
    bot: Bot,
    user: User,
    page: int,
    *,
    callback: CallbackQuery,
) -> None:
    total = await get_referrals_count(user.id)
    referrals = await get_referrals_page(user.id, page, REFERRALS_PER_PAGE)
    await edit_or_send(
        bot,
        user,
        referrals_list_text(referrals, page, total),
        referrals_list_keyboard(page, total),
        callback=callback,
    )


class SubscriptionCache:
    def __init__(self, cooldown: float = SUBSCRIPTION_COOLDOWN) -> None:
        self._cooldown = cooldown
        self._cache: dict[int, tuple[float, bool]] = {}

    async def check(
        self,
        bot: Bot,
        user_id: int,
        *,
        force: bool = False,
    ) -> bool:
        now = time.monotonic()
        if not force and user_id in self._cache:
            checked_at, result = self._cache[user_id]
            if now - checked_at < self._cooldown:
                return result

        try:
            member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            subscribed = member.status not in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED,
            )
        except Exception:
            logger.exception("Failed to check subscription for user %s", user_id)
            subscribed = False

        self._cache[user_id] = (now, subscribed)
        return subscribed


sub_cache = SubscriptionCache()
router = Router()


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot = data["bot"]
        user: User | None = None
        message: Message | None = None
        callback: CallbackQuery | None = None

        if isinstance(event, Message):
            if event.chat.type != "private" or not event.from_user:
                return await handler(event, data)
            user = event.from_user
            message = event
        elif isinstance(event, CallbackQuery):
            if not event.from_user:
                return await handler(event, data)
            if event.data == CB_CHECK_SUB:
                return await handler(event, data)
            user = event.from_user
            callback = event
            message = event.message
        else:
            return await handler(event, data)

        if isinstance(event, Message):
            await save_pending_referrer_from_start(user, message.text)

        subscribed = await sub_cache.check(bot, user.id)

        if subscribed:
            await apply_referral_after_channel_subscribe(user)
            data["subscribed"] = True
            return await handler(event, data)

        text = subscribe_text()
        keyboard = subscribe_keyboard()

        if isinstance(event, Message):
            await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            return None

        if callback:
            await callback.answer()
            if message:
                try:
                    await message.edit_text(
                        text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    await bot.send_message(
                        user.id,
                        text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                    )
        return None


async def edit_or_send(
    bot: Bot,
    user: User,
    text: str,
    keyboard: InlineKeyboardMarkup,
    *,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    if callback:
        await callback.answer()
        if callback.message:
            try:
                await callback.message.edit_text(
                    text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception:
                pass

    if message:
        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return

    await bot.send_message(
        user.id,
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def show_main_menu(
    bot: Bot,
    user: User,
    *,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    user_data = await get_or_create_user(
        user.id,
        username=user.username,
        first_name=user.first_name,
    )
    balance = await get_balance(user.id)
    status, subscription = await get_subscription_status(user.id)
    trial_used = bool(user_data.get("trial_used"))
    text = main_menu_text(
        display_name(user),
        balance,
        status,
        subscription,
        trial_used=trial_used,
    )
    keyboard = main_menu_keyboard(status, trial_used=trial_used)
    await edit_or_send(
        bot,
        user,
        text,
        keyboard,
        message=message,
        callback=callback,
    )


async def show_manage_menu(
    bot: Bot,
    user: User,
    *,
    callback: CallbackQuery,
) -> None:
    status, subscription = await get_subscription_status(user.id)
    if status != "active" or subscription is None:
        await callback.answer("Нет активной подписки", show_alert=True)
        await show_main_menu(bot, user, callback=callback)
        return

    text = manage_menu_text(subscription)
    keyboard = manage_menu_keyboard()
    await edit_or_send(bot, user, text, keyboard, callback=callback)


async def show_delete_confirm(
    bot: Bot,
    user: User,
    *,
    callback: CallbackQuery,
    confirm_callback: str,
) -> None:
    await edit_or_send(
        bot,
        user,
        delete_confirm_text(),
        delete_confirm_keyboard(confirm_callback),
        callback=callback,
    )


@router.callback_query(F.data == CB_CHECK_SUB)
async def check_subscription(callback: CallbackQuery, bot: Bot) -> None:
    subscribed = await sub_cache.check(bot, callback.from_user.id, force=True)
    if subscribed:
        await apply_referral_after_channel_subscribe(callback.from_user)
        await show_main_menu(bot, callback.from_user, callback=callback)
        return

    await callback.answer(
        "❗️ Подписка не обнаружена. Подпишитесь и подождите ~30 секунд",
        show_alert=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, command: CommandObject) -> None:
    if command.args:
        await try_set_pending_referrer(
            message.from_user.id,
            parse_referrer_id(command.args),
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
    await show_main_menu(bot, message.from_user, message=message)


@router.message(F.text)
async def any_text(message: Message, bot: Bot) -> None:
    await show_main_menu(bot, message.from_user, message=message)


@router.callback_query(F.data == CB_TRY_FREE)
async def try_free(callback: CallbackQuery, bot: Bot) -> None:
    if not await can_activate_trial(callback.from_user.id):
        await callback.answer(
            "Пробный период уже был использован или подписка уже активна",
            show_alert=True,
        )
        return

    await show_device_selection(
        bot, callback.from_user, FLOW_TRIAL, callback=callback
    )


@router.callback_query(F.data == CB_INSTRUCTION)
async def instruction(callback: CallbackQuery, bot: Bot) -> None:
    status, _ = await get_subscription_status(callback.from_user.id)
    if status != "active":
        await callback.answer("Нет активной подписки", show_alert=True)
        return

    await show_device_selection(
        bot, callback.from_user, FLOW_INSTR, callback=callback
    )


@router.callback_query(F.data.startswith("tdev:"))
async def setup_select_device(callback: CallbackQuery, bot: Bot) -> None:
    _, flow, device_key = callback.data.split(":", 2)
    if device_key not in DEVICES or flow not in (FLOW_TRIAL, FLOW_INSTR):
        await callback.answer("Неизвестное устройство", show_alert=True)
        return

    if flow == FLOW_TRIAL:
        if not await can_activate_trial(callback.from_user.id):
            await callback.answer(
                "Пробный период уже был использован или подписка уже активна",
                show_alert=True,
            )
            return
    else:
        status, _ = await get_subscription_status(callback.from_user.id)
        if status != "active":
            await callback.answer("Нет активной подписки", show_alert=True)
            return

    await edit_or_send(
        bot,
        callback.from_user,
        setup_step1_text(),
        setup_step1_keyboard(flow, device_key),
        callback=callback,
    )


@router.callback_query(F.data.startswith("tstep:"))
async def setup_next_step(callback: CallbackQuery, bot: Bot) -> None:
    _, flow, device_key, step_raw = callback.data.split(":", 3)
    if device_key not in DEVICES or flow not in (FLOW_TRIAL, FLOW_INSTR):
        await callback.answer("Неизвестное устройство", show_alert=True)
        return

    step = int(step_raw)
    if step == 2:
        if flow == FLOW_TRIAL:
            if not await can_activate_trial(callback.from_user.id):
                await callback.answer(
                    "Пробный период уже был использован или подписка уже активна",
                    show_alert=True,
                )
                return
        else:
            status, _ = await get_subscription_status(callback.from_user.id)
            if status != "active":
                await callback.answer("Нет активной подписки", show_alert=True)
                return

        await edit_or_send(
            bot,
            callback.from_user,
            setup_step2_text(),
            setup_step2_keyboard(flow, device_key),
            callback=callback,
        )
        return

    if step == 3:
        if flow == FLOW_TRIAL:
            sub = await create_trial_subscription(callback.from_user.id)
            if sub is None:
                await callback.answer(
                    "Пробный период уже был использован или подписка уже активна",
                    show_alert=True,
                )
                return
        else:
            status, sub = await get_subscription_status(callback.from_user.id)
            if status != "active" or sub is None:
                await callback.answer("Нет активной подписки", show_alert=True)
                return

        await edit_or_send(
            bot,
            callback.from_user,
            setup_step3_text(sub["expires_at"], flow=flow),
            setup_step3_keyboard(flow),
            callback=callback,
        )


@router.callback_query(F.data.startswith("tadd:"))
async def setup_add_subscription(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == CB_MANAGE_SUB)
async def manage_subscription(callback: CallbackQuery, bot: Bot) -> None:
    await show_manage_menu(bot, callback.from_user, callback=callback)


@router.callback_query(F.data == CB_BACK_MAIN)
async def back_to_main(callback: CallbackQuery, bot: Bot) -> None:
    await show_main_menu(bot, callback.from_user, callback=callback)


@router.callback_query(F.data == CB_DELETE)
async def delete_subscription_step1(callback: CallbackQuery, bot: Bot) -> None:
    status, _ = await get_subscription_status(callback.from_user.id)
    if status != "active":
        await callback.answer("Нет активной подписки", show_alert=True)
        return

    await show_delete_confirm(
        bot,
        callback.from_user,
        callback=callback,
        confirm_callback=CB_DELETE_CONFIRM,
    )


@router.callback_query(F.data == CB_DELETE_CONFIRM)
async def delete_subscription_step2(callback: CallbackQuery, bot: Bot) -> None:
    await show_delete_confirm(
        bot,
        callback.from_user,
        callback=callback,
        confirm_callback=CB_DELETE_FINAL,
    )


@router.callback_query(F.data == CB_DELETE_FINAL)
async def delete_subscription_final(callback: CallbackQuery, bot: Bot) -> None:
    deleted = await deactivate_subscription(callback.from_user.id)
    if not deleted:
        await callback.answer("Нет активной подписки", show_alert=True)
        return

    await show_main_menu(bot, callback.from_user, callback=callback)


@router.callback_query(F.data == CB_DELETE_CANCEL)
async def delete_subscription_cancel(callback: CallbackQuery, bot: Bot) -> None:
    await show_manage_menu(bot, callback.from_user, callback=callback)


@router.callback_query(
    F.data.in_({CB_EXTEND, CB_BUY_TRAFFIC, CB_REISSUE, CB_BUY_SUB})
)
async def manage_stub(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == CB_REFERRAL)
async def referral(callback: CallbackQuery, bot: Bot) -> None:
    await show_referral_menu(bot, callback.from_user, callback=callback)


@router.callback_query(F.data == CB_REF_LIST)
async def referrals_list(callback: CallbackQuery, bot: Bot) -> None:
    await show_referrals_list(bot, callback.from_user, 0, callback=callback)


@router.callback_query(F.data.startswith("reflp:"))
async def referrals_list_page(callback: CallbackQuery, bot: Bot) -> None:
    page = int(callback.data.split(":", 1)[1])
    await show_referrals_list(bot, callback.from_user, page, callback=callback)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not CHANNEL_ID:
        raise RuntimeError("TELEGRAM_CHANNEL_ID is not set in .env")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    await get_bot_username(bot)
    dp = Dispatcher()
    router.message.middleware(SubscriptionMiddleware())
    router.callback_query.middleware(SubscriptionMiddleware())
    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
