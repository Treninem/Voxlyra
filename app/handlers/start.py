from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db import claim_daily_bonus, get_admin_permissions, get_author_profile, get_bonus_balance, get_referral_stats, list_bonus_transactions, register_referral, reward_referral_if_needed, upsert_user, get_user_preferences, set_user_preference, reset_user_preferences, get_book
from app.keyboards import back_to_main, bonuses_menu, main_menu, more_menu, user_settings_menu, user_notifications_menu, user_theme_menu, user_font_menu
from app.handlers.legal import send_next_required_document

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
    payload = parts[1].strip() if len(parts) > 1 else ""
    if payload.startswith("ref_"):
        raw = payload.replace("ref_", "", 1)
        if raw.isdigit():
            ref_tg_id = int(raw)
            if ref_tg_id != message.from_user.id:
                ref_user = await upsert_user(ref_tg_id, None, None)
                await register_referral(int(ref_user["id"]), user_id)
                await reward_referral_if_needed(user_id)
    if settings.LEGAL_REQUIRE_ON_START and await send_next_required_document(message, user_id):
        return
    if payload.startswith("promote_book_"):
        raw_book_id = payload.replace("promote_book_", "", 1)
        if raw_book_id.isdigit():
            book = await get_book(int(raw_book_id))
            if book and book["publication_status"] == "published":
                kb = InlineKeyboardBuilder()
                kb.button(text="📢 Опубликовать книгу в канале", callback_data=f"channel:promote:{int(raw_book_id)}")
                kb.button(text="🏠 Главное меню", callback_data="menu:main")
                kb.adjust(1)
                await message.answer(
                    f"<b>📢 Продвижение книги</b>\n\n"
                    f"Книга: <b>{book['title']}</b>\n"
                    f"Автор: <b>{book['pen_name'] or 'не указан'}</b>\n\n"
                    "После подтверждения бот проверит доступность публикации и покажет стоимость.",
                    reply_markup=kb.as_markup(),
                )
                return
    if payload.startswith("book_"):
        raw_book_id = payload.replace("book_", "", 1)
        if raw_book_id.isdigit():
            book = await get_book(int(raw_book_id))
            if book and book["publication_status"] == "published":
                kb = InlineKeyboardBuilder()
                web_url = settings.WEBAPP_URL.strip().rstrip("/")
                if web_url:
                    kb.button(text="📖 Открыть книгу", url=f"{web_url}/book/{int(raw_book_id)}")
                kb.button(text="🏠 Главное меню", callback_data="menu:main")
                kb.adjust(1)
                await message.answer(
                    f"<b>📖 {book['title']}</b>\n\n"
                    f"Автор: <b>{book['pen_name'] or 'не указан'}</b>\n\n"
                    "Книга доступна в Вокслире.",
                    reply_markup=kb.as_markup(),
                )
                return
    text = (
        "<b>✨ Добро пожаловать в Вокслиру</b>\n\n"
        "Здесь истории можно читать, слушать и сохранять в свою личную библиотеку.\n\n"
        "Начните с каталога или продолжите то, что уже открыли."
    )
    await message.answer(text, reply_markup=main_menu(is_owner, has_admin, has_author))


@router.callback_query(F.data == "menu:main")
async def callback_main_menu(call: CallbackQuery) -> None:
    is_owner, has_admin, has_author, _ = await build_context(call)
    await call.message.edit_text(
        "<b>✨ Вокслира</b>\n\n"
        "Ваша библиотека историй, глав и голосов.\n"
        "Выберите, куда пойдём.",
        reply_markup=main_menu(is_owner, has_admin, has_author),
    )
    await call.answer()


@router.callback_query(F.data == "main:more")
async def callback_more(call: CallbackQuery) -> None:
    _, _, has_author, _ = await build_context(call)
    await call.message.edit_text(
        "<b>⚙️ Ещё</b>\n\nОформление, поддержка и правила — всё необходимое без лишних пунктов.",
        reply_markup=more_menu(has_author),
    )
    await call.answer()


