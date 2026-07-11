from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.build_info import owner_build_label
from app.db import (
    add_admin,
    add_audit,
    get_admin_permissions,
    get_platform_stats,
    get_owner_today_stats,
    get_platform_finance_summary,
    get_reader_ad_settings,
    get_book,
    get_complaint,
    list_complaints,
    search_books,
    search_users,
    set_book_blocked,
    set_complaint_status,
    set_user_blocked,
    get_setting,
    get_user_by_telegram_id,
    get_user_by_username,
    list_admins,
    list_audit,
    list_books_for_moderation,
    remove_admin,
    set_permission,
    set_setting,
    upsert_user,
    list_authors_for_owner,
    list_recent_channel_posts,
    list_recent_channel_promotions,
    get_channel_promotion_price,
    record_owner_channel_promotion,
    list_blocked_users,
)
from app.keyboards import (
    admin_card_menu,
    admins_list_menu,
    admins_menu,
    back_to_main,
    finance_owner_menu,
    payment_systems_owner_menu,
    owner_menu,
    reader_ads_owner_menu,
    owner_search_menu,
    owner_users_search_results_menu,
    owner_user_card_menu,
    owner_books_search_results_menu,
    owner_book_card_menu,
    owner_channel_menu,
    complaints_menu,
    complaint_card_menu,
    navigation_menu,
)
from app.permissions import DELEGABLE_PERMISSION_CODES, PERMISSION_BY_CODE
from app.services.diagnostics import format_diagnostics_for_owner
from app.services.notifications import complaint_message, send_user_notification
from app.services.publication import post_book_to_channel
from app.services.payment_runtime import public_runtime_payment_settings, update_runtime_payment_settings

router = Router()


async def _notify_complaint_owner_action(
    call: CallbackQuery,
    *,
    actor_user_id: int,
    complaint,
    status: str,
) -> None:
    result = await send_user_notification(
        app_user_id=int(complaint["user_id"]) if complaint["user_id"] is not None else None,
        telegram_id=int(complaint["telegram_id"]) if complaint["telegram_id"] is not None else None,
        text=complaint_message(status),
        bot=call.bot,
    )
    await add_audit(
        actor_user_id,
        f"notification_{result}",
        "complaint",
        str(complaint["id"]),
        f"complaint_{status}",
        result,
    )


class AddAdmin(StatesGroup):
    waiting_for_user = State()


class SetCommission(StatesGroup):
    waiting_for_value = State()


class OwnerSearch(StatesGroup):
    user_query = State()
    book_query = State()


class OwnerChannelSettings(StatesGroup):
    price = State()


def is_owner_tg(telegram_id: int) -> bool:
    return telegram_id in settings.owner_ids


