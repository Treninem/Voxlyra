from __future__ import annotations

import html
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.services.github_source_publish import GitHubSourcePublishError, publish_source_package_zip

router = Router()

TELEGRAM_CLOUD_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024


class GitHubSourcePublishFlow(StatesGroup):
    waiting_zip = State()


def _is_system_owner(message: Message) -> bool:
    return bool(message.from_user and settings.is_system_owner(message.from_user.id))


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Системные инструменты", callback_data="owner:system")]
        ]
    )


@router.message(Command("github_source_publish"))
async def source_publish_start(message: Message, state: FSMContext) -> None:
    """Hidden non-delegable binary bridge from Telegram to BookVoxLyra."""
    if not _is_system_owner(message):
        return
    if not bool(settings.GITHUB_SOURCE_WRITE_ENABLED):
        await state.clear()
        await message.answer(
            "<b>⬆️ Source ZIP → GitHub выключен</b>\n\n"
            "Чтобы включить этот закрытый системный мост, задайте "
            "<code>GITHUB_SOURCE_WRITE_ENABLED=true</code> и отдельный "
            "<code>GITHUB_SOURCE_WRITE_TOKEN</code> с Contents: Read and write "
            "только для source-репозитория.",
            reply_markup=_back_keyboard(),
        )
        return
    if not str(settings.GITHUB_SOURCE_WRITE_TOKEN or "").strip():
        await state.clear()
        await message.answer(
            "<b>❌ Source-write token не настроен</b>\n\n"
            "Добавьте <code>GITHUB_SOURCE_WRITE_TOKEN</code> в секретные env. "
            "Сам токен бот никогда не показывает.",
            reply_markup=_back_keyboard(),
        )
        return
    await state.set_state(GitHubSourcePublishFlow.waiting_zip)
    await message.answer(
        "<b>⬆️ Source ZIP → GitHub</b>\n\n"
        f"Репозиторий: <code>{html.escape(settings.GITHUB_IMPORT_REPOSITORY)}</code>\n"
        f"Ветка: <code>{html.escape(settings.GITHUB_IMPORT_BRANCH)}</code>\n\n"
        "Отправьте один готовый source ZIP. Бот проверит manifest, SHA-256, "
        "LICENSE.txt/SOURCES.txt, наличие реального произведения и структуру пакета. "
        "После этого пакет и переключение <code>enabled=true</code> попадут в GitHub "
        "одним атомарным commit.\n\n"
        "Через Telegram поддерживаются ZIP до 20 МБ.",
        reply_markup=_back_keyboard(),
    )


@router.message(GitHubSourcePublishFlow.waiting_zip, F.document)
async def source_publish_receive(message: Message, state: FSMContext) -> None:
    if not _is_system_owner(message):
        await state.clear()
        return
    document = message.document
    filename = str(document.file_name or "source.zip")
    if not filename.lower().endswith(".zip"):
        await message.answer("Нужен ZIP-файл source-ready пакета.", reply_markup=_back_keyboard())
        return
    file_size = int(document.file_size or 0)
    configured_limit = max(1, int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB)) * 1024 * 1024
    if file_size > configured_limit:
        await state.clear()
        await message.answer(
            f"ZIP превышает source-write лимит {int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB)} МБ.",
            reply_markup=_back_keyboard(),
        )
        return
    if file_size > TELEGRAM_CLOUD_DOWNLOAD_LIMIT_BYTES:
        await state.clear()
        await message.answer(
            "Этот ZIP больше 20 МБ и Telegram Bot API не отдаст его боту напрямую. "
            "Пакет не изменён и не включён. Для больших source-пакетов нужен прямой upload bridge.",
            reply_markup=_back_keyboard(),
        )
        return

    temp_root = Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import")) / "source_uploads"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_path = temp_root / f"{uuid.uuid4().hex}.zip"
    progress = await message.answer(
        "<b>⏳ Проверяю source ZIP и готовлю атомарный GitHub commit…</b>"
    )
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
            "Теперь пакет может быть обнаружен обычным owner-only GitHub Import.",
            reply_markup=_back_keyboard(),
        )
    except GitHubSourcePublishError as exc:
        await state.clear()
        await progress.edit_text(
            "<b>❌ Source-пакет не опубликован</b>\n\n"
            f"{html.escape(str(exc)[:1500])}\n\n"
            "Ветка и import index не переключались на частично загруженный пакет.",
            reply_markup=_back_keyboard(),
        )
    except Exception as exc:
        await state.clear()
        token = str(settings.GITHUB_SOURCE_WRITE_TOKEN or "")
        safe = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        await progress.edit_text(
            "<b>❌ Неожиданная ошибка source-публикации</b>\n\n"
            f"{html.escape(safe[:1200])}",
            reply_markup=_back_keyboard(),
        )
    finally:
        temp_path.unlink(missing_ok=True)


@router.message(GitHubSourcePublishFlow.waiting_zip)
async def source_publish_non_document(message: Message) -> None:
    if _is_system_owner(message):
        await message.answer("Отправьте ZIP-файл source-ready пакета.", reply_markup=_back_keyboard())