@router.callback_query(F.data == "main:bonuses")
async def callback_bonuses(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    balance = await get_bonus_balance(user_id)
    await call.message.edit_text(
        "<b>💎 Бонусы</b>\n\n"
        f"Ваш баланс: <b>{balance}</b> бонусов.\n\n"
        "Получайте бонусы за ежедневный вход и приглашения.",
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


@router.callback_query(F.data == "main:read")
async def callback_read_fallback(call: CallbackQuery) -> None:
    url = settings.WEBAPP_URL.rstrip("/")
    if url:
        await call.message.edit_text(
            "<b>📚 Читать</b>\n\nКаталог открывается во встроенном окне Telegram. Если окно не появилось, закройте Telegram и попробуйте ещё раз. Если ошибка повторится, напишите в поддержку.",
            reply_markup=back_to_main(),
        )
    else:
        await call.message.edit_text("<b>📚 Читать</b>\n\nКаталог временно недоступен. Попробуйте открыть его позже или напишите в поддержку.", reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data == "main:listen")
async def callback_listen_fallback(call: CallbackQuery) -> None:
    url = settings.WEBAPP_URL.rstrip("/")
    if url:
        await call.message.edit_text(
            "<b>🎧 Слушать</b>\n\nАудиокниги открываются во встроенном окне Telegram. Если аудиоглав ещё нет, они появятся здесь после загрузки авторами.",
            reply_markup=back_to_main(),
        )
    else:
        await call.message.edit_text("<b>🎧 Слушать</b>\n\nАудиораздел временно недоступен. Попробуйте открыть его позже или напишите в поддержку.", reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data == "main:support")
async def callback_support(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "<b>🛟 Поддержка</b>\n\n"
        "Напишите одним сообщением, что случилось. Для платежей укажите книгу, главу, дату оплаты и что именно не открылось. "
        "Для жалобы на книгу укажите название и причину. Обращение будет видно владельцу и администрации с доступом к поддержке.",
        reply_markup=back_to_main(),
    )
    await call.answer()


@router.callback_query(F.data == "main:settings")
async def callback_user_settings(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    prefs = await get_user_preferences(user_id)
    await call.message.edit_text(
        "<b>⚙️ Настройки</b>\n\n"
        "Здесь можно выбрать тему, размер текста и уведомления. Настройки сохраняются и применяются при чтении.",
        reply_markup=user_settings_menu(prefs),
    )
    await call.answer()


@router.callback_query(F.data == "settings:theme")
async def callback_user_settings_theme(call: CallbackQuery) -> None:
    await call.message.edit_text("<b>🎨 Тема</b>\n\nВыберите оформление читалки.", reply_markup=user_theme_menu())
    await call.answer()


@router.callback_query(F.data.startswith("settings:set_theme:"))
async def callback_set_theme(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    theme = call.data.split(":")[-1]
    prefs = await set_user_preference(user_id, "theme", theme)
    await call.message.edit_text("<b>⚙️ Настройки</b>\n\nТема сохранена.", reply_markup=user_settings_menu(prefs))
    await call.answer("Сохранено")


@router.callback_query(F.data == "settings:font")
async def callback_user_settings_font(call: CallbackQuery) -> None:
    await call.message.edit_text("<b>🔠 Размер шрифта</b>\n\nВыберите размер текста для чтения.", reply_markup=user_font_menu())
    await call.answer()


@router.callback_query(F.data.startswith("settings:set_font:"))
async def callback_set_font(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    font = call.data.split(":")[-1]
    prefs = await set_user_preference(user_id, "font_size", font)
    await call.message.edit_text("<b>⚙️ Настройки</b>\n\nРазмер шрифта сохранён.", reply_markup=user_settings_menu(prefs))
    await call.answer("Сохранено")


@router.callback_query(F.data == "settings:notifications")
async def callback_user_notifications(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    prefs = await get_user_preferences(user_id)
    await call.message.edit_text(
        "<b>🔔 Уведомления</b>\n\nВыберите, какие события Вокслира будет присылать вам в Telegram.",
        reply_markup=user_notifications_menu(prefs),
    )
    await call.answer()


@router.callback_query(F.data.startswith("settings:toggle_notification:"))
async def callback_toggle_notification_category(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    key = call.data.rsplit(":", 1)[-1]
    allowed = {
        "notifications", "notifications_chapters", "notifications_audio", "notifications_discounts",
        "notifications_reminders", "notifications_achievements",
    }
    if key not in allowed:
        await call.answer("Настройка не найдена", show_alert=True)
        return
    prefs = await get_user_preferences(user_id)
    new_value = "0" if str(prefs.get(key, "1")) != "0" else "1"
    prefs = await set_user_preference(user_id, key, new_value)
    await call.message.edit_text(
        "<b>🔔 Уведомления</b>\n\nВыбор сохранён.",
        reply_markup=user_notifications_menu(prefs),
    )
    await call.answer("Сохранено")


@router.callback_query(F.data == "settings:toggle_notifications")
async def callback_toggle_notifications_legacy(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    prefs = await get_user_preferences(user_id)
    new_value = "0" if str(prefs.get("notifications", "1")) != "0" else "1"
    prefs = await set_user_preference(user_id, "notifications", new_value)
    await call.message.edit_text(
        "<b>🔔 Уведомления</b>\n\nВыбор сохранён.",
        reply_markup=user_notifications_menu(prefs),
    )
    await call.answer("Сохранено")


@router.callback_query(F.data == "settings:reset")
async def callback_reset_settings(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    prefs = await reset_user_preferences(user_id)
    await call.message.edit_text("<b>⚙️ Настройки</b>\n\nНастройки сброшены.", reply_markup=user_settings_menu(prefs))
    await call.answer("Сброшено")


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
