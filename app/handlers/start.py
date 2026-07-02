from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import claim_daily_bonus, get_admin_permissions, get_author_profile, get_bonus_balance, get_referral_stats, list_bonus_transactions, register_referral, reward_referral_if_needed, upsert_user
from app.keyboards import back_to_main, bonuses_menu, main_menu, more_menu

router = Router()


async def build_context(message_or_call) -> tuple[bool, bool, bool, int]:
    tg_user = message_or_call.from_user
    user = await upsert_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        full_name=tg_user.full_name,
    )
    is_owner = tg_user.id in settings.owner_ids
    perms = await get_admin_permissions(user["id"])
    author_profile = await get_author_profile(user["id"])
    return is_owner, bool(perms), bool(author_profile), user["id"]


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    is_owner, has_admin, has_author, user_id = await build_context(message)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_"):
        raw = parts[1].replace("ref_", "", 1)
        if raw.isdigit():
            ref_tg_id = int(raw)
            if ref_tg_id != message.from_user.id:
                ref_user = await upsert_user(ref_tg_id, None, None)
                await register_referral(int(ref_user["id"]), user_id)
                await reward_referral_if_needed(user_id)
    text = (
        "<b>Вокслира</b>\n\n"
        "Истории, которые звучат.\n\n"
        "Выберите раздел."
    )
    await message.answer(text, reply_markup=main_menu(is_owner, has_admin, has_author))


@router.callback_query(F.data == "menu:main")
async def callback_main_menu(call: CallbackQuery) -> None:
    is_owner, has_admin, has_author, _ = await build_context(call)
    await call.message.edit_text(
        "<b>Вокслира</b>\n\nВыберите раздел.",
        reply_markup=main_menu(is_owner, has_admin, has_author),
    )
    await call.answer()


@router.callback_query(F.data == "main:more")
async def callback_more(call: CallbackQuery) -> None:
    _, _, has_author, _ = await build_context(call)
    await call.message.edit_text("<b>Ещё</b>\n\nВыберите нужный раздел.", reply_markup=more_menu(has_author))
    await call.answer()


@router.callback_query(F.data == "main:bonuses")
async def callback_bonuses(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    balance = await get_bonus_balance(user_id)
    await call.message.edit_text(
        "<b>💎 Бонусы</b>\n\n"
        f"Ваш баланс: <b>{balance}</b> бонусов.\n\n"
        "Бонусы нужны для будущих скидок, промокодов, активности и мягкой мотивации читателей. "
        "Сейчас доступен ежедневный бонус и реферальная ссылка.",
        reply_markup=bonuses_menu(True),
    )
    await call.answer()


@router.callback_query(F.data == "bonus:daily")
async def callback_daily_bonus(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    received, amount, balance = await claim_daily_bonus(user_id)
    if received:
        text = f"Начислено: <b>{amount}</b> бонусов.\nБаланс: <b>{balance}</b>."
    else:
        text = f"Сегодня бонус уже получен.\nБаланс: <b>{balance}</b>."
    await call.message.edit_text("<b>💎 Бонусы</b>\n\n" + text, reply_markup=bonuses_menu(not received))
    await call.answer("Готово" if received else "Уже получали сегодня")


@router.callback_query(F.data == "bonus:history")
async def callback_bonus_history(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    rows = await list_bonus_transactions(user_id, limit=10)
    if not rows:
        text = "История бонусов пока пустая."
    else:
        lines = ["<b>📜 История бонусов</b>\n"]
        for row in rows:
            sign = "+" if int(row["amount"]) >= 0 else ""
            lines.append(f"• {sign}{row['amount']} · {row['reason']} · {row['created_at'][:10]}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=bonuses_menu(True))
    await call.answer()


@router.callback_query(F.data.in_({"main:read", "main:listen", "main:my", "main:support", "main:settings"}))
async def callback_stub(call: CallbackQuery) -> None:
    titles = {
        "main:read": "📚 Читать",
        "main:listen": "🎧 Слушать",
        "main:my": "⭐ Моё",
        "main:support": "🛟 Поддержка",
        "main:settings": "⚙️ Настройки",
    }
    await call.message.edit_text(
        f"<b>{titles.get(call.data, 'Раздел')}</b>\n\n"
        "Раздел подготовлен. В следующих этапах сюда добавим полную логику.",
        reply_markup=back_to_main(),
    )
    await call.answer()


@router.callback_query(F.data == "bonus:referral")
async def callback_referral(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    stats = await get_referral_stats(user_id)
    bot_username = (await call.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{call.from_user.id}"
    await call.message.edit_text(
        "<b>👥 Пригласить друга</b>\n\n"
        f"Ваша ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено: <b>{stats['invited']}</b>\n"
        f"С бонусом: <b>{stats['rewarded']}</b>\n\n"
        "Бонус начисляется, когда новый человек открывает бота по вашей ссылке.",
        reply_markup=bonuses_menu(True),
    )
    await call.answer()
