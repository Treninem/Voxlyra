from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.config import settings
from app.db import get_admin_permissions, upsert_user
from app.keyboards import (
    library_batch_details_menu,
    library_batch_menu,
    library_channel_schedule_menu,
    library_book_list_menu,
    library_duplicate_menu,
    library_manager_menu,
    library_publish_confirm_menu,
    library_rollback_confirm_menu,
    library_settings_menu,
    navigation_menu,
)
from app.services.author_channel_queue import (
    get_author_channel_status, retry_failed_author_posts, update_author_channel_settings,
)
from app.services.library_manager import (
    audit_batch_publication,
    build_batch_report,
    count_library_books,
    export_library_zip,
    get_batch,
    get_channel_schedule_status,
    get_import_settings,
    import_library_zip,
    list_batch_duplicates,
    list_batches,
    list_imported_books,
    publish_batch,
    resolve_duplicate,
    retry_failed_channel_posts,
    rollback_batch_drafts,
    update_import_settings,
)

router = Router()


class LibraryImportFlow(StatesGroup):
    waiting_zip = State()
    waiting_setting_value = State()


async def _allowed(telegram_id: int) -> bool:
    if telegram_id in settings.owner_ids:
        return True
    user = await upsert_user(telegram_id, None, None)
    return "library_import_manage" in await get_admin_permissions(int(user["id"]))