async def deny_if_not_owner(call: CallbackQuery) -> bool:
    if not is_owner_tg(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return True
    return False


@router.callback_query(F.data == "owner:menu")
async def owner_menu_handler(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    today = await get_owner_today_stats()
    finance = await get_platform_finance_summary()
    await call.message.edit_text(
        "<b>👑 Центр управления</b>\n\n"
        "<b>Сегодня</b>\n"
        f"👤 Новых читателей: <b>{today['new_users']}</b>\n"
        f"🛍 Покупок: <b>{today['purchases']}</b> · <b>{today['stars']} Stars</b>\n"
        f"📚 Новых книг: <b>{today['new_books']}</b>\n"
        f"💬 Комментариев: <b>{today['comments']}</b> · ⭐ Отзывов: <b>{today['reviews']}</b>\n\n"
        f"🕊 На проверке: <b>{today['books_review']}</b>\n"
        f"🧾 Новых жалоб: <b>{today['complaints']}</b>\n"
        f"💰 Комиссия платформы: <b>{finance['platform_commission']} Stars</b>\n\n"
        f"🔖 Версия сборки: <b>{owner_build_label()}</b>\n\n"
        "Выберите раздел управления.",
        reply_markup=owner_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "owner:admins")
async def owner_admins(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    await call.message.edit_text(
        "<b>👥 Администрация</b>\n\n"
        "Добавляйте людей и выдавайте только нужные права. Недоступные кнопки у них не появятся.",
        reply_markup=admins_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "owner:add_admin")
async def owner_add_admin(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    await state.set_state(AddAdmin.waiting_for_user)
    await call.message.edit_text(
        "Введите Telegram ID или username администратора.\n\n"
        "Важно: username сработает только если человек уже запускал этого бота.",
        reply_markup=navigation_menu(cancel_callback="owner:cancel_input"),
    )
    await call.answer()


@router.message(AddAdmin.waiting_for_user)
async def owner_add_admin_finish(message: Message, state: FSMContext) -> None:
    if not is_owner_tg(message.from_user.id):
        await message.answer("Недоступно")
        await state.clear()
        return

    owner = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    raw = (message.text or "").strip()
    target = None

    if raw.isdigit():
        target = await get_user_by_telegram_id(int(raw))
    else:
        target = await get_user_by_username(raw)

    if target is None:
        await message.answer(
            "Пользователь не найден в базе.\n\n"
            "Пусть он сначала откроет бота и нажмёт /start, потом повторите добавление."
        )
        return

    await add_admin(target["id"], owner["id"])
    await add_audit(owner["id"], "admin_added", "user", str(target["id"]), None, raw)
    await state.clear()
    perms = await get_admin_permissions(target["id"])
    await message.answer(
        "Администратор добавлен. Теперь включите нужные права.\n\n"
        f"Пользователь: <b>{target['full_name'] or target['username'] or target['telegram_id']}</b>",
        reply_markup=admin_card_menu(target["id"], perms),
    )


@router.callback_query(F.data == "owner:list_admins")
async def owner_list_admins(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    admins = await list_admins()
    if not admins:
        await call.message.edit_text("Администрация пока не добавлена.", reply_markup=admins_menu())
    else:
        await call.message.edit_text("<b>Список администрации</b>\n\nВыберите человека для настройки прав.", reply_markup=admins_list_menu(admins))
    await call.answer()


@router.callback_query(F.data.startswith("owner:admin_card:"))
async def owner_admin_card(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    target_user_id = int(call.data.split(":")[-1])
    perms = await get_admin_permissions(target_user_id)
    await call.message.edit_text(
        "<b>Права администратора</b>\n\n"
        "Включайте только то, что человеку действительно нужно. Недоступные разделы у него не появятся.",
        reply_markup=admin_card_menu(target_user_id, perms),
    )
    await call.answer()


@router.callback_query(F.data.startswith("owner:perm:"))
async def owner_toggle_permission(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    _, _, user_id_raw, code = call.data.split(":", 3)
    target_user_id = int(user_id_raw)
    if code not in PERMISSION_BY_CODE:
        await call.answer("Неизвестное право", show_alert=True)
        return
    if code not in DELEGABLE_PERMISSION_CODES:
        await call.answer("Это действие доступно только владельцу", show_alert=True)
        return
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    perms = await get_admin_permissions(target_user_id)
    new_allowed = code not in perms
    await set_permission(target_user_id, code, new_allowed)
    await add_audit(owner["id"], "admin_permission_changed", "user", str(target_user_id), code, str(new_allowed))
    perms = await get_admin_permissions(target_user_id)
    await call.message.edit_reply_markup(reply_markup=admin_card_menu(target_user_id, perms))
    await call.answer("Сохранено")


@router.callback_query(F.data.startswith("owner:remove_admin:"))
async def owner_remove_admin(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    target_user_id = int(call.data.split(":")[-1])
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await remove_admin(target_user_id)
    await add_audit(owner["id"], "admin_removed", "user", str(target_user_id))
    await call.message.edit_text("Доступ администратора убран.", reply_markup=admins_menu())
    await call.answer("Готово")


@router.callback_query(F.data == "owner:finance")
async def owner_finance(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    books = await get_setting("commission_books", "20")
    audio = await get_setting("commission_audio", "20")
    donations = await get_setting("commission_donations", "10")
    await call.message.edit_text(
        "<b>💰 Финансы</b>\n\n"
        f"Комиссия книг: <b>{books}%</b>\n"
        f"Комиссия аудио: <b>{audio}%</b>\n"
        f"Комиссия донатов: <b>{donations}%</b>\n\n"
        "Менять комиссии может только владелец.",
        reply_markup=finance_owner_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "owner:payment_systems")
async def owner_payment_systems(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    cfg = await public_runtime_payment_settings()
    await call.message.edit_text(
        "<b>💳 Оплата и расчёты</b>\n\n"
        f"Telegram Stars: <b>{'включены' if cfg['stars_enabled'] else 'выключены'}</b>\n"
        f"Ориентир для покупателя: <b>{cfg['buyer_star_rate_rubles']:.2f} ₽ за 1 Star</b>\n"
        f"Расчётный курс автора: <b>{cfg['author_star_rate_rubles']:.2f} ₽ за 1 Star</b>\n"
        f"Разница курсов: <b>{cfg['rate_spread_minor'] / 100:.2f} ₽</b>\n"
        f"Защита содержимого: <b>{'включена' if cfg['content_protection_enabled'] else 'выключена'}</b>\n"
        f"Водяной знак: <b>{'включён' if cfg['watermark_enabled'] else 'выключен'}</b>\n\n"
        "ЮKassa и сторонние провайдеры отключены. Все цифровые покупки проходят только в Telegram Stars. "
        "Рублёвый ориентир покупателя не является отдельной оплатой. Курс автора фиксируется для каждой продажи после удержания комиссии платформы.",
        reply_markup=payment_systems_owner_menu(cfg),
    )
    await call.answer()


@router.callback_query(F.data.startswith("owner:payment_toggle:"))
async def owner_payment_toggle(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    key = call.data.split(":", 2)[-1]
    allowed = {"stars_enabled", "content_protection_enabled", "watermark_enabled"}
    if key not in allowed:
        await call.answer("Эта платёжная система отключена", show_alert=True)
        return
    current = await public_runtime_payment_settings()
    updated = await update_runtime_payment_settings({key: not bool(current.get(key))})
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(owner["id"], "payment_setting_changed", "setting", key, str(current.get(key)), str(updated.get(key)))
    await call.message.edit_reply_markup(reply_markup=payment_systems_owner_menu(updated))
    await call.answer("Сохранено")


@router.callback_query(F.data.startswith("owner:set_commission:"))
async def owner_set_commission_start(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    key = call.data.split(":")[-1]
    await state.update_data(setting_key=key)
    await state.set_state(SetCommission.waiting_for_value)
    current = await get_setting(key, "20")
    await call.message.edit_text(
        f"Текущее значение: <b>{current}%</b>\n\n"
        "Введите новое значение комиссии числом от 0 до 50.",
        reply_markup=navigation_menu(cancel_callback="owner:cancel_input"),
    )
    await call.answer()


@router.message(SetCommission.waiting_for_value)
async def owner_set_commission_finish(message: Message, state: FSMContext) -> None:
    if not is_owner_tg(message.from_user.id):
        await message.answer("Недоступно")
        await state.clear()
        return
    raw = (message.text or "").strip().replace("%", "")
    if not raw.isdigit():
        await message.answer("Введите число. Например: 20")
        return
    value = int(raw)
    if value < 0 or value > 50:
        await message.answer("Комиссия должна быть от 0 до 50%.")
        return
    data = await state.get_data()
    key = data["setting_key"]
    old = await get_setting(key, "")
    await set_setting(key, str(value))
    owner = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await add_audit(owner["id"], "commission_changed", "setting", key, old, str(value))
    await state.clear()
    await message.answer("Комиссия сохранена.", reply_markup=finance_owner_menu())


@router.callback_query(F.data == "owner:stats")
async def owner_stats(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    stats = await get_platform_stats()
    await call.message.edit_text(
        "<b>📊 Статистика</b>\n\n"
        f"Пользователи: <b>{stats['users']}</b>\n"
        f"Авторы: <b>{stats['authors']}</b>\n"
        f"Книги: <b>{stats['books']}</b>\n"
        f"На проверке: <b>{stats['books_review']}</b>\n"
        f"Опубликовано: <b>{stats['books_published']}</b>\n"
        f"Главы: <b>{stats['chapters']}</b>\n"
        f"Аудио: <b>{stats['audio']}</b>\n"
        f"Новые жалобы: <b>{stats['complaints']}</b>",
        reply_markup=back_to_main(),
    )
    await call.answer()


@router.callback_query(F.data == "owner:audit")
async def owner_audit(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    logs = await list_audit(15)
    if not logs:
        text = "Журнал пока пуст."
    else:
        lines = ["<b>📝 Журнал действий</b>\n"]
        for row in logs:
            actor = row["full_name"] or row["username"] or row["telegram_id"] or "система"
            lines.append(f"• {row['action']} · {actor} · {row['created_at'][:16]}")
        text = "\n".join(lines)
    await call.message.edit_text(text[:4096], reply_markup=admins_menu())
    await call.answer()


@router.callback_query(F.data == "owner:reader_ads")
async def owner_reader_ads(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    ads = await get_reader_ad_settings()
    await call.message.edit_text(
        "<b>📖 Реклама в читалке</b>\n\n"
        "Здесь включается нативная реклама похожих книг внутри чтения главы. "
        "Подбор идёт по жанрам, сюжетным тегам и аудитории, которые автор отметил галочками при создании книги.\n\n"
        f"Название блока: <b>{ads.get('label')}</b>",
        reply_markup=reader_ads_owner_menu(ads),
    )
    await call.answer()


@router.callback_query(F.data.startswith("owner:reader_ads_toggle:"))
async def owner_reader_ads_toggle(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    key = call.data.split(":")[-1]
    if key not in {"reader_ads_enabled", "reader_ads_top", "reader_ads_bottom"}:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    old = await get_setting(key, "1")
    new_value = "0" if old != "0" else "1"
    await set_setting(key, new_value)
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(owner["id"], "reader_ads_setting_changed", "setting", key, old, new_value)
    ads = await get_reader_ad_settings()
    await call.message.edit_reply_markup(reply_markup=reader_ads_owner_menu(ads))
    await call.answer("Сохранено")


@router.callback_query(F.data == "owner:books")
async def owner_books(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    await state.set_state(OwnerSearch.book_query)
    review_books = await list_books_for_moderation()
    await call.message.edit_text(
        "<b>📚 Книги</b>\n\n"
        f"На проверке: <b>{len(review_books)}</b>\n\n"
        "Введите название книги, часть описания или псевдоним автора для поиска.",
        reply_markup=navigation_menu(cancel_callback="owner:cancel_search"),
    )
    await call.answer()


@router.callback_query(F.data == "owner:settings")
async def owner_settings(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="📖 Реклама в читалке", callback_data="owner:reader_ads")
    kb.button(text="⬅️ Назад", callback_data="owner:menu")
    kb.adjust(1)
    await call.message.edit_text(
        "<b>⚙️ Настройки платформы</b>\n\n"
        "Здесь находятся скрытые настройки, которые видит только владелец.",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "owner:users")
async def owner_users(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    await state.set_state(OwnerSearch.user_query)
    await call.message.edit_text(
        "<b>👤 Пользователи и авторы</b>\n\n"
        "Введите Telegram ID, username, имя или псевдоним автора для поиска.",
        reply_markup=navigation_menu(cancel_callback="owner:cancel_search"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("owner:user_card:"))
async def owner_user_card(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    user_id = int(call.data.split(":")[-1])
    rows = await search_users(str(user_id))
    row = next((r for r in rows if int(r["id"]) == user_id), None)
    if not row:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    await call.message.edit_text(
        f"<b>👤 Пользователь</b>\n\n"
        f"ID базы: <b>{row['id']}</b>\n"
        f"Telegram ID: <b>{row['telegram_id']}</b>\n"
        f"Username: <b>@{row['username'] or '-'}</b>\n"
        f"Имя: <b>{row['full_name'] or '-'}</b>\n"
        f"Псевдоним автора: <b>{row['pen_name'] or '-'}</b>\n"
        f"Блокировка: <b>{'да' if row['is_blocked'] else 'нет'}</b>",
        reply_markup=owner_user_card_menu(user_id, bool(row["is_blocked"])),
    )
    await call.answer()


@router.callback_query(F.data.startswith("owner:user_block:"))
async def owner_user_block(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    _, _, _, user_id_raw, blocked_raw = call.data.split(":")
    user_id = int(user_id_raw); blocked = bool(int(blocked_raw))
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await set_user_blocked(user_id, blocked)
    await add_audit(owner["id"], "user_block_changed", "user", str(user_id), None, str(blocked))
    await call.message.edit_text("Пользователь заблокирован." if blocked else "Пользователь разблокирован.", reply_markup=owner_search_menu())
    await call.answer("Сохранено")


@router.callback_query(F.data.startswith("owner:book_card:"))
async def owner_book_card(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    book_id = int(call.data.split(":")[-1])
    book = await get_book(book_id)
    if not book:
        await call.answer("Книга не найдена", show_alert=True)
        return
    await call.message.edit_text(
        f"<b>📚 Книга</b>\n\n"
        f"Название: <b>{book['title']}</b>\n"
        f"Автор: <b>{book['pen_name'] or '-'}</b>\n"
        f"Возраст: <b>{book['age_limit']}</b>\n"
        f"Статус: <b>{book['publication_status']}</b>",
        reply_markup=owner_book_card_menu(book_id, book["publication_status"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("owner:book_block:"))
async def owner_book_block(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    _, _, _, book_id_raw, blocked_raw = call.data.split(":")
    book_id = int(book_id_raw); blocked = bool(int(blocked_raw))
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await set_book_blocked(book_id, blocked)
    await add_audit(owner["id"], "book_block_changed", "book", str(book_id), None, str(blocked))
    await call.message.edit_text("Книга заблокирована." if blocked else "Книга переведена в скрытые.", reply_markup=owner_search_menu())
    await call.answer("Сохранено")


@router.callback_query(F.data == "owner:complaints")
async def owner_complaints(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    rows = await list_complaints("new")
    if not rows:
        await call.message.edit_text("Новых жалоб нет.", reply_markup=owner_menu())
    else:
        await call.message.edit_text("<b>🧾 Жалобы</b>\n\nВыберите жалобу.", reply_markup=complaints_menu(rows, "ownercomplaint"))
    await call.answer()


@router.callback_query(F.data.startswith("ownercomplaint:card:"))
async def owner_complaint_card(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    complaint_id = int(call.data.split(":")[-1])
    rows = await list_complaints("new", 100)
    row = next((r for r in rows if int(r["id"]) == complaint_id), None)
    if not row:
        await call.answer("Жалоба не найдена", show_alert=True)
        return
    who = row["username"] or row["full_name"] or row["telegram_id"] or "неизвестно"
    await call.message.edit_text(
        f"<b>Жалоба #{row['id']}</b>\n\n"
        f"От: <b>{who}</b>\n"
        f"Цель: <b>{row['target_type']} #{row['target_id']}</b>\n"
        f"Причина:\n{row['reason']}",
        reply_markup=complaint_card_menu(complaint_id, "ownercomplaint"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ownercomplaint:close:"))
async def owner_complaint_close(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    complaint_id = int(call.data.split(":")[-1])
    complaint = await get_complaint(complaint_id)
    if not complaint or complaint["status"] not in {"new", "pending"}:
        await call.answer("Жалоба уже обработана или не найдена", show_alert=True)
        return
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await set_complaint_status(complaint_id, "closed", owner["id"])
    await add_audit(owner["id"], "complaint_closed", "complaint", str(complaint_id))
    await _notify_complaint_owner_action(
        call,
        actor_user_id=int(owner["id"]),
        complaint=complaint,
        status="closed",
    )
    await call.message.edit_text("Жалоба закрыта.", reply_markup=owner_menu())
    await call.answer("Закрыто")


@router.callback_query(F.data.startswith("ownercomplaint:pending:"))
async def owner_complaint_pending(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    complaint_id = int(call.data.split(":")[-1])
    complaint = await get_complaint(complaint_id)
    if not complaint or complaint["status"] != "new":
        await call.answer("Жалоба уже обработана или не найдена", show_alert=True)
        return
    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await set_complaint_status(complaint_id, "pending", owner["id"])
    await add_audit(owner["id"], "complaint_pending", "complaint", str(complaint_id))
    await _notify_complaint_owner_action(
        call,
        actor_user_id=int(owner["id"]),
        complaint=complaint,
        status="pending",
    )
    await call.message.edit_text("Жалоба оставлена в работе.", reply_markup=owner_menu())
    await call.answer("В работе")


@router.callback_query(F.data == "owner:search_user")
async def owner_search_user_start(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    await state.set_state(OwnerSearch.user_query)
    await call.message.edit_text(
        "<b>👤 Поиск пользователя или автора</b>\n\n"
        "Введите Telegram ID, username, имя или псевдоним автора.",
        reply_markup=navigation_menu(cancel_callback="owner:cancel_search"),
    )
    await call.answer()


@router.message(OwnerSearch.user_query)
async def owner_search_user_finish(message: Message, state: FSMContext) -> None:
    if not is_owner_tg(message.from_user.id):
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите хотя бы два символа.")
        return
    rows = await search_users(query, limit=20)
    await state.clear()
    if not rows:
        await message.answer(
            "Ничего не найдено. Проверьте написание или попробуйте Telegram ID.",
            reply_markup=owner_search_menu(),
        )
        return
    await message.answer(
        f"<b>Результаты поиска</b>\n\nНайдено: <b>{len(rows)}</b>",
        reply_markup=owner_users_search_results_menu(rows),
    )


@router.callback_query(F.data == "owner:search_book")
async def owner_search_book_start(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    await state.set_state(OwnerSearch.book_query)
    await call.message.edit_text(
        "<b>📚 Поиск книги</b>\n\nВведите название книги или псевдоним автора.",
        reply_markup=navigation_menu(cancel_callback="owner:cancel_search"),
    )
    await call.answer()


@router.message(OwnerSearch.book_query)
async def owner_search_book_finish(message: Message, state: FSMContext) -> None:
    if not is_owner_tg(message.from_user.id):
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите хотя бы два символа.")
        return
    rows = await search_books(query, limit=20)
    await state.clear()
    if not rows:
        await message.answer(
            "Книг по этому запросу не найдено.",
            reply_markup=owner_search_menu(),
        )
        return
    await message.answer(
        f"<b>Найденные книги</b>\n\nРезультатов: <b>{len(rows)}</b>",
        reply_markup=owner_books_search_results_menu(rows),
    )


@router.callback_query(F.data == "owner:cancel_search")
async def owner_cancel_search(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    await state.clear()
    await call.message.edit_text(
        "<b>👑 Центр управления</b>\n\nПоиск отменён. Выберите нужный раздел.",
        reply_markup=owner_menu(),
    )
    await call.answer("Отменено")


@router.callback_query(F.data == "owner:cancel_input")
async def owner_cancel_input(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    await state.clear()
    await call.message.edit_text(
        "Изменение отменено.",
        reply_markup=owner_menu(),
    )
    await call.answer("Отменено")


@router.callback_query(F.data == "owner:system")
async def owner_system(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    await call.message.edit_text(format_diagnostics_for_owner(), reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data == "owner:authors")
async def owner_authors(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    rows = await list_authors_for_owner(limit=20)
    if not rows:
        text = "<b>✍️ Авторы</b>\n\nАвторов пока нет."
    else:
        lines = ["<b>✍️ Авторы</b>\n"]
        for row in rows:
            lines.append(
                f"• <b>{row['pen_name']}</b> · книг: {row['books_count']} · статус: {row['status']} · @{row['username'] or 'без username'}"
            )
        text = "\n".join(lines)
    await call.message.edit_text(text[:4096], reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data == "owner:channel")
async def owner_channel(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    posts = await list_recent_channel_posts(limit=8)
    promotions = await list_recent_channel_promotions(limit=8)
    channel_name = settings.CHANNEL_ID or "канал не выбран"
    price = await get_channel_promotion_price()
    lines = [
        "<b>📢 Канал и продвижение</b>",
        "",
        f"Подключённый канал: <b>{channel_name}</b>",
        f"Платное размещение: <b>{price} Stars</b>",
        "Ограничение для пользователей: одна публикация одной книги раз в 30 дней.",
        "",
        "Владелец может повторно разместить опубликованную книгу бесплатно из её карточки.",
    ]
    if promotions:
        lines.append("\n<b>Последние повторные размещения:</b>")
        labels = {"sent": "опубликовано", "failed": "ошибка", "paid": "оплачено", "invoice": "ожидает оплаты"}
        for row in promotions:
            source = "владелец" if row["source"] == "owner" else "платно"
            status = labels.get(row["status"], row["status"])
            lines.append(f"• {row['book_title']} · {source} · {status}")
    elif posts:
        lines.append("\n<b>Последние автоматические публикации:</b>")
        for row in posts:
            lines.append(f"• Книга №{row['book_id']} · {row['status']} · {row['created_at'][:16]}")
    else:
        lines.append("\nПубликаций пока нет.")
    await call.message.edit_text("\n".join(lines)[:4096], reply_markup=owner_channel_menu())
    await call.answer()


@router.callback_query(F.data.startswith("owner:channel_repost:"))
async def owner_channel_repost(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    book_id = int(call.data.split(":")[-1])
    book = await get_book(book_id)
    if not book or book["publication_status"] != "published":
        await call.answer("Повторно публиковать можно только опубликованную книгу", show_alert=True)
        return
    actor = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    result = await post_book_to_channel(
        call.bot, book_id, actor_user_id=int(actor["id"]), force=True
    )
    sent = result.channel_status == "sent"
    await record_owner_channel_promotion(
        book_id, int(actor["id"]), sent=sent, error=result.channel_error
    )
    await call.message.edit_text(
        "Книга повторно опубликована в канале." if sent else f"Не удалось отправить пост. {result.channel_message}",
        reply_markup=owner_book_card_menu(book_id, book["publication_status"]),
    )
    await call.answer("Опубликовано" if sent else "Ошибка", show_alert=not sent)


@router.callback_query(F.data == "owner:channel_price")
async def owner_channel_price_start(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_not_owner(call):
        return
    current = await get_channel_promotion_price()
    await state.set_state(OwnerChannelSettings.price)
    await call.message.edit_text(
        f"Текущая цена платного размещения: <b>{current} Stars</b>.\n\nВведите новую цену числом от 1 до 100000.",
        reply_markup=navigation_menu(cancel_callback="owner:cancel_input"),
    )
    await call.answer()


@router.message(OwnerChannelSettings.price)
async def owner_channel_price_save(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.owner_ids:
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 100000:
        await message.answer("Введите число от 1 до 100000.")
        return
    await set_setting("channel_promotion_price_stars", str(int(raw)))
    await state.clear()
    await message.answer(
        f"Цена платного размещения сохранена: <b>{int(raw)} Stars</b>.",
        reply_markup=owner_channel_menu(),
    )


@router.callback_query(F.data == "owner:security")
async def owner_security(call: CallbackQuery) -> None:
    if await deny_if_not_owner(call):
        return
    rows = await list_blocked_users(limit=15)
    if not rows:
        blocked = "Заблокированных пользователей нет."
    else:
        blocked = "\n".join(f"• {r['username'] or r['full_name'] or r['telegram_id']}" for r in rows)
    await call.message.edit_text(
        "<b>🛡 Безопасность</b>\n\n"
        "Работает журнал действий, блокировка пользователей/книг, заморозка выплат и разграничение прав модераторов.\n\n"
        f"<b>Заблокированные:</b>\n{blocked}",
        reply_markup=back_to_main(),
    )
    await call.answer()
