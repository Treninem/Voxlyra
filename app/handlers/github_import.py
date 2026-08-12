from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.services.github_import import GitHubImportError, discover_packages, repository_status

router = Router()


def _allowed(call: CallbackQuery) -> bool:
    return bool(call.from_user and settings.is_system_owner(call.from_user.id))


async def _deny(call: CallbackQuery) -> None:
    # Do not disclose that a hidden GitHub import feature exists.
    await call.answer("Недоступно", show_alert=True)


def github_import_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎 Проверить GitHub", callback_data="ghimp:check")
    kb.button(text="🆕 Найти новые пакеты", callback_data="ghimp:scan:1")
    kb.button(text="☑️ Импортировать выбранное", callback_data="ghimp:selected")
    kb.button(text="📥 Импортировать всё новое", callback_data="ghimp:all")
    kb.button(text="🕘 История импорта", callback_data="ghimp:history")
    kb.button(text="⚠️ Ошибки", callback_data="ghimp:errors")
    kb.button(text="🔁 Повторить неудачные", callback_data="ghimp:retry")
    kb.button(text="⚙️ Настройки GitHub", callback_data="ghimp:settings")
    kb.button(text="⬅️ Назад", callback_data="owner:menu")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "owner:github_import")
async def menu(call: CallbackQuery) -> None:
    if not _allowed(call):
        await _deny(call)
        return
    await call.message.edit_text(
        "<b>📦 Контент → Импорт → GitHub</b>\n\n"
        "Источник: <code>Treninem/bookvoxlyra</code>\n"
        "GitHub используется только как источник. После импорта контент хранится по обычной схеме VoxLyra.",
        reply_markup=github_import_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "ghimp:check")
async def check(call: CallbackQuery) -> None:
    if not _allowed(call):
        await _deny(call)
        return
    try:
        info = await repository_status(call.from_user.id)
        text = (
            "<b>✅ GitHub доступен</b>\n\n"
            f"Репозиторий: <code>{info['repository']}</code>\n"
            f"Ветка: <code>{info['branch']}</code>\n"
            f"Корень: <code>{info['root'] or '/'}</code>\n"
            f"Commit: <code>{info['commit_sha'][:12]}</code>"
        )
    except Exception as exc:
        text = f"<b>❌ Проверка не пройдена</b>\n\n{str(exc)[:500]}"
    await call.message.edit_text(text, reply_markup=github_import_menu())
    await call.answer()


@router.callback_query(F.data.startswith("ghimp:scan:"))
async def scan(call: CallbackQuery) -> None:
    if not _allowed(call):
        await _deny(call)
        return
    try:
        page = max(1, int(call.data.rsplit(":", 1)[1]))
        result = await discover_packages(call.from_user.id, page=page)
        items = result["items"]
        lines = [f"<b>📦 Пакеты GitHub · стр. {page}</b>", ""]
        if not items:
            lines.append("Пакеты на этой странице не найдены.")
        else:
            marks = {"new": "🆕", "update": "⬆️", "imported": "✅"}
            for item in items:
                suffix = f" · {item.current_version} → {item.version}" if item.status == "update" else f" · v{item.version}"
                lines.append(f"{marks.get(item.status, '•')} <code>{item.package_id}</code> · {item.title}{suffix}")
        kb = InlineKeyboardBuilder()
        for item in items:
            if item.status in {"new", "update"}:
                kb.button(text=f"{'⬆️' if item.status == 'update' else '📥'} {item.package_id}", callback_data=f"ghimp:pick:{item.package_id}")
        if page > 1:
            kb.button(text="⬅️", callback_data=f"ghimp:scan:{page-1}")
        if page * result["page_size"] < result["total"]:
            kb.button(text="➡️", callback_data=f"ghimp:scan:{page+1}")
        kb.button(text="⬅️ Меню импорта", callback_data="owner:github_import")
        kb.adjust(1)
        await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    except GitHubImportError as exc:
        await call.message.edit_text(f"<b>❌ Ошибка GitHub</b>\n\n{str(exc)[:500]}", reply_markup=github_import_menu())
    await call.answer()


@router.callback_query(F.data.startswith("ghimp:"))
async def protected_placeholder(call: CallbackQuery) -> None:
    if not _allowed(call):
        await _deny(call)
        return
    # Mutating import actions stay disabled until the transaction/rollback adapter
    # to the existing VoxLyra import pipeline is complete.
    await call.answer("Раздел подключается к существующему импортёру VoxLyra", show_alert=True)
