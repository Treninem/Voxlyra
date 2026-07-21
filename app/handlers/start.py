from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db import (
    get_admin_permissions, get_author_profile, get_bonus_balance, get_referral_stats,
    list_bonus_transactions, list_reader_wallet_transactions, get_wallet_summary,
    register_referral, upsert_user, get_user_preferences, set_user_preference,
    reset_user_preferences, get_book, get_chapter, get_graphic_chapter, get_audio_chapter,
)
from app.keyboards import back_to_main, bonuses_menu, main_menu, more_menu, user_settings_menu, user_notifications_menu, user_theme_menu, user_font_menu
from app.handlers.legal import send_next_required_document
from app.services.bonus_economy import load_revenue_split_settings

router = Router()

async def _safe_edit_text(message: Message, text: str, *, reply_markup=None) -> None:
    """Edit a text message or safely replace media/service messages with a new one.

    Telegram cannot apply ``editMessageText`` to a photo, document, animation or
    any other message that has no text body. Navigation buttons can legitimately
    live under such messages, so falling back to ``answer`` is required instead of
    letting a callback crash.
    """
    if message.text is None:
        try:
            await message.delete()
        except TelegramBadRequest:
            # The message may be too old, already deleted or not deletable. A new
            # menu still has to be shown even when cleanup is impossible.
            pass
        await message.answer(text, reply_markup=reply_markup)
        return

    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            return
        if "there is no text in the message to edit" not in lowered:
            raise
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer(text, reply_markup=reply_markup)


def _bonus_reason_label(reason: object) -> str:
    labels = {
        "daily_bonus": "Старое ежедневное начисление",
        "referral_invite": "Старый бонус за приглашение",
        "referral_join": "Старый бонус приглашённому",
        "topup_cashback": "Кешбэк за пополнение",
        "referral_topup": "Пополнение приглашённого",
        "chapter_purchase_discount": "Скидка на главу",
        "chapter_purchase_refund": "Возврат бонусов за главу",
    }
    raw = str(reason or "").strip()
    return labels.get(raw, raw.replace("_", " ") or "Начисление")



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


async def _deep_link_target(payload: str) -> tuple[str, str, str] | None:
    """Resolve a safe content deep link into (relative URL, title, subtitle)."""
    value = str(payload or "").strip()
    static_targets = {
        "catalog": ("/catalog", "Каталог", "Выберите книгу или комикс."),
        "library": ("/library", "Моя библиотека", "Продолжите чтение и откройте личные списки."),
        "audio": ("/audio", "Аудиокниги", "Откройте доступные аудиоглавы."),
        "comics": ("/comics", "Комиксы", "Откройте каталог графических историй."),
        "premium": ("/premium", "VoxLyra Premium", "Управление подпиской и преимуществами."),
        "settings": ("/settings", "Настройки", "Оформление и параметры чтения."),
        "author": ("/author", "Кабинет автора", "Книги, главы и публикация."),
        "control": ("/control", "Центр управления", "Доступ определяется вашей ролью в VoxLyra."),
    }
    if value in static_targets:
        return static_targets[value]

    for prefix, route_prefix in (("book_", "/book/"), ("reader_", "/reader/"), ("chapter_", "/reader/"), ("comic_", "/comic/"), ("audio_", "/audio/")):
        if not value.startswith(prefix):
            continue
        raw_id = value[len(prefix):]
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return None
        target_id = int(raw_id)
        if prefix == "book_":
            row = await get_book(target_id)
            if not row or str(row["publication_status"] or "") != "published":
                return None
            return f"{route_prefix}{target_id}", str(row["title"] or "Книга"), f"Автор: {row['pen_name'] or 'не указан'}"
        if prefix in {"reader_", "chapter_"}:
            row = await get_chapter(target_id)
            if not row or str(row["publication_status"] or "") != "published" or str(row["status"] or "") != "published":
                return None
            return f"{route_prefix}{target_id}", str(row["title"] or "Глава"), str(row["book_title"] or "Книга")
        if prefix == "comic_":
            row = await get_graphic_chapter(target_id)
            if not row or str(row["publication_status"] or "") != "published" or str(row["status"] or "") != "published":
                return None
            return f"{route_prefix}{target_id}", str(row["title"] or "Глава"), str(row["book_title"] or "Графическая история")
        row = await get_audio_chapter(target_id)
        if not row or str(row["publication_status"] or "") != "published" or str(row["status"] or "") != "published":
            return None
        return f"{route_prefix}{target_id}", str(row["title"] or "Аудиоглава"), str(row["book_title"] or "Аудиокнига")
    return None