async def _deny(call: CallbackQuery) -> bool:
    if not await _allowed(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return True
    return False


async def _safe_edit(call: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@router.callback_query(F.data == "library:menu")
async def library_menu_handler(call: CallbackQuery) -> None:
    if await _deny(call): return
    total = await count_library_books()
    drafts = await count_library_books("draft")
    published = await count_library_books("published")
    await _safe_edit(
        call,
        "<b>📚 Управление библиотекой</b>\n\n"
        f"Всего импортировано: <b>{total}</b>\n"
        f"Ожидают проверки: <b>{drafts}</b>\n"
        f"Опубликовано: <b>{published}</b>",
        library_manager_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "library:import")
async def library_import_start(call: CallbackQuery, state: FSMContext) -> None:
    if await _deny(call): return
    cfg = await get_import_settings()
    await state.set_state(LibraryImportFlow.waiting_zip)
    await _safe_edit(
        call,
        "<b>📥 Импорт книг</b>\n\n"
        "Отправьте ZIP со структурой <code>Books/001/...</code>.\n"
        f"Количество книг: <b>{'без ограничения' if int(cfg['max_books']) == 0 else str(cfg['max_books'])}</b>, архив до <b>{cfg['max_archive_mb']} МБ</b>.\n\n"
        "Книги сохраняются как черновики. Дубли обрабатываются по выбранной политике.",
        navigation_menu(cancel_callback="library:menu"),
    )
    await call.answer()


@router.message(LibraryImportFlow.waiting_zip, F.document)
async def library_import_receive(message: Message, state: FSMContext) -> None:
    if not await _allowed(message.from_user.id):
        await state.clear(); return
    document = message.document
    filename = document.file_name or "library.zip"
    if not filename.lower().endswith(".zip"):
        await message.answer("Нужен ZIP-архив.", reply_markup=navigation_menu(cancel_callback="library:menu")); return
    cfg = await get_import_settings()
    max_mb = int(cfg["max_archive_mb"])
    if int(document.file_size or 0) > max_mb * 1024 * 1024:
        await message.answer(f"Архив больше {max_mb} МБ. Разделите библиотеку на несколько пакетов."); return
    actor = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    progress = await message.answer("⏳ Архив загружается, проверяется и импортируется…")
    last_progress = {"processed": -1, "phase": -1}

    async def update_progress(data: dict[str, int]) -> None:
        processed = int(data.get("processed", 0))
        total = int(data.get("total", 0))
        phase = int(data.get("phase", 0))
        # Не редактируем сообщение на каждой книге: Telegram может ограничить частые изменения.
        step = 1 if total <= 20 else max(5, total // 20)
        if phase == last_progress["phase"] and processed not in {0, total} and processed - last_progress["processed"] < step:
            return
        last_progress.update(processed=processed, phase=phase)
        phase_text = {1: "Проверяю структуру", 2: "Импортирую книги", 3: "Завершаю пакет"}.get(phase, "Обрабатываю архив")
        percent = int(processed * 100 / total) if total else 0
        text = (
            f"<b>⏳ {phase_text}</b>\n\n"
            f"Обработано: <b>{processed} из {total}</b> ({percent}%)\n"
            f"Добавлено: <b>{data.get('added', 0)}</b>\n"
            f"Дублей: <b>{data.get('duplicates', 0)}</b>\n"
            f"Ошибок: <b>{data.get('errors', 0)}</b>"
        )
        try:
            await progress.edit_text(text)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise

    try:
        with tempfile.TemporaryDirectory(prefix="voxlyra_upload_") as temp_name:
            zip_path = Path(temp_name) / "library.zip"
            await message.bot.download(document, destination=zip_path)
            result = await import_library_zip(
                zip_path,
                filename,
                int(actor["id"]),
                progress_callback=update_progress,
            )
    except ValueError as exc:
        await state.clear()
        await progress.edit_text(
            f"<b>❌ Импорт не начат</b>\n\n{html.escape(str(exc))}",
            reply_markup=navigation_menu(cancel_callback="library:menu"),
        )
        return
    await state.clear()
    lines = [
        "<b>✅ Импорт завершён</b>", "",
        f"📚 Добавлено: <b>{result.added}</b>",
        f"⚠️ Найдено дублей: <b>{result.duplicates}</b>",
        f"❌ Ошибок: <b>{len(result.errors)}</b>", "",
        "Новые книги сохранены как черновики.",
    ]
    if result.duplicate_ids:
        lines.append("Дубли ожидают решения: пропустить или заменить существующую книгу.")
    if result.errors:
        lines.extend(["", "<b>Первые ошибки:</b>"])
        for item in result.errors[:6]:
            lines.append(f"• {html.escape(item.title)} ({html.escape(item.folder)}): {html.escape('; '.join(item.reasons))}")
        if len(result.errors) > 6: lines.append(f"…ещё {len(result.errors)-6}")
    await progress.edit_text("\n".join(lines), reply_markup=library_batch_menu(result.batch_id, bool(result.duplicate_ids)))


@router.message(LibraryImportFlow.waiting_zip)
async def library_import_wrong(message: Message) -> None:
    await message.answer("Отправьте ZIP-файл как документ.", reply_markup=navigation_menu(cancel_callback="library:menu"))


@router.callback_query(F.data.startswith("library:audit:"))
async def library_batch_audit(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    audit = await audit_batch_publication(batch_id)
    lines = [
        f"<b>🔎 Проверка готовности пакета #{batch_id}</b>", "",
        f"Черновиков: <b>{audit['total']}</b>",
        f"Готовы к публикации: <b>{audit['ready']}</b>",
        f"Требуют исправления: <b>{audit['blocked']}</b>",
        f"Средняя оценка качества: <b>{audit.get('average_score', 0)}%</b>",
        f"Предупреждений: <b>{audit.get('warning_count', 0)}</b>",
    ]
    if audit["blocked_items"]:
        lines.extend(["", "<b>Найденные проблемы:</b>"])
        for item in audit["blocked_items"][:10]:
            lines.append(
                f"• #{item['book_id']} {html.escape(item['title'])}: "
                f"{html.escape('; '.join(item['reasons']))}" + (f" · ⚠ {html.escape('; '.join(item.get('warnings') or []))}" if item.get('warnings') else "") + f" · качество {item.get('quality_score', 0)}%"
            )
        if len(audit["blocked_items"]) > 10:
            lines.append(f"…ещё {len(audit['blocked_items']) - 10}")
    else:
        lines.extend(["", "✅ Все черновики пакета прошли обязательную проверку."])
    await _safe_edit(call, "\n".join(lines), library_batch_details_menu(batch_id))
    await call.answer()


@router.callback_query(F.data.startswith("library:publish_confirm:"))
async def library_publish_confirm(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    batch = await get_batch(batch_id)
    if not batch:
        await call.answer("Пакет не найден", show_alert=True); return
    pending_duplicates = await list_batch_duplicates(batch_id)
    audit = await audit_batch_publication(batch_id)
    warning = ""
    if pending_duplicates:
        warning = f"\n⚠️ Неразобранных дублей: <b>{len(pending_duplicates)}</b>. Они не будут опубликованы."
    blocked_preview = ""
    if audit["blocked_items"]:
        lines = ["", "<b>Не пройдут проверку:</b>"]
        for item in audit["blocked_items"][:5]:
            lines.append(
                f"• #{item['book_id']} {html.escape(item['title'])}: "
                f"{html.escape('; '.join(item['reasons']))}" + (f" · ⚠ {html.escape('; '.join(item.get('warnings') or []))}" if item.get('warnings') else "") + f" · качество {item.get('quality_score', 0)}%"
            )
        if len(audit["blocked_items"]) > 5:
            lines.append(f"…ещё {len(audit['blocked_items']) - 5}")
        blocked_preview = "\n".join(lines)
    await _safe_edit(
        call,
        "<b>Подтвердите массовую публикацию</b>\n\n"
        f"Черновиков в пакете: <b>{audit['total']}</b>\n"
        f"Готовы к публикации: <b>{audit['ready']}</b>\n"
        f"Заблокированы проверкой: <b>{audit['blocked']}</b>\n"
        f"Средняя оценка качества: <b>{audit.get('average_score', 0)}%</b>"
        f"{warning}{blocked_preview}\n\n"
        "Опубликованы будут только книги с подтверждёнными правами, допустимой лицензией, "
        "обложкой, описанием, жанром и текстом.",
        library_publish_confirm_menu(batch_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("library:publish:"))
async def library_publish(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    result = await publish_batch(batch_id)
    blocked_preview = ""
    if result.get("blocked_items"):
        lines = ["", "<b>Причины блокировки:</b>"]
        for item in result["blocked_items"][:5]:
            lines.append(
                f"• #{item['book_id']} {html.escape(item['title'])}: "
                f"{html.escape('; '.join(item['reasons']))}" + (f" · ⚠ {html.escape('; '.join(item.get('warnings') or []))}" if item.get('warnings') else "") + f" · качество {item.get('quality_score', 0)}%"
            )
        if len(result["blocked_items"]) > 5:
            lines.append(f"…ещё {len(result['blocked_items']) - 5}")
        blocked_preview = "\n".join(lines)
    await _safe_edit(
        call,
        "<b>✅ Массовая публикация завершена</b>\n\n"
        f"Опубликовано в каталоге: <b>{result['published']}</b>\n"
        f"Поставлено в очередь канала: <b>{result.get('queued', 0)}</b>\n"
        f"Заблокировано проверкой: <b>{result['skipped']}</b>"
        f"{blocked_preview}",
        library_batch_details_menu(batch_id),
    )
    await call.answer()


@router.callback_query(F.data == "library:batches")
async def library_batches(call: CallbackQuery) -> None:
    if await _deny(call): return
    batches = await list_batches()
    if not batches:
        text = "<b>🗂 История импортов</b>\n\nИмпортов пока нет."
    else:
        lines = ["<b>🗂 История импортов</b>", ""]
        for row in batches[:15]:
            lines.append(
                f"<b>#{row['id']}</b> · {html.escape(str(row['archive_name']))}\n"
                f"добавлено {row['imported_count']}, дублей {row['duplicate_count']}, ошибок {row['error_count']}"
            )
        text = "\n\n".join(lines)
    await _safe_edit(call, text, library_manager_menu())
    await call.answer()


@router.callback_query(F.data.startswith("library:batch:"))
async def library_batch_details(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    row = await get_batch(batch_id)
    if not row:
        await call.answer("Пакет не найден", show_alert=True); return
    pending = await list_batch_duplicates(batch_id)
    errors = json.loads(str(row["errors_json"] or "[]"))
    lines = [
        f"<b>📦 Импорт #{batch_id}</b>", "",
        f"Архив: <b>{html.escape(str(row['archive_name']))}</b>",
        f"Найдено папок: <b>{row['total_found']}</b>",
        f"Добавлено: <b>{row['imported_count']}</b>",
        f"Дублей: <b>{row['duplicate_count']}</b>",
        f"Ошибок: <b>{row['error_count']}</b>",
        f"Дублей без решения: <b>{len(pending)}</b>",
    ]
    if errors:
        lines.extend(["", "<b>Первые ошибки:</b>"])
        for item in errors[:5]:
            lines.append(f"• {html.escape(str(item.get('title') or 'Без названия'))}: {html.escape('; '.join(item.get('reasons') or []))}")
    await _safe_edit(call, "\n".join(lines), library_batch_details_menu(batch_id, bool(pending)))
    await call.answer()


@router.callback_query(F.data.startswith("library:duplicates:"))
async def library_duplicates(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    rows = await list_batch_duplicates(batch_id)
    if not rows:
        await _safe_edit(call, "<b>⚠️ Дубли</b>\n\nВсе дубли этого пакета уже обработаны.", library_batch_details_menu(batch_id))
    else:
        row = rows[0]
        await _safe_edit(
            call,
            "<b>⚠️ Найден дубль</b>\n\n"
            f"Книга: <b>{html.escape(str(row['title']))}</b>\n"
            f"Автор: <b>{html.escape(str(row['author']))}</b>\n"
            f"Существующая книга: <b>ID {row['existing_book_id']}</b>\n\n"
            "«Заменить» сохранит ID книги и покупки, но заменит файл, главы, обложку и метаданные.",
            library_duplicate_menu(int(row["id"]), batch_id, len(rows)),
        )
    await call.answer()


@router.callback_query(F.data.startswith("library:duplicate_action:"))
async def library_duplicate_action(call: CallbackQuery) -> None:
    if await _deny(call): return
    _, _, _, duplicate_id, action, batch_id = call.data.split(":")
    try:
        await resolve_duplicate(int(duplicate_id), action)
    except Exception as exc:
        await call.answer(f"Не удалось обработать дубль: {exc}", show_alert=True); return
    await call.answer("Книга заменена" if action == "replace" else "Дубль пропущен")
    rows = await list_batch_duplicates(int(batch_id))
    if rows:
        row = rows[0]
        await _safe_edit(
            call,
            "<b>⚠️ Следующий дубль</b>\n\n"
            f"Книга: <b>{html.escape(str(row['title']))}</b>\n"
            f"Автор: <b>{html.escape(str(row['author']))}</b>\n"
            f"Существующая книга: <b>ID {row['existing_book_id']}</b>",
            library_duplicate_menu(int(row["id"]), int(batch_id), len(rows)),
        )
    else:
        await _safe_edit(call, "<b>✅ Все дубли обработаны</b>", library_batch_details_menu(int(batch_id)))


@router.callback_query(F.data == "library:export")
async def library_export(call: CallbackQuery) -> None:
    if await _deny(call): return
    await call.answer("Готовлю архив")
    with tempfile.TemporaryDirectory(prefix="voxlyra_export_send_") as temp_name:
        output = Path(temp_name) / "VoxLyra_Library_Export.zip"
        exported = await export_library_zip(output)
        await call.message.answer_document(FSInputFile(output, filename="VoxLyra_Library_Export.zip"), caption=f"📦 Экспортировано книг: <b>{exported}</b>")


@router.callback_query(F.data.startswith("library:list:"))
async def library_list(call: CallbackQuery) -> None:
    if await _deny(call): return
    _, _, kind, page_raw = call.data.split(":")
    page = max(0, int(page_raw)); limit = 10
    status = None if kind == "all" else ("draft" if kind == "drafts" else "published")
    total = await count_library_books(status)
    rows = await list_imported_books(status, limit=limit, offset=page*limit)
    title = {"all": "Все импортированные книги", "drafts": "Книги на проверке", "published": "Опубликованные книги"}[kind]
    lines = [f"<b>📖 {title}</b>", "", f"Всего: <b>{total}</b>"]
    for row in rows:
        lines.append(f"\n<b>#{row['id']} · {html.escape(str(row['title']))}</b>\n{html.escape(str(row['source_author_name'] or 'Автор не указан'))} · {row['chapters_count']} глав · {row['publication_status']}")
    await _safe_edit(call, "\n".join(lines), library_book_list_menu(kind, page, total, limit))
    await call.answer()




@router.callback_query(F.data.startswith("library:report:"))
async def library_batch_report(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    await call.answer("Готовлю отчёт")
    with tempfile.TemporaryDirectory(prefix="voxlyra_batch_report_") as temp_name:
        output = Path(temp_name) / f"VoxLyra_Import_{batch_id}_report.json"
        stats = await build_batch_report(batch_id, output)
        await call.message.answer_document(
            FSInputFile(output, filename=output.name),
            caption=(f"📄 Отчёт импорта #{batch_id}\n"
                     f"Книг: <b>{stats['books']}</b> · дублей: <b>{stats['duplicates']}</b> · ошибок: <b>{stats['errors']}</b>"),
        )


@router.callback_query(F.data.startswith("library:rollback_confirm:"))
async def library_rollback_confirm(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    await _safe_edit(
        call,
        "<b>Удалить черновики этого пакета?</b>\n\n"
        "Будут удалены только ещё не опубликованные книги, их главы и файлы. "
        "Опубликованные книги, покупки и книги авторов не затрагиваются.",
        library_rollback_confirm_menu(batch_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("library:rollback:"))
async def library_rollback(call: CallbackQuery) -> None:
    if await _deny(call): return
    batch_id = int(call.data.rsplit(":", 1)[1])
    result = await rollback_batch_drafts(batch_id)
    await _safe_edit(
        call,
        "<b>🗑 Черновики пакета удалены</b>\n\n"
        f"Книг: <b>{result['books']}</b>\nГлав: <b>{result['chapters']}</b>",
        library_batch_details_menu(batch_id),
    )
    await call.answer()


SETTING_INPUTS = {
    "max_books": {
        "title": "Изменение лимита",
        "label": "максимальное количество книг в архиве",
        "hint": "Для количества книг значение <b>0</b> означает отсутствие лимита.",
        "cancel": "library:settings",
        "minimum": 0,
        "maximum": 100000,
    },
    "max_archive_mb": {
        "title": "Изменение лимита",
        "label": "максимальный размер ZIP в МБ",
        "hint": "Допустимое значение: от <b>10</b> до <b>2000</b> МБ.",
        "cancel": "library:settings",
        "minimum": 10,
        "maximum": 2000,
    },
    "max_unpacked_mb": {
        "title": "Изменение лимита",
        "label": "максимальный размер после распаковки в МБ",
        "hint": "Допустимое значение: от <b>100</b> до <b>20000</b> МБ.",
        "cancel": "library:settings",
        "minimum": 100,
        "maximum": 20000,
    },
    "channel_interval_minutes": {
        "title": "Интервал пакетной библиотеки",
        "label": "интервал между запусками публикации в минутах",
        "hint": "Допустимое значение: от <b>1</b> минуты до <b>10080</b> минут.",
        "cancel": "library:channel_schedule",
        "minimum": 1,
        "maximum": 10080,
    },
    "channel_posts_per_run": {
        "title": "Книги пакетной библиотеки",
        "label": "количество книг за один запуск",
        "hint": "Допустимое значение: от <b>1</b> до <b>50</b> книг.",
        "cancel": "library:channel_schedule",
        "minimum": 1,
        "maximum": 50,
    },
    "author_channel_interval_minutes": {
        "title": "Интервал обычных авторов",
        "label": "интервал между запусками авторской очереди в минутах",
        "hint": "Допустимое значение: от <b>1</b> минуты до <b>10080</b> минут.",
        "cancel": "library:channel_schedule",
        "minimum": 1,
        "maximum": 10080,
    },
    "author_channel_posts_per_run": {
        "title": "Книги обычных авторов",
        "label": "количество авторских книг за один запуск",
        "hint": "Допустимое значение: от <b>1</b> до <b>20</b> книг.",
        "cancel": "library:channel_schedule",
        "minimum": 1,
        "maximum": 20,
    },
}


@router.callback_query(F.data.startswith("library:set_limit:"))
async def library_set_limit_start(call: CallbackQuery, state: FSMContext) -> None:
    if await _deny(call): return
    key = call.data.rsplit(":", 1)[1]
    item = SETTING_INPUTS.get(key)
    if item is None:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    await state.set_state(LibraryImportFlow.waiting_setting_value)
    await state.update_data(setting_key=key)
    await _safe_edit(
        call,
        f"<b>{item['title']}</b>\n\nВведите целое число: {item['label']}.\n{item['hint']}",
        navigation_menu(cancel_callback=str(item["cancel"])),
    )
    await call.answer()


@router.message(LibraryImportFlow.waiting_setting_value)
async def library_set_limit_receive(message: Message, state: FSMContext) -> None:
    if not await _allowed(message.from_user.id):
        await state.clear(); return
    raw = (message.text or "").strip()
    try:
        value = int(raw)
    except ValueError:
        await message.answer("Введите целое число."); return
    data = await state.get_data()
    key = str(data.get("setting_key") or "")
    item = SETTING_INPUTS.get(key)
    if item is None:
        await state.clear()
        await message.answer("Эта настройка больше недоступна. Откройте меню расписания заново.")
        return
    minimum = int(item["minimum"])
    maximum = int(item["maximum"])
    if value < minimum or value > maximum:
        await message.answer(f"Введите число от <b>{minimum}</b> до <b>{maximum}</b>.")
        return
    try:
        if key == "author_channel_interval_minutes":
            await update_author_channel_settings(interval_minutes=value)
            cfg = await get_import_settings()
        elif key == "author_channel_posts_per_run":
            await update_author_channel_settings(posts_per_run=value)
            cfg = await get_import_settings()
        elif key in {"max_books", "max_archive_mb", "max_unpacked_mb", "channel_interval_minutes", "channel_posts_per_run"}:
            cfg = await update_import_settings(**{key: value})
        else:
            raise ValueError("Неизвестная настройка")
    except Exception as exc:
        await message.answer(f"Не удалось сохранить: {html.escape(str(exc))}"); return
    await state.clear()
    if key in {"channel_interval_minutes", "channel_posts_per_run", "author_channel_interval_minutes", "author_channel_posts_per_run"}:
        author_cfg = await get_author_channel_status()
        await message.answer(
            "✅ Расписание сохранено.\n\n"
            f"Пакетная библиотека: каждые <b>{cfg['channel_interval_minutes']} мин.</b>, "
            f"по <b>{cfg['channel_posts_per_run']}</b> книг.\n"
            f"Обычные авторы: каждые <b>{author_cfg['interval_minutes']} мин.</b>, "
            f"по <b>{author_cfg['posts_per_run']}</b> книг.",
            reply_markup=library_channel_schedule_menu(
                bool(cfg.get("channel_auto_post", 1)), bool(author_cfg.get("enabled", 1))
            ),
        )
    else:
        await message.answer(
            "✅ Лимит сохранён.\n\n"
            f"Книг: <b>{'без ограничения' if int(cfg['max_books']) == 0 else cfg['max_books']}</b>\nZIP: <b>{cfg['max_archive_mb']} МБ</b>\n"
            f"После распаковки: <b>{cfg['max_unpacked_mb']} МБ</b>",
            reply_markup=library_settings_menu(str(cfg['duplicate_policy'])),
        )


@router.callback_query(F.data == "library:channel_schedule")
async def library_channel_schedule(call: CallbackQuery) -> None:
    if await _deny(call): return
    status = await get_channel_schedule_status()
    author_status = await get_author_channel_status()
    enabled = bool(status.get("channel_auto_post", 1))
    author_enabled = bool(author_status.get("enabled", 1))
    await _safe_edit(
        call,
        "<b>⏱ Публикация книг в канал</b>\n\n"
        "<b>Пакетная библиотека</b>\n"
        f"Состояние: <b>{'включена' if enabled else 'остановлена'}</b>\n"
        f"Каждые: <b>{status.get('channel_interval_minutes', 60)} мин.</b>\n"
        f"За запуск: <b>{status.get('channel_posts_per_run', 5)}</b>\n"
        f"Ожидают: <b>{status.get('queued', 0)}</b>, ошибок: <b>{status.get('failed', 0)}</b>\n\n"
        "<b>Обычные авторы</b>\n"
        f"Состояние: <b>{'включена' if author_enabled else 'остановлена'}</b>\n"
        f"Каждые: <b>{author_status.get('interval_minutes', 30)} мин.</b>\n"
        f"За запуск: <b>{author_status.get('posts_per_run', 2)}</b>\n"
        f"Ожидают: <b>{author_status.get('queued', 0)}</b>, ошибок: <b>{author_status.get('failed', 0)}</b>\n\n"
        "Авторская очередь распределяется по кругу: один автор не сможет занять все ближайшие публикации.",
        library_channel_schedule_menu(enabled, author_enabled),
    )
    await call.answer()


@router.callback_query(F.data == "library:channel_toggle")
async def library_channel_toggle(call: CallbackQuery) -> None:
    if await _deny(call): return
    cfg = await get_import_settings()
    await update_import_settings(channel_auto_post=not bool(cfg.get("channel_auto_post", 1)))
    await call.answer("Настройка сохранена")
    await library_channel_schedule(call)


@router.callback_query(F.data == "library:author_channel_toggle")
async def library_author_channel_toggle(call: CallbackQuery) -> None:
    if await _deny(call): return
    current = await get_author_channel_status()
    await update_author_channel_settings(enabled=0 if current.get("enabled", 1) else 1)
    await call.answer("Настройка сохранена")
    await library_channel_schedule(call)


@router.callback_query(F.data == "library:author_channel_retry_failed")
async def library_author_channel_retry_failed(call: CallbackQuery) -> None:
    if await _deny(call): return
    count = await retry_failed_author_posts()
    await call.answer(f"Возвращено в очередь: {count}", show_alert=True)
    await library_channel_schedule(call)


@router.callback_query(F.data == "library:channel_retry_failed")
async def library_channel_retry_failed(call: CallbackQuery) -> None:
    if await _deny(call): return
    count = await retry_failed_channel_posts()
    await call.answer(f"Возвращено в очередь: {count}", show_alert=True)
    await library_channel_schedule(call)


@router.callback_query(F.data == "library:settings")
async def library_settings(call: CallbackQuery) -> None:
    if await _deny(call): return
    cfg = await get_import_settings()
    policy = {"ask": "спрашивать", "skip": "пропускать", "replace": "заменять"}.get(cfg["duplicate_policy"], cfg["duplicate_policy"])
    await _safe_edit(
        call,
        "<b>⚙️ Настройки импорта</b>\n\n"
        f"Книг в архиве: до <b>{cfg['max_books']}</b>\n"
        f"Размер ZIP: до <b>{cfg['max_archive_mb']} МБ</b>\n"
        f"После распаковки: до <b>{cfg['max_unpacked_mb']} МБ</b>\n"
        f"Дубли: <b>{policy}</b>\n\n"
        "Безопасный режим «спрашивать» позволяет решить судьбу каждого дубля после импорта.",
        library_settings_menu(str(cfg["duplicate_policy"])),
    )
    await call.answer()


@router.callback_query(F.data.startswith("library:set_duplicate_policy:"))
async def library_set_duplicate_policy(call: CallbackQuery) -> None:
    if await _deny(call): return
    policy = call.data.rsplit(":", 1)[1]
    await update_import_settings(duplicate_policy=policy)
    await call.answer("Настройка сохранена")
    await library_settings(call)
