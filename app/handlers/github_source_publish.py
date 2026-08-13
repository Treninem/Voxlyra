from __future__ import annotations

import html
import uuid
from pathlib import Path
from urllib.parse import quote

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.services.github_source_publish import GitHubSourcePublishError, publish_source_package_zip
from app.services.github_source_upload import GitHubSourceUploadError, create_github_source_upload_token

router = Router()

TELEGRAM_CLOUD_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024


class GitHubSourcePublishFlow(StatesGroup):
    waiting_zip = State()


def _is_system_owner(subject) -> bool:
    user = getattr(subject, "from_user", None)
    return bool(user and settings.is_system_owner(user.id))


def _direct_upload_url(*, telegram_id: int, chat_id: int) -> str:
    base = str(settings.WEBAPP_URL or "").strip().rstrip("/")
    if not base:
        return ""
    try:
        token = create_github_source_upload_token(telegram_id=telegram_id, chat_id=chat_id)
    except GitHubSourceUploadError:
        return ""
    return f"{base}/github-source-upload?token={quote(token, safe='')}"


def _source_keyboard(*, telegram_id: int, chat_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    direct_url = _direct_upload_url(telegram_id=telegram_id, chat_id=chat_id)
    if direct_url:
        rows.append([InlineKeyboardButton(text="🌐 Загрузить ZIP напрямую", url=direct_url)])
    rows.append([InlineKeyboardButton(text="⬅️ Системные инструменты", callback_data="owner:system")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _setup_error() -> str:
    if not bool(settings.GITHUB_SOURCE_WRITE_ENABLED):
        return (
            "<b>⬆️ Source ZIP → GitHub выключен</b>\n\n"
            "Чтобы включить этот закрытый системный мост, задайте "
            "<code>GITHUB_SOURCE_WRITE_ENABLED=true</code> и отдельный "
            "<code>GITHUB_SOURCE_WRITE_TOKEN</code> с Contents: Read and write "
            "только для source-репозитория."
        )
    if not str(settings.GITHUB_SOURCE_WRITE_TOKEN or "").strip():
        return (
            "<b>❌ Source-write token не настроен</b>\n\n"
            "Добавьте <code>GITHUB_SOURCE_WRITE_TOKEN</code> в секретные env. "
            "Сам токен бот никогда не показывает."
        )
    return ""


def _instructions() -> str:
    direct = bool(str(settings.WEBAPP_URL or "").strip())
    return (
        "<b>⬆️ Source ZIP → GitHub</b>\n\n"
        f"Репозиторий: <code>{html.escape(settings.GITHUB_IMPORT_REPOSITORY)}</code>\n"
        f"Ветка: <code>{html.escape(settings.GITHUB_IMPORT_BRANCH)}</code>\n\n"
        "Бот проверяет manifest, SHA-256, LICENSE.txt/SOURCES.txt, наличие реального "
        "произведения и структуру пакета. Пакет и <code>enabled=true</code> появляются "
        "в GitHub одним атомарным commit.\n\n"
        "📎 ZIP до 20 МБ можно отправить сообщением прямо сюда.\n"
        + (
            f"🌐 Для ZIP до {int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB)} МБ используйте кнопку прямой загрузки — лимит Telegram 20 МБ на неё не действует."
            if direct
            else "🌐 Прямая загрузка станет доступна после настройки WEBAPP_URL."
        )
    )


@router.message(Command("github_source_publish"))
async def source_publish_start(message: Message, state: FSMContext) -> None:
    """Hidden non-delegable binary bridge from Telegram/web to BookVoxLyra."""
    if not _is_system_owner(message):
        return
    error = _setup_error()
    if error:
        await state.clear()
        await message.answer(
            error,
            reply_markup=_source_keyboard(telegram_id=message.from_user.id, chat_id=message.chat.id),
        )
        return
    await state.set_state(GitHubSourcePublishFlow.waiting_zip)
    await message.answer(
        _instructions(),
        reply_markup=_source_keyboard(telegram_id=message.from_user.id, chat_id=message.chat.id),
    )


@router.callback_query(F.data == "owner:github_source_publish")
async def source_publish_from_system_tools(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_system_owner(call):
        await call.answer("Недоступно", show_alert=True)
        return
    error = _setup_error()
    markup = _source_keyboard(telegram_id=call.from_user.id, chat_id=call.message.chat.id)
    if error:
        await state.clear()
        await call.message.edit_text(error, reply_markup=markup)
        await call.answer()
        return
    await state.set_state(GitHubSourcePublishFlow.waiting_zip)
    await call.message.edit_text(_instructions(), reply_markup=markup)
    await call.answer()


@router.message(GitHubSourcePublishFlow.waiting_zip, F.document)
async def source_publish_receive(message: Message, state: FSMContext) -> None:
    if not _is_system_owner(message):
        await state.clear()
        return
    markup = _source_keyboard(telegram_id=message.from_user.id, chat_id=message.chat.id)
    document = message.document
    filename = str(document.file_name or "source.zip")
    if not filename.lower().endswith(".zip"):
        await message.answer("Нужен ZIP-файл source-ready пакета.", reply_markup=markup)
        return
    file_size = int(document.file_size or 0)
    configured_limit = max(1, int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB)) * 1024 * 1024
    if file_size > configured_limit:
        await state.clear()
        await message.answer(
            f"ZIP превышает source-write лимит {int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB)} МБ.",
            reply_markup=markup,
        )
        return
    if file_size > TELEGRAM_CLOUD_DOWNLOAD_LIMIT_BYTES:
        await message.answer(
            "Этот ZIP больше 20 МБ. Telegram Bot API не отдаст его боту напрямую, "
            "поэтому откройте защищённую прямую загрузку кнопкой ниже. Пакет пока не изменён.",
            reply_markup=markup,
        )
        return

    temp_root = Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import")) / "source_uploads"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_path = temp_root / f"{uuid.uuid4().hex}.zip"
    progress = await message.answer("<b>⏳ Проверяю source ZIP и готовлю атомарный GitHub commit…</b>")
    try:
        await message.bot.download(document, destination=temp_path)
        result = await publish_source_package_zip(message.from_user.id, temp_path)
        await state.clear()
        await progress.edit_text(
            "<b>✅ Source-пакет опубликован в GitHub</b>\n\n"
            f"Пакет: <code>{html.escape(result['package_id'])}</code>\n"
            f"Файлов payload: <b>{int(result['file_count'])}</b>\n"
            f"Репозиторий: <code>{html.escape(result['repository'])}</code>\n"
            f"Ветка: <code>{html.escape(result['branch'])}</code>\n"
            f"Commit: <code>{html.escape(result['commit_sha'][:12])}</code>\n"
            "Import index: <b>enabled=true</b>\n\n"
            "Теперь пакет обнаруживается обычным owner-only GitHub Import.",
            reply_markup=markup,
        )
    except GitHubSourcePublishError as exc:
        await state.clear()
        await progress.edit_text(
            "<b>❌ Source-пакет не опубликован</b>\n\n"
            f"{html.escape(str(exc)[:1500])}\n\n"
            "Ветка и import index не переключались на частично загруженный пакет.",
            reply_markup=markup,
        )
    except Exception as exc:
        await state.clear()
        token = str(settings.GITHUB_SOURCE_WRITE_TOKEN or "")
        safe = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        await progress.edit_text(
            "<b>❌ Неожиданная ошибка source-публикации</b>\n\n"
            f"{html.escape(safe[:1200])}",
            reply_markup=markup,
        )
    finally:
        temp_path.unlink(missing_ok=True)


@router.message(GitHubSourcePublishFlow.waiting_zip)
async def source_publish_non_document(message: Message) -> None:
    if _is_system_owner(message):
        await message.answer(
            "Отправьте source-ready ZIP или используйте прямую загрузку.",
            reply_markup=_source_keyboard(telegram_id=message.from_user.id, chat_id=message.chat.id),
        )
