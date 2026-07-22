from __future__ import annotations

import asyncio
import html
import json
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo,
)

from app.config import settings
from app.db import get_admin_permissions, upsert_user
from app.keyboards import (
    library_batch_details_menu,
    library_batch_menu,
    library_channel_schedule_menu,
    library_book_list_menu,
    library_duplicate_menu,
    library_import_active_menu,
    library_manager_menu,
    library_publish_confirm_menu,
    library_rollback_confirm_menu,
    library_settings_menu,
    navigation_menu,
)
from app.services.author_channel_queue import (
    get_author_channel_status, retry_failed_author_posts, update_author_channel_settings,
)
from app.services.library_import_queue import (
    IMPORT_UPLOAD_ROOT,
    calculate_archive_hash,
    cancel_import_job,
    enqueue_import_job,
    get_import_queue_control_state,
    retry_import_job,
)
from app.services.library_import_upload import create_library_import_upload_token
from app.services.moderation_learning import (
    get_moderation_learning_summary,
    is_auto_moderation_enabled,
    set_auto_moderation_enabled,
)
from app.services.library_manager import (
    audit_batch_publication,
    build_batch_report,
    count_library_books,
    export_library_zip,
    get_batch,
    get_channel_schedule_status,
    get_import_settings,
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

TELEGRAM_CLOUD_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024


def _parse_duplicate_action(data: str | None) -> tuple[int, str, int] | None:
    prefix = "library:duplicate_action:"
    raw = str(data or "")
    if not raw.startswith(prefix):
        return None
    parts = raw[len(prefix):].split(":")
    if len(parts) != 3:
        return None
    duplicate_raw, action, batch_raw = parts
    if action not in {"skip", "replace"}:
        return None
    try:
        duplicate_id = int(duplicate_raw)
        batch_id = int(batch_raw)
    except (TypeError, ValueError):
        return None
    if duplicate_id <= 0 or batch_id <= 0:
        return None
    return duplicate_id, action, batch_id


def _direct_upload_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Загрузить большой ZIP", web_app=WebAppInfo(url=url))],
            [InlineKeyboardButton(text="⬅️ В управление библиотекой", callback_data="library:menu")],
        ]
    )


async def _offer_direct_upload(
    message: Message,
    state: FSMContext,
    progress: Message,
    *,
    filename: str,
    file_size: int,
) -> None:
    base_url = settings.WEBAPP_URL.strip().rstrip("/")
    await state.clear()
    if not base_url:
        await progress.edit_text(
            "<b>❌ Telegram не отдаёт боту этот большой файл</b>\n\n"
            "Для прямой загрузки укажите <code>WEBAPP_URL</code> в настройках запуска. "
            "До этого можно разделить ZIP на части меньше 20 МБ.",
            reply_markup=navigation_menu(cancel_callback="library:menu"),
        )
        return
    token = create_library_import_upload_token(
        telegram_id=int(message.from_user.id),
        chat_id=int(message.chat.id),
        progress_message_id=int(progress.message_id),
    )
    upload_url = f"{base_url}/library-import-upload?token={quote(token, safe='')}"
    size_mb = max(0.1, float(file_size or 0) / 1024 / 1024)
    await progress.edit_text(
        "<b>📤 Нужна прямая загрузка</b>\n\n"
        f"Архив <b>{html.escape(filename)}</b> весит <b>{size_mb:.1f} МБ</b>. "
        "Telegram принимает такой файл в чат, но облачный Bot API не позволяет боту скачать его.\n\n"
        "Нажмите кнопку ниже и выберите тот же ZIP. Он загрузится напрямую в VoxLyra частями, "
        "после чего автоматически попадёт в фоновую очередь. Ботом уже можно пользоваться.",
        reply_markup=_direct_upload_keyboard(upload_url),
    )


class LibraryImportFlow(StatesGroup):
    waiting_zip = State()
    waiting_setting_value = State()