async def _send_deep_link_target(message: Message, payload: str) -> bool:
    target = await _deep_link_target(payload)
    if target is None:
        return False
    relative_url, title, subtitle = target
    web_url = settings.WEBAPP_URL.strip().rstrip("/")
    kb = InlineKeyboardBuilder()
    if web_url:
        kb.button(text="Открыть в VoxLyra", web_app=WebAppInfo(url=f"{web_url}{relative_url}"))
    kb.button(text="Главное меню", callback_data="menu:main")
    kb.adjust(1)
    await message.answer(
        f"<b>{title}</b>\n\n{subtitle}\n\nОткройте нужный экран внутри VoxLyra.",
        reply_markup=kb.as_markup(),
    )
    return True


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
    if await _send_deep_link_target(message, payload):
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
    await _safe_edit_text(call.message,
        "<b>✨ Вокслира</b>\n\n"
        "Ваша библиотека историй, глав и голосов.\n"
        "Выберите, куда пойдём.",
        reply_markup=main_menu(is_owner, has_admin, has_author),
    )
    await call.answer()


@router.callback_query(F.data == "main:more")
async def callback_more(call: CallbackQuery) -> None:
    _, _, has_author, _ = await build_context(call)
    await _safe_edit_text(call.message,
        "<b>⚙️ Ещё</b>\n\nОформление, поддержка и правила — всё необходимое без лишних пунктов.",
        reply_markup=more_menu(has_author),
    )
    await call.answer()


