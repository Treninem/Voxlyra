from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.db import (
    add_audit,
    connect,
    get_owner_today_stats,
    get_platform_finance_summary,
    get_user_by_telegram_id,
    get_user_by_username,
    owner_user_search_rows,
    search_users,
    upsert_user,
)
from app.keyboards import owner_menu, owner_search_menu

router = Router()


def _is_owner(user_id: int) -> bool:
    return settings.is_system_owner(int(user_id))


def _deny(call: CallbackQuery) -> bool:
    return False


def _owner_menu_with_catalog() -> InlineKeyboardMarkup:
    base = owner_menu()
    rows = [list(row) for row in base.inline_keyboard]
    rows.append([InlineKeyboardButton(text="📦 Добавить в каталог", callback_data="owner:catalog_recovery")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _owner_home_text() -> str:
    today = await get_owner_today_stats()
    finance = await get_platform_finance_summary()
    return (
        "<b>👑 Центр управления</b>\n\n"
        "<b>Сегодня</b>\n"
        f"👤 Новых читателей: <b>{today['new_users']}</b>\n"
        f"🛍 Покупок: <b>{today['purchases']}</b> · <b>{today['stars']} Stars</b>\n"
        f"📚 Новых книг: <b>{today['new_books']}</b>\n"
        f"💬 Комментариев: <b>{today['comments']}</b> · ⭐ Отзывов: <b>{today['reviews']}</b>\n\n"
        f"🕊 На проверке: <b>{today['books_review']}</b>\n"
        f"🧾 Новых жалоб: <b>{today['complaints']}</b>\n"
        f"💰 Комиссия платформы: <b>{finance['platform_commission']} Stars</b>\n\n"
        "Выберите раздел управления."
    )


async def _catalog_rows(limit: int = 20):
    async with connect() as db:
        cur = await db.execute(
            """SELECT id, title, source_author_name, content_type, publication_status,
                      import_batch_id, has_audio, updated_at
               FROM books
               WHERE publication_status NOT IN ('published','deleted')
               ORDER BY updated_at DESC, id DESC LIMIT ?""",
            (max(1, min(50, int(limit))),),
        )
        return await cur.fetchall()


def _content_label(row) -> str:
    kind = str(row["content_type"] or "book").lower()
    if kind in {"comic", "manga", "manhwa", "webtoon", "graphic_novel"}:
        return "комикс"
    if bool(row["has_audio"]):
        return "аудиокнига"
    return "книга"


async def _show_catalog(call: CallbackQuery, *, notice: str = "") -> None:
    rows = await _catalog_rows()
    if not rows:
        await call.message.edit_text(
            "<b>📦 Восстановление каталога</b>\n\n"
            "Все доступные произведения уже опубликованы.\n"
            "Кнопки восстановления для опубликованных произведений не показываются.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="owner:menu")]]),
        )
        return
    buttons: list[list[InlineKeyboardButton]] = []
    lines = ["<b>📦 Добавить в каталог</b>"]
    if notice:
        lines.append(f"\n{notice}")
    lines.append("\nЗдесь только произведения, которые ещё не доступны всем. Выберите нужное:")
    for row in rows:
        label = str(row["title"] or "Без названия")[:45]
        kind = _content_label(row)
        lines.append(f"\n• <b>{label}</b> · {kind} · ID {int(row['id'])}")
        buttons.append([
            InlineKeyboardButton(
                text=f"➕ {label}",
                callback_data=f"owner:catalog_add:{int(row['id'])}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="owner:catalog_recovery")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="owner:menu")])
    await call.message.edit_text("".join(lines)[:4096], reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(Command("owner"))
async def owner_command(message: Message) -> None:
    if not _is_owner(message.from_user.id):
        return
    await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(await _owner_home_text(), reply_markup=_owner_menu_with_catalog())


@router.callback_query(F.data == "owner:menu")
async def owner_menu(call: CallbackQuery) -> None:
    if not _is_owner(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await call.message.edit_text(await _owner_home_text(), reply_markup=_owner_menu_with_catalog())
    await call.answer()


@router.callback_query(F.data == "owner:catalog_recovery")
async def catalog_recovery(call: CallbackQuery) -> None:
    if not _is_owner(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await _show_catalog(call)
    await call.answer()


@router.callback_query(F.data.startswith("owner:catalog_add:"))
async def catalog_add(call: CallbackQuery) -> None:
    if not _is_owner(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    try:
        book_id = int(call.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        await call.answer("Некорректный ID", show_alert=True)
        return

    owner = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    async with connect() as db:
        cur = await db.execute("SELECT id, title, publication_status FROM books WHERE id=? LIMIT 1", (book_id,))
        book = await cur.fetchone()
        if not book:
            await call.answer("Произведение не найдено", show_alert=True)
            return
        status = str(book["publication_status"] or "draft")
        if status == "published":
            await _show_catalog(call, notice="Это произведение уже доступно всем. Повторно его публиковать нельзя.")
            await call.answer("Уже опубликовано")
            return
        if status == "deleted":
            await call.answer("Произведение удалено", show_alert=True)
            return
        now = __import__("app.db", fromlist=["utc_now"]).utc_now()
        await db.execute("UPDATE books SET publication_status='published', rights_checked=1, updated_at=? WHERE id=?", (now, book_id))
        await db.execute("UPDATE chapters SET status='published', updated_at=? WHERE book_id=? AND status!='deleted'", (now, book_id))
        await db.execute("UPDATE graphic_chapters SET status='published', updated_at=? WHERE book_id=? AND status!='deleted'", (now, book_id))
        await db.execute("UPDATE audio_chapters SET status='published', updated_at=? WHERE book_id=? AND status!='deleted'", (now, book_id))
        await db.commit()
    await add_audit(int(owner["id"]), "catalog_owner_publish", "book", str(book_id), status, "published")
    await _show_catalog(call, notice=f"<b>{book['title']}</b> добавлено в каталог. Прогресс читателей не изменён.")
    await call.answer("Добавлено в каталог")


@router.callback_query(F.data.startswith("owner:user_card:"))
async def owner_user_card(call: CallbackQuery) -> None:
    if not _is_owner(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    try:
        user_id = int(call.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        await call.answer("Некорректный пользователь", show_alert=True)
        return
    rows = await search_users(str(user_id))
    row = next((r for r in rows if int(r["id"]) == user_id), None)
    if not row:
        await call.answer("Пользователь не найден", show_alert=True)
        return

    async with connect() as db:
        cur = await db.execute(
            """SELECT rp.book_id, b.title, MAX(rp.position_percent) AS progress, MAX(rp.updated_at) AS last_read
               FROM reading_progress rp
               JOIN books b ON b.id=rp.book_id
               WHERE rp.user_id=? AND b.publication_status!='deleted'
               GROUP BY rp.book_id, b.title
               ORDER BY last_read DESC LIMIT 8""",
            (user_id,),
        )
        reading = await cur.fetchall()
        cur = await db.execute(
            """SELECT ac.book_id, b.title, SUM(lp.position_seconds) AS seconds, MAX(lp.updated_at) AS last_listen
               FROM listening_progress lp
               JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id
               JOIN books b ON b.id=ac.book_id
               WHERE lp.user_id=? AND b.publication_status!='deleted'
               GROUP BY ac.book_id, b.title
               ORDER BY last_listen DESC LIMIT 8""",
            (user_id,),
        )
        listening = await cur.fetchall()
        cur = await db.execute(
            """SELECT COUNT(*) AS count, COALESCE(SUM(amount_stars),0) AS stars
               FROM purchases WHERE user_id=? AND status='paid'""",
            (user_id,),
        )
        payments = await cur.fetchone()
        cur = await db.execute(
            """SELECT p.created_at, p.amount_stars, b.title
               FROM purchases p LEFT JOIN books b ON b.id=p.book_id
               WHERE p.user_id=? AND p.status='paid'
               ORDER BY p.created_at DESC LIMIT 8""",
            (user_id,),
        )
        purchases = await cur.fetchall()

    lines = [
        "<b>👤 Пользователь</b>",
        f"\nID базы: <b>{row['id']}</b>",
        f"Telegram ID: <b>{row['telegram_id']}</b>",
        f"Username: <b>@{row['username'] or '-'}</b>",
        f"Имя: <b>{row['full_name'] or '-'}</b>",
        f"Псевдоним автора: <b>{row['pen_name'] or '-'}</b>",
        f"Статус: <b>{'заблокирован' if row['is_blocked'] else 'активен'}</b>",
        f"\n💳 Покупок: <b>{int(payments['count'] or 0)}</b> · <b>{int(payments['stars'] or 0)} Stars</b>",
        "\n<b>📖 Что читает:</b>",
    ]
    if reading:
        for item in reading:
            lines.append(f"• {str(item['title'])[:55]} — <b>{int(item['progress'] or 0)}%</b>")
    else:
        lines.append("• пока нет сохранённого прогресса")
    if listening:
        lines.append("\n<b>🎧 Что слушает:</b>")
        for item in listening:
            minutes = int(item["seconds"] or 0) // 60
            lines.append(f"• {str(item['title'])[:55]} — <b>{minutes} мин.</b>")
    lines.append("\n<b>💰 Последние оплаты:</b>")
    if purchases:
        for item in purchases:
            lines.append(f"• {str(item['title'] or 'Книга/глава')[:45]} — <b>{int(item['amount_stars'] or 0)} Stars</b>")
    else:
        lines.append("• оплат пока нет")

    await call.message.edit_text(
        "\n".join(lines)[:4096],
        reply_markup=owner_search_menu(),
    )
    await call.answer()