async def _allowed(telegram_id: int, permission: str = "library_import_manage") -> bool:
    if telegram_id in settings.owner_ids:
        return True
    user = await upsert_user(telegram_id, None, None)
    return permission in await get_admin_permissions(int(user["id"]))


async def _allowed_any(telegram_id: int, *permissions: str) -> bool:
    if telegram_id in settings.owner_ids:
        return True
    user = await upsert_user(telegram_id, None, None)
    current = await get_admin_permissions(int(user["id"]))
    return any(permission in current for permission in permissions)


async def _deny(call: CallbackQuery, permission: str = "library_import_manage") -> bool:
    if not await _allowed(call.from_user.id, permission):
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
    if not await _allowed_any(call.from_user.id, "library_import_manage", "library_bulk_import"):
        await call.answer("Недоступно", show_alert=True)
        return
    can_manage = await _allowed(call.from_user.id, "library_import_manage")
    can_bulk_import = await _allowed(call.from_user.id, "library_bulk_import")
    total = await count_library_books()
    drafts = await count_library_books("draft")
    published = await count_library_books("published")
    await _safe_edit(
        call,
        "<b>📚 Управление библиотекой</b>\n\n"
        f"Всего импортировано: <b>{total}</b>\n"
        f"Ожидают проверки: <b>{drafts}</b>\n"
        f"Опубликовано: <b>{published}</b>",
        library_manager_menu(
            can_bulk_import=can_bulk_import,
            can_manage=can_manage,
            back_callback="owner:menu" if call.from_user.id in settings.owner_ids else "mod:menu",
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("library:cancel_job:"))
async def library_import_cancel(call: CallbackQuery) -> None:
    if not await _allowed_any(call.from_user.id, "library_import_manage", "library_bulk_import"):
        await call.answer("Недоступно", show_alert=True)
        return
    try:
        job_id = int(str(call.data or "").rsplit(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректное задание", show_alert=True)
        return
    actor = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    result = await cancel_import_job(
        job_id,
        actor_user_id=int(actor["id"]),
        allow_any=await _allowed(call.from_user.id, "library_import_manage"),
    )
    reason = str(result.get("reason") or "")
    if reason == "not_found":
        await call.answer("Задание не найдено", show_alert=True)
        return
    if reason == "forbidden":
        await call.answer("Можно остановить только своё задание", show_alert=True)
        return
    if reason in {"not_cancellable", "race"}:
        await call.answer("Задание уже завершено или остановлено", show_alert=True)
        return
    if bool(result.get("pending")):
        await _safe_edit(
            call,
            "<b>⏹ Запрошена безопасная остановка</b>\n\n"
            "Бот завершит текущую целостную операцию, откатит незавершённый пакет "
            "и сохранит ZIP для повторного запуска.",
            navigation_menu(cancel_callback="library:menu"),
        )
        await call.answer("Остановка запрошена")
        return
    await _safe_edit(
        call,
        "<b>⛔ Импорт отменён</b>\n\nЗадание не успело начаться, загруженный ZIP удалён.",
        navigation_menu(cancel_callback="library:menu"),
    )
    await call.answer("Задание отменено")


@router.callback_query(F.data.startswith("library:retry_job:"))
async def library_import_retry(call: CallbackQuery) -> None:
    if not await _allowed_any(call.from_user.id, "library_import_manage", "library_bulk_import"):
        await call.answer("Недоступно", show_alert=True)
        return
    try:
        job_id = int(str(call.data or "").rsplit(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await call.answer("Некорректное задание", show_alert=True)
        return
    actor = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    result = await retry_import_job(
        job_id,
        actor_user_id=int(actor["id"]),
        allow_any=await _allowed(call.from_user.id, "library_import_manage"),
    )
    reason = str(result.get("reason") or "")
    if reason == "not_found":
        await call.answer("Задание не найдено", show_alert=True)
        return
    if reason == "forbidden":
        await call.answer("Можно повторить только своё задание", show_alert=True)
        return
    if reason in {"not_failed", "not_retryable", "race"}:
        await call.answer("Задание уже запущено или завершено", show_alert=True)
        return
    if reason == "archive_expired":
        await _safe_edit(
            call,
            "<b>⌛ Срок хранения ZIP истёк</b>\n\nЗагрузите исходный архив повторно.",
            navigation_menu(cancel_callback="library:menu"),
        )
        await call.answer("Архив уже удалён", show_alert=True)
        return
    position = int(result.get("position") or 1)
    position_text = "начинается сейчас" if position == 1 else f"позиция в очереди: <b>{position}</b>"
    await _safe_edit(
        call,
        "<b>🔄 Импорт поставлен повторно</b>\n\n"
        f"Задание: <b>#{job_id}</b>\n"
        f"Импорт {position_text}. Новая загрузка ZIP не потребовалась.",
        navigation_menu(cancel_callback="library:menu"),
    )
    await call.answer("Задание возвращено в очередь")


@router.callback_query(F.data == "library:import")
async def library_import_start(call: CallbackQuery, state: FSMContext) -> None:
    if await _deny(call, "library_bulk_import"): return
    cfg = await get_import_settings()
    await state.set_state(LibraryImportFlow.waiting_zip)
    await _safe_edit(
        call,
        "<b>📥 Импорт книг</b>\n\n"
        "Отправьте ZIP со структурой <code>Books/001/...</code>.\n"
        f"Количество книг: <b>{'без ограничения' if int(cfg['max_books']) == 0 else str(cfg['max_books'])}</b>, "
        f"размер архива: <b>{'без ограничения' if int(cfg['max_archive_mb']) == 0 else 'до ' + str(cfg['max_archive_mb']) + ' МБ'}</b>.\n\n"
        "Для ZIP больше 20 МБ появится кнопка защищённой прямой загрузки. "
        "Книги сохраняются как черновики. Дубли обрабатываются по выбранной политике.",
        navigation_menu(cancel_callback="library:menu"),
    )
    await call.answer()


@router.message(LibraryImportFlow.waiting_zip, F.document)
async def library_import_receive(message: Message, state: FSMContext) -> None:
    if not await _allowed(message.from_user.id, "library_bulk_import"):
        await state.clear()
        return
    document = message.document
    filename = document.file_name or "library.zip"
    if not filename.lower().endswith(".zip"):
        await message.answer(
            "Нужен ZIP-архив.",
            reply_markup=navigation_menu(cancel_callback="library:menu"),
        )
        return
    cfg = await get_import_settings()
    max_mb = int(cfg["max_archive_mb"])
    if max_mb > 0 and int(document.file_size or 0) > max_mb * 1024 * 1024:
        await message.answer(f"Архив больше установленного лимита {max_mb} МБ.")
        return

    actor = await upsert_user(
        message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    progress = await message.answer(
        "<b>⏳ Архив загружается</b>\n\n"
        "После загрузки он будет поставлен в фоновую очередь. Бот останется доступным."
    )
    if int(document.file_size or 0) > TELEGRAM_CLOUD_DOWNLOAD_LIMIT_BYTES:
        await _offer_direct_upload(
            message, state, progress, filename=filename, file_size=int(document.file_size or 0)
        )
        return
    await asyncio.to_thread(IMPORT_UPLOAD_ROOT.mkdir, parents=True, exist_ok=True)
    zip_path = IMPORT_UPLOAD_ROOT / f"{uuid.uuid4().hex}.zip"

    try:
        await message.bot.download(document, destination=zip_path)
        archive_hash = await calculate_archive_hash(zip_path)
        job_id, position = await enqueue_import_job(
            archive_path=zip_path,
            archive_name=filename,
            archive_hash=archive_hash,
            actor_user_id=int(actor["id"]),
            chat_id=int(message.chat.id),
            progress_message_id=int(progress.message_id),
        )
    except TelegramBadRequest as exc:
        if zip_path.exists():
            await asyncio.to_thread(zip_path.unlink)
        if "file is too big" in str(exc).lower():
            await _offer_direct_upload(
                message, state, progress, filename=filename, file_size=int(document.file_size or 0)
            )
            return
        await state.clear()
        await progress.edit_text(
            "<b>❌ Не удалось получить файл из Telegram</b>\n\n"
            f"Причина: {html.escape(str(exc)[:1000])}",
            reply_markup=navigation_menu(cancel_callback="library:menu"),
        )
        return
    except ValueError as exc:
        if zip_path.exists():
            await asyncio.to_thread(zip_path.unlink)
        await state.clear()
        await progress.edit_text(
            f"<b>❌ Импорт не поставлен в очередь</b>\n\n{html.escape(str(exc))}",
            reply_markup=navigation_menu(cancel_callback="library:menu"),
        )
        return
    except Exception as exc:
        if zip_path.exists():
            await asyncio.to_thread(zip_path.unlink)
        await state.clear()
        await progress.edit_text(
            "<b>❌ Не удалось загрузить архив</b>\n\n"
            f"Причина: {html.escape(str(exc)[:1000])}",
            reply_markup=navigation_menu(cancel_callback="library:menu"),
        )
        return

    await state.clear()
    queue_state = await get_import_queue_control_state()
    queue_mode = str(queue_state.get("mode") or "running")
    if queue_mode == "running":
        position_text = "запустится в течение нескольких секунд" if position == 1 else f"позиция в очереди: <b>{position}</b>"
        queue_note = "Во время импорта можно пользоваться ботом, читать книги и открывать другие разделы."
    else:
        position_text = f"поставлен в очередь на позицию <b>{position}</b>"
        queue_note = (
            "Очередь сейчас приостановлена. Откройте управление библиотекой и нажмите "
            "«Продолжить очередь». Повторно отправлять ZIP не нужно."
        )
    await progress.edit_text(
        "<b>✅ Архив принят</b>\n\n"
        f"Задание: <b>#{job_id}</b>\n"
        f"Импорт {position_text}. Прогресс будет обновляться в этом сообщении.\n\n"
        f"{queue_note}",
        reply_markup=library_import_active_menu(job_id, processing=False),
    )


@router.message(LibraryImportFlow.waiting_zip)
async def library_import_wrong(message: Message) -> None:
    if not await _allowed(message.from_user.id, "library_bulk_import"):
        return
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
                f"добавлено {row['imported_count']}, заменено {row['replaced_count']}, "
                f"перенумеровано {row['renumbered_count']}, дублей {row['duplicate_count']}, "
                f"ошибок {row['error_count']}"
            )
        text = "\n\n".join(lines)
    can_bulk_import = await _allowed(call.from_user.id, "library_bulk_import")
    await _safe_edit(
        call,
        text,
        library_manager_menu(
            can_bulk_import=can_bulk_import,
            can_manage=True,
            back_callback="owner:menu" if call.from_user.id in settings.owner_ids else "mod:menu",
        ),
    )
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
        f"Заменено: <b>{row['replaced_count']}</b>",
        f"Перенумеровано: <b>{row['renumbered_count']}</b>",
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
    if await _deny(call):
        return

    parsed = _parse_duplicate_action(call.data)
    if parsed is None:
        await call.answer(
            "Кнопка устарела или повреждена. Откройте список дублей заново.",
            show_alert=True,
        )
        return
    duplicate_id, action, batch_id = parsed

    try:
        result = await resolve_duplicate(duplicate_id, action)
    except Exception as exc:
        await call.answer(f"Не удалось обработать дубль: {exc}", show_alert=True)
        return

    if result.get("status") == "resolved" and result.get("action") is None:
        answer_text = "Этот дубль уже обработан"
    else:
        answer_text = "Книга заменена" if action == "replace" else "Дубль пропущен"
    await call.answer(answer_text)

    rows = await list_batch_duplicates(batch_id)
    if rows:
        row = rows[0]
        await _safe_edit(
            call,
            "<b>⚠️ Следующий дубль</b>\n\n"
            f"Книга: <b>{html.escape(str(row['title']))}</b>\n"
            f"Автор: <b>{html.escape(str(row['author']))}</b>\n"
            f"Существующая книга: <b>ID {row['existing_book_id']}</b>",
            library_duplicate_menu(int(row["id"]), batch_id, len(rows)),
        )
    else:
        await _safe_edit(
            call,
            "<b>✅ Все дубли обработаны</b>",
            library_batch_details_menu(batch_id),
        )


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
        "Будут удалены только новые, ещё не опубликованные книги, их главы и файлы. "
        "Существующие книги, автоматически заменённые этим импортом, опубликованные книги, "
        "покупки и книги авторов не затрагиваются.",
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
        "hint": "Значение <b>0</b> снимает пользовательский лимит; технический потолок прямой загрузки задаётся сервером.",
        "cancel": "library:settings",
        "minimum": 0,
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
        auto_enabled = await is_auto_moderation_enabled()
        await message.answer(
            "✅ Лимит сохранён.\n\n"
            f"Книг: <b>{'без ограничения' if int(cfg['max_books']) == 0 else cfg['max_books']}</b>\nZIP: <b>{'без ограничения' if int(cfg['max_archive_mb']) == 0 else str(cfg['max_archive_mb']) + ' МБ'}</b>\n"
            f"После распаковки: <b>{cfg['max_unpacked_mb']} МБ</b>",
            reply_markup=library_settings_menu(str(cfg['duplicate_policy']), auto_enabled),
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
    learning = await get_moderation_learning_summary()
    policy = {"ask": "спрашивать", "skip": "пропускать", "replace": "заменять"}.get(cfg["duplicate_policy"], cfg["duplicate_policy"])
    await _safe_edit(
        call,
        "<b>⚙️ Настройки импорта</b>\n\n"
        f"Книг в архиве: до <b>{cfg['max_books']}</b>\n"
        f"Размер ZIP: <b>{'без ограничения' if int(cfg['max_archive_mb']) == 0 else 'до ' + str(cfg['max_archive_mb']) + ' МБ'}</b>\n"
        f"После распаковки: до <b>{cfg['max_unpacked_mb']} МБ</b>\n"
        f"Дубли: <b>{policy}</b>\n\n"
        f"Автомодерация: <b>{'включена' if learning['enabled'] else 'выключена'}</b>\n"
        f"Ручных решений для обучения: <b>{learning['approved'] + learning['rejected']}</b>\n"
        f"Доверенных мягких категорий: <b>{len(learning['trusted_categories'])}</b>\n\n"
        "Изменённая версия той же книги заменяется автоматически. "
        "Опасные категории никогда не отключаются обучением.",
        library_settings_menu(str(cfg["duplicate_policy"]), bool(learning["enabled"])),
    )
    await call.answer()


@router.callback_query(F.data.startswith("library:set_duplicate_policy:"))
async def library_set_duplicate_policy(call: CallbackQuery) -> None:
    if await _deny(call): return
    policy = call.data.rsplit(":", 1)[1]
    await update_import_settings(duplicate_policy=policy)
    await call.answer("Настройка сохранена")
    await library_settings(call)


@router.callback_query(F.data == "library:auto_moderation_toggle")
async def library_auto_moderation_toggle(call: CallbackQuery) -> None:
    if await _deny(call):
        return
    enabled = await is_auto_moderation_enabled()
    await set_auto_moderation_enabled(not enabled)
    await call.answer("Автомодерация включена" if not enabled else "Автомодерация выключена")
    await library_settings(call)
