from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.config import settings
from app.db import (
    add_audit,
    count_chapters_for_book,
    get_ad_campaign,
    get_admin_permissions,
    get_book,
    get_comment_for_moderation,
    get_review_for_moderation,
    list_active_ad_campaigns,
    list_complaints,
    list_books_for_moderation,
    list_moderation_comments,
    list_moderation_reviews,
    set_ad_campaign_status,
    set_complaint_status,
    set_book_publication_status,
    set_comment_status,
    set_review_status,
    upsert_user,
)
from app.keyboards import ad_moderation_card_menu, back_to_main, complaint_card_menu, complaints_menu, moderation_ads_menu, moderation_book_card_menu, moderation_books_menu, moderation_comments_menu, moderation_content_menu, moderation_hide_menu, moderation_menu, moderation_reviews_menu
from app.services.channel import build_new_book_post

router = Router()


@router.callback_query(F.data == "mod:menu")
async def moderation_main(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    perms = await get_admin_permissions(user["id"])
    if not perms:
        await call.answer("Недоступно", show_alert=True)
        return
    await call.message.edit_text(
        "<b>🛡 Модерация</b>\n\n"
        "Показаны только те разделы, которые разрешил владелец.",
        reply_markup=moderation_menu(perms),
    )
    await call.answer()


async def _require_perm(call: CallbackQuery, code: str) -> bool:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    perms = await get_admin_permissions(user["id"])
    if code not in perms:
        await call.answer("Недоступно", show_alert=True)
        return False
    return True


@router.callback_query(F.data == "mod:books")
async def moderation_books(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_books"):
        return
    books = await list_books_for_moderation()
    if not books:
        await call.message.edit_text("Книг на проверке нет.", reply_markup=moderation_menu({"mod_books"}))
    else:
        await call.message.edit_text("<b>Книги на проверке</b>\n\nВыберите книгу.", reply_markup=moderation_books_menu(books))
    await call.answer()


@router.callback_query(F.data.startswith("mod:book:"))
async def moderation_book_card(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_books"):
        return
    book_id = int(call.data.split(":")[-1])
    book = await get_book(book_id)
    if not book:
        await call.answer("Книга не найдена", show_alert=True)
        return
    chapters_count = await count_chapters_for_book(book_id)
    text = (
        f"<b>{book['title']}</b>\n\n"
        f"Автор: <b>{book['pen_name'] or 'не указан'}</b>\n"
        f"Возраст: <b>{book['age_limit']}</b>\n"
        f"Глав: <b>{chapters_count}</b>\n"
        f"Цена: <b>{book['price_stars']} Stars</b>\n"
        f"Скачивание: <b>{'разрешено' if book['allow_download'] else 'запрещено'}</b>\n\n"
        f"{book['description'] or ''}"
    )
    await call.message.edit_text(text[:4096], reply_markup=moderation_book_card_menu(book_id))
    await call.answer()


@router.callback_query(F.data.startswith("mod:book_publish:"))
async def moderation_book_publish(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_books"):
        return
    book_id = int(call.data.split(":")[-1])
    chapters_count = await count_chapters_for_book(book_id)
    if chapters_count < 1:
        await call.answer("Нельзя публиковать книгу без глав", show_alert=True)
        return
    book = await get_book(book_id)
    await set_book_publication_status(book_id, "published")
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(user["id"], "book_published", "book", str(book_id))

    channel_status = ""
    if settings.CHANNEL_ID and book:
        try:
            post = build_new_book_post(
                title=book["title"],
                genre="не указан",
                age_limit=book["age_limit"],
                chapters_count=chapters_count,
                has_audio=bool(book["has_audio"]),
            )
            await call.bot.send_message(settings.CHANNEL_ID, post)
            channel_status = "\n\nПост отправлен в канал."
            await add_audit(user["id"], "channel_post_sent", "book", str(book_id))
        except Exception as exc:
            channel_status = f"\n\nКнига опубликована, но пост в канал не отправился: {exc}"
            await add_audit(user["id"], "channel_post_failed", "book", str(book_id), None, str(exc))

    await call.message.edit_text(
        "Книга опубликована. Теперь она появится в каталоге." + channel_status,
        reply_markup=back_to_main(),
    )
    await call.answer("Опубликовано")


@router.callback_query(F.data.startswith("mod:book_reject:"))
async def moderation_book_reject(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_books"):
        return
    book_id = int(call.data.split(":")[-1])
    await set_book_publication_status(book_id, "draft")
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(user["id"], "book_rejected", "book", str(book_id))
    await call.message.edit_text("Книга возвращена автору в черновик.", reply_markup=back_to_main())
    await call.answer("Готово")


@router.callback_query(F.data == "mod:comments")
async def moderation_comments_root(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_comments"):
        return
    await call.message.edit_text(
        "<b>💬 Комментарии и отзывы</b>\n\n"
        "Здесь модератор может скрывать комментарии и отзывы. Недоступные действия не показываются.",
        reply_markup=moderation_content_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "mod:content:comments")
async def moderation_comments_list(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_comments"):
        return
    comments = await list_moderation_comments(30)
    if not comments:
        await call.message.edit_text("Опубликованных комментариев пока нет.", reply_markup=moderation_content_menu())
    else:
        await call.message.edit_text("<b>💬 Последние комментарии</b>", reply_markup=moderation_comments_menu(comments))
    await call.answer()


@router.callback_query(F.data.startswith("mod:comment:"))
async def moderation_comment_card(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_comments"):
        return
    comment_id = int(call.data.split(":")[-1])
    row = await get_comment_for_moderation(comment_id)
    if not row:
        await call.answer("Комментарий не найден", show_alert=True)
        return
    who = row["username"] or row["full_name"] or "читатель"
    await call.message.edit_text(
        f"<b>Комментарий #{row['id']}</b>\n\n"
        f"Книга: <b>{row['book_title']}</b>\n"
        f"Глава: <b>{row['chapter_title']}</b>\n"
        f"Автор комментария: <b>{who}</b>\n"
        f"Статус: <b>{row['status']}</b>\n\n"
        f"{row['text'][:3000]}",
        reply_markup=moderation_hide_menu("comment", comment_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mod:comment_hide:"))
async def moderation_comment_hide(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_comments"):
        return
    comment_id = int(call.data.split(":")[-1])
    await set_comment_status(comment_id, "hidden")
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(user["id"], "comment_hidden", "comment", str(comment_id))
    await call.message.edit_text("Комментарий скрыт.", reply_markup=moderation_content_menu())
    await call.answer("Готово")


@router.callback_query(F.data == "mod:content:reviews")
async def moderation_reviews_list(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_comments"):
        return
    reviews = await list_moderation_reviews(30)
    if not reviews:
        await call.message.edit_text("Опубликованных отзывов пока нет.", reply_markup=moderation_content_menu())
    else:
        await call.message.edit_text("<b>⭐ Последние отзывы</b>", reply_markup=moderation_reviews_menu(reviews))
    await call.answer()


@router.callback_query(F.data.startswith("mod:review:"))
async def moderation_review_card(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_comments"):
        return
    review_id = int(call.data.split(":")[-1])
    row = await get_review_for_moderation(review_id)
    if not row:
        await call.answer("Отзыв не найден", show_alert=True)
        return
    who = row["username"] or row["full_name"] or "читатель"
    await call.message.edit_text(
        f"<b>Отзыв #{row['id']}</b>\n\n"
        f"Книга: <b>{row['book_title']}</b>\n"
        f"Автор отзыва: <b>{who}</b>\n"
        f"Оценка: <b>{row['rating']}★</b>\n"
        f"Статус: <b>{row['status']}</b>\n\n"
        f"{(row['text'] or 'Без текста')[:3000]}",
        reply_markup=moderation_hide_menu("review", review_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mod:review_hide:"))
async def moderation_review_hide(call: CallbackQuery) -> None:
    if not await _require_perm(call, "mod_comments"):
        return
    review_id = int(call.data.split(":")[-1])
    await set_review_status(review_id, "hidden")
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(user["id"], "review_hidden", "review", str(review_id))
    await call.message.edit_text("Отзыв скрыт.", reply_markup=moderation_content_menu())
    await call.answer("Готово")


@router.callback_query(F.data == "mod:ads")
async def moderation_ads_list(call: CallbackQuery) -> None:
    if not await _require_perm(call, "ads"):
        return
    campaigns = await list_active_ad_campaigns(30)
    if not campaigns:
        await call.message.edit_text("Активной рекламы пока нет.", reply_markup=moderation_menu({"ads"}))
    else:
        await call.message.edit_text("<b>📢 Активная реклама</b>", reply_markup=moderation_ads_menu(campaigns))
    await call.answer()


@router.callback_query(F.data.startswith("mod:ad:"))
async def moderation_ad_card(call: CallbackQuery) -> None:
    if not await _require_perm(call, "ads"):
        return
    campaign_id = int(call.data.split(":")[-1])
    row = await get_ad_campaign(campaign_id)
    if not row:
        await call.answer("Кампания не найдена", show_alert=True)
        return
    left = max(0, int(row["budget_units"] or 0) - int(row["spent_units"] or 0))
    await call.message.edit_text(
        f"<b>Реклама #{row['id']}</b>\n\n"
        f"Книга: <b>{row['book_title']}</b>\n"
        f"Автор: <b>{row['pen_name']}</b>\n"
        f"Статус: <b>{row['status']}</b>\n"
        f"Место: <b>{row['placement']}</b>\n"
        f"Бюджет: <b>{row['budget_units']}</b>\n"
        f"Потрачено: <b>{row['spent_units']}</b>\n"
        f"Остаток: <b>{left}</b>",
        reply_markup=ad_moderation_card_menu(campaign_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mod:ad_pause:"))
async def moderation_ad_pause(call: CallbackQuery) -> None:
    if not await _require_perm(call, "ads"):
        return
    campaign_id = int(call.data.split(":")[-1])
    await set_ad_campaign_status(campaign_id, "paused")
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(user["id"], "ad_campaign_paused", "ad_campaign", str(campaign_id))
    await call.message.edit_text("Реклама остановлена.", reply_markup=moderation_menu({"ads"}))
    await call.answer("Готово")


@router.callback_query(F.data.startswith("mod:ad_block:"))
async def moderation_ad_block(call: CallbackQuery) -> None:
    if not await _require_perm(call, "ads"):
        return
    campaign_id = int(call.data.split(":")[-1])
    await set_ad_campaign_status(campaign_id, "blocked")
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await add_audit(user["id"], "ad_campaign_blocked", "ad_campaign", str(campaign_id))
    await call.message.edit_text("Реклама заблокирована.", reply_markup=moderation_menu({"ads"}))
    await call.answer("Готово")


@router.callback_query(F.data == "mod:complaints")
async def moderation_complaints_list(call: CallbackQuery) -> None:
    if not await _require_perm(call, "complaints"):
        return
    rows = await list_complaints("new")
    if not rows:
        await call.message.edit_text("Новых жалоб нет.", reply_markup=moderation_menu({"complaints"}))
    else:
        await call.message.edit_text("<b>🧾 Жалобы</b>\n\nВыберите жалобу.", reply_markup=complaints_menu(rows, "modcomplaint"))
    await call.answer()


@router.callback_query(F.data.startswith("modcomplaint:card:"))
async def moderation_complaint_card(call: CallbackQuery) -> None:
    if not await _require_perm(call, "complaints"):
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
        reply_markup=complaint_card_menu(complaint_id, "modcomplaint"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("modcomplaint:close:"))
async def moderation_complaint_close(call: CallbackQuery) -> None:
    if not await _require_perm(call, "complaints"):
        return
    complaint_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await set_complaint_status(complaint_id, "closed", user["id"])
    await add_audit(user["id"], "complaint_closed", "complaint", str(complaint_id))
    await call.message.edit_text("Жалоба закрыта.", reply_markup=moderation_menu({"complaints"}))
    await call.answer("Закрыто")


@router.callback_query(F.data.startswith("modcomplaint:pending:"))
async def moderation_complaint_pending(call: CallbackQuery) -> None:
    if not await _require_perm(call, "complaints"):
        return
    complaint_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await set_complaint_status(complaint_id, "pending", user["id"])
    await add_audit(user["id"], "complaint_pending", "complaint", str(complaint_id))
    await call.message.edit_text("Жалоба оставлена в работе.", reply_markup=moderation_menu({"complaints"}))
    await call.answer("В работе")


@router.callback_query(F.data.startswith("mod:"))
async def moderation_stubs(call: CallbackQuery) -> None:
    titles = {
        "mod:comments": "💬 Комментарии",
        "mod:complaints": "🧾 Жалобы",
        "mod:authors": "✍️ Авторы",
        "mod:users": "👤 Пользователи",
        "mod:block_books": "📕 Блокировка книг",
        "mod:finance": "💰 Финансы",
        "mod:refunds": "↩️ Возвраты",
        "mod:ads": "📢 Реклама",
        "mod:channel": "📣 Канал",
        "mod:stats": "📊 Статистика",
        "mod:support": "🛟 Поддержка",
    }
    await call.message.edit_text(
        f"<b>{titles.get(call.data, 'Раздел')}</b>\n\n"
        "Для этого раздела нет доступных действий в текущих правах или кнопка устарела. Вернитесь в меню модерации и выберите рабочий раздел.",
        reply_markup=back_to_main(),
    )
    await call.answer()