@router.callback_query(F.data == "main:bonuses")
async def callback_bonuses(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    summary = await get_wallet_summary(user_id)
    cfg = await load_revenue_split_settings()
    points_per_star = max(1, int(summary["points_per_star"]))
    usable_bonus_stars, points_remainder = divmod(int(summary["bonus_points"]), points_per_star)
    next_star_line = (
        f"До следующей целой Star скидки: <b>{points_per_star - points_remainder} бонусов</b>.\n"
        if points_remainder else ""
    )
    await _safe_edit_text(call.message,
        "<b>💎 Баланс и бонусы</b>\n\n"
        f"Баланс покупок: <b>{summary['wallet_stars']} Stars</b>.\n"
        f"Бонусы: <b>{summary['bonus_points']}</b>. Доступно: <b>{usable_bonus_stars} Stars</b> скидки.\n"
        f"{next_star_line}"
        f"Курс: <b>{points_per_star} бонусов = 1 целая Star</b>.\n\n"
        "Ежедневных начислений больше нет. Бонусы появляются только после реального пополнения баланса. "
        "Если пользователь пришёл по реферальной ссылке, часть бонусного фонда получает пригласивший. "
        "Бонусы можно применить к покупке платной главы; доход автора при этом не уменьшается.",
        reply_markup=bonuses_menu(cfg.topup_packages),
    )
    await call.answer()


@router.callback_query(F.data == "bonus:daily")
async def callback_daily_bonus(call: CallbackQuery) -> None:
    # Старые сообщения с этой кнопкой могут оставаться в чатах после обновления.
    await call.answer("Ежедневные бонусы отключены. Теперь бонусы начисляются за пополнение.", show_alert=True)


@router.callback_query(F.data == "bonus:history")
async def callback_bonus_history(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    bonus_rows = await list_bonus_transactions(user_id, limit=12)
    wallet_rows = await list_reader_wallet_transactions(user_id, limit=12)
    events: list[tuple[str, str]] = []
    for row in bonus_rows:
        amount = int(row["amount"] or 0)
        sign = "+" if amount >= 0 else ""
        events.append((str(row["created_at"]), f"• {sign}{amount} бонусов · {_bonus_reason_label(row['reason'])}"))
    wallet_labels = {
        "topup": "Пополнение баланса",
        "chapter_purchase": "Покупка главы",
        "purchase_refund": "Возврат на баланс",
    }
    for row in wallet_rows:
        amount = int(row["amount_stars"] or 0)
        sign = "+" if amount >= 0 else ""
        events.append((str(row["created_at"]), f"• {sign}{amount} Stars · {wallet_labels.get(str(row['transaction_type']), str(row['transaction_type']))}"))
    events.sort(key=lambda item: item[0], reverse=True)
    if not events:
        text = "История баланса и бонусов пока пустая."
    else:
        lines = ["<b>📜 История баланса и бонусов</b>\n"]
        for created_at, line in events[:15]:
            lines.append(f"{line} · {created_at[:10]}")
        text = "\n".join(lines)
    cfg = await load_revenue_split_settings()
    await _safe_edit_text(call.message,text, reply_markup=bonuses_menu(cfg.topup_packages))
    await call.answer()


@router.callback_query(F.data == "main:read")
async def callback_read_fallback(call: CallbackQuery) -> None:
    url = settings.WEBAPP_URL.rstrip("/")
    if url:
        kb = InlineKeyboardBuilder()
        kb.button(
            text="📚 Открыть Mini App",
            web_app=WebAppInfo(url=f"{url}/catalog"),
        )
        kb.button(text="⬅️ Назад", callback_data="menu:main")
        kb.adjust(1)
        await _safe_edit_text(call.message,
            "<b>📚 Читать</b>\n\nНажмите кнопку ниже — каталог откроется сразу во встроенном окне Telegram.",
            reply_markup=kb.as_markup(),
        )
    else:
        await _safe_edit_text(call.message,"<b>📚 Читать</b>\n\nКаталог временно недоступен. Попробуйте открыть его позже или напишите в поддержку.", reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data == "main:listen")
async def callback_listen_fallback(call: CallbackQuery) -> None:
    url = settings.WEBAPP_URL.rstrip("/")
    if url:
        await _safe_edit_text(call.message,
            "<b>🎧 Слушать</b>\n\nАудиокниги открываются во встроенном окне Telegram. Если аудиоглав ещё нет, они появятся здесь после загрузки авторами.",
            reply_markup=back_to_main(),
        )
    else:
        await _safe_edit_text(call.message,"<b>🎧 Слушать</b>\n\nАудиораздел временно недоступен. Попробуйте открыть его позже или напишите в поддержку.", reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data == "main:support")
async def callback_support(call: CallbackQuery) -> None:
    await _safe_edit_text(call.message,
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
    await _safe_edit_text(call.message,
        "<b>⚙️ Настройки</b>\n\n"
        "Здесь можно выбрать тему, размер текста и уведомления. Настройки сохраняются и применяются при чтении.",
        reply_markup=user_settings_menu(prefs),
    )
    await call.answer()


@router.callback_query(F.data == "settings:theme")
async def callback_user_settings_theme(call: CallbackQuery) -> None:
    await _safe_edit_text(call.message,"<b>🎨 Тема</b>\n\nВыберите оформление читалки.", reply_markup=user_theme_menu())
    await call.answer()


@router.callback_query(F.data.startswith("settings:set_theme:"))
async def callback_set_theme(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    theme = call.data.split(":")[-1]
    prefs = await set_user_preference(user_id, "theme", theme)
    await _safe_edit_text(call.message,"<b>⚙️ Настройки</b>\n\nТема сохранена.", reply_markup=user_settings_menu(prefs))
    await call.answer("Сохранено")


@router.callback_query(F.data == "settings:font")
async def callback_user_settings_font(call: CallbackQuery) -> None:
    await _safe_edit_text(call.message,"<b>🔠 Размер шрифта</b>\n\nВыберите размер текста для чтения.", reply_markup=user_font_menu())
    await call.answer()


@router.callback_query(F.data.startswith("settings:set_font:"))
async def callback_set_font(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    font = call.data.split(":")[-1]
    prefs = await set_user_preference(user_id, "font_size", font)
    await _safe_edit_text(call.message,"<b>⚙️ Настройки</b>\n\nРазмер шрифта сохранён.", reply_markup=user_settings_menu(prefs))
    await call.answer("Сохранено")


@router.callback_query(F.data == "settings:notifications")
async def callback_user_notifications(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    prefs = await get_user_preferences(user_id)
    await _safe_edit_text(call.message,
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
    await _safe_edit_text(call.message,
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
    await _safe_edit_text(call.message,
        "<b>🔔 Уведомления</b>\n\nВыбор сохранён.",
        reply_markup=user_notifications_menu(prefs),
    )
    await call.answer("Сохранено")


@router.callback_query(F.data == "settings:reset")
async def callback_reset_settings(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    prefs = await reset_user_preferences(user_id)
    await _safe_edit_text(call.message,"<b>⚙️ Настройки</b>\n\nНастройки сброшены.", reply_markup=user_settings_menu(prefs))
    await call.answer("Сброшено")


@router.callback_query(F.data == "bonus:referral")
async def callback_referral(call: CallbackQuery) -> None:
    _, _, _, user_id = await build_context(call)
    stats = await get_referral_stats(user_id)
    bot_username = (await call.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{call.from_user.id}"
    await _safe_edit_text(call.message,
        "<b>👥 Пригласить друга</b>\n\n"
        f"Ваша ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено: <b>{stats['invited']}</b>\n"
        f"Пополнили баланс: <b>{stats['funded']}</b>\n"
        f"Пополнений рефералов: <b>{stats['topups']}</b>\n"
        f"Заработано: <b>{stats['earned_points']}</b> бонусов.\n\n"
        "За сам вход по ссылке начисления нет. Бонус появляется только после успешного пополнения баланса приглашённым пользователем.",
        reply_markup=bonuses_menu((await load_revenue_split_settings()).topup_packages),
    )
    await call.answer()
