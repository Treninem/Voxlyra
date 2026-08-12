from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.services.diagnostics import format_diagnostics_for_owner
from app.services.github_import import (
    discover_packages,
    import_all_new,
    import_history,
    import_package,
    repository_status,
    retry_failed,
)

router = Router()


def _allowed(call) -> bool:
    return bool(call.from_user and settings.is_system_owner(call.from_user.id))


async def _deny(call):
    await call.answer("Недоступно", show_alert=True)


def github_import_menu():
    kb = InlineKeyboardBuilder()
    for text, data in (
        ("🔎 Проверить GitHub", "ghimp:check"),
        ("🆕 Найти новые пакеты", "ghimp:scan:1"),
        ("📥 Импортировать всё новое", "ghimp:all"),
        ("🕘 История импорта", "ghimp:history"),
        ("⚠️ Ошибки", "ghimp:errors"),
        ("🔁 Повторить неудачные", "ghimp:retry"),
        ("⚙️ Настройки GitHub", "ghimp:settings"),
        ("⬅️ Системные инструменты", "owner:system"),
    ):
        kb.button(text=text, callback_data=data)
    kb.adjust(1)
    return kb.as_markup()


def system_owner_tools_menu():
    kb = InlineKeyboardBuilder()
    state = "✅" if bool(settings.GITHUB_IMPORT_ENABLED) else "▫️"
    kb.button(text=f"📦 GitHub Import {state}", callback_data="owner:github_import")
    kb.button(text="🩺 Диагностика", callback_data="owner:system:diagnostics")
    kb.button(text="⬅️ Центр управления", callback_data="owner:menu")
    kb.adjust(1)
    return kb.as_markup()


def _change_lines(package, limit: int = 8) -> str:
    changes = list(getattr(package, "changes", ()) or ())
    if not changes:
        return ""
    shown = changes[:limit]
    result = "\n" + "\n".join(f"   {html.escape(str(item))}" for item in shown)
    if len(changes) > limit:
        result += f"\n   … ещё {len(changes) - limit}"
    return result


@router.message(Command("github_import"))
async def direct_menu(message):
    """Emergency hidden entry for the one non-delegable system owner."""
    if not message.from_user or not settings.is_system_owner(message.from_user.id):
        return
    repository = html.escape(str(settings.GITHUB_IMPORT_REPOSITORY or "не настроен"))
    await message.answer(
        "<b>📦 GitHub Import</b>\n\n"
        f"Источник: <code>{repository}</code>\n"
        "Доступ принадлежит только системному владельцу.",
        reply_markup=github_import_menu(),
    )


# github_import.router is registered before owner.router. This filter is bound to
# the configured SYSTEM_OWNER_ID, so only that one account receives the extended
# system screen; other regular owners continue into owner.py and see the original
# diagnostics screen without any hint that GitHub Import exists.
@router.callback_query((F.data == "owner:system") & (F.from_user.id == settings.SYSTEM_OWNER_ID))
async def system_owner_tools(call):
    if not _allowed(call):
        return await _deny(call)
    enabled = "включён" if bool(settings.GITHUB_IMPORT_ENABLED) else "выключен в env"
    await call.message.edit_text(
        "<b>🧩 Системные инструменты</b>\n\n"
        f"GitHub Import: <b>{enabled}</b>\n"
        "Раздел виден только системному владельцу. Обычные владельцы и администраторы его не видят.",
        reply_markup=system_owner_tools_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "owner:system:diagnostics")
async def system_owner_diagnostics(call):
    if not _allowed(call):
        return await _deny(call)
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Системные инструменты", callback_data="owner:system")
    kb.button(text="🏠 Центр управления", callback_data="owner:menu")
    kb.adjust(1)
    await call.message.edit_text(format_diagnostics_for_owner(), reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data == "owner:github_import")
async def menu(call):
    if not _allowed(call):
        return await _deny(call)
    repository = html.escape(str(settings.GITHUB_IMPORT_REPOSITORY or "не настроен"))
    status = "включён" if bool(settings.GITHUB_IMPORT_ENABLED) else "выключен в env"
    await call.message.edit_text(
        "<b>📦 Контент → Импорт → GitHub</b>\n\n"
        f"Источник: <code>{repository}</code>\n"
        f"Флаг запуска: <b>{status}</b>\n"
        "GitHub используется только как источник. После импорта контент хранится по обычной схеме VoxLyra.\n\n"
        "Массово: книги и комиксы. Аудиокниги пока пропускаются.",
        reply_markup=github_import_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "ghimp:check")
async def check(call):
    if not _allowed(call):
        return await _deny(call)
    try:
        info = await repository_status(call.from_user.id)
        text = (
            "<b>✅ GitHub доступен</b>\n\n"
            f"Репозиторий: <code>{html.escape(info['repository'])}</code>\n"
            f"Ветка: <code>{html.escape(info['branch'])}</code>\n"
            f"Корень: <code>{html.escape(info['root'] or '/')}</code>\n"
            f"Commit: <code>{html.escape(info['commit_sha'][:12])}</code>"
        )
    except Exception as exc:
        text = f"<b>❌ Проверка не пройдена</b>\n\n{html.escape(str(exc)[:500])}"
    await call.message.edit_text(text, reply_markup=github_import_menu())
    await call.answer()


@router.callback_query(F.data.startswith("ghimp:scan:"))
async def scan(call):
    if not _allowed(call):
        return await _deny(call)
    try:
        page = max(1, int(call.data.rsplit(":", 1)[1]))
        result = await discover_packages(call.from_user.id, page=page)
        lines = [f"<b>📦 Пакеты GitHub · стр. {page}</b>", ""]
        kb = InlineKeyboardBuilder()
        if not result["items"]:
            lines.append("Пакеты на этой странице не найдены.")
        for package in result["items"]:
            suffix = (
                f" · {package.current_version} → {package.version}"
                if package.status == "update"
                else f" · v{package.version}"
            )
            mark = {"new": "🆕", "update": "⬆️", "imported": "✅"}.get(package.status, "•")
            lines.append(
                f"{mark} <code>{html.escape(package.package_id)}</code> · "
                f"{html.escape(package.title)}{html.escape(suffix)}{_change_lines(package)}"
            )
            if package.content_type == "audiobook":
                continue
            if package.status == "new":
                kb.button(text=f"📥 {package.package_id}", callback_data=f"ghimp:pick:{package.package_id}")
            elif package.status == "update":
                kb.button(text=f"⬆️ Обновить {package.package_id}", callback_data=f"ghimp:update:{package.package_id}")
        if page > 1:
            kb.button(text="⬅️", callback_data=f"ghimp:scan:{page - 1}")
        if page * result["page_size"] < result["total"]:
            kb.button(text="➡️", callback_data=f"ghimp:scan:{page + 1}")
        kb.button(text="⬅️ Меню импорта", callback_data="owner:github_import")
        kb.adjust(1)
        await call.message.edit_text("\n".join(lines)[:3900], reply_markup=kb.as_markup())
    except Exception as exc:
        # Network/TLS/HTTP errors are not always GitHubImportError. The owner
        # callback must still finish with a readable message instead of leaving
        # Telegram's loading spinner active and emitting only a framework log.
        await call.message.edit_text(
            f"<b>❌ Ошибка GitHub</b>\n\n{html.escape(str(exc)[:500])}",
            reply_markup=github_import_menu(),
        )
    await call.answer()


async def _run_one(call, package_id: str, allow_update: bool = False):
    if not _allowed(call):
        return await _deny(call)
    await call.answer("Импорт запущен…")
    try:
        result = await import_package(call.from_user.id, package_id, allow_update=allow_update)
        if result["status"] == "unsupported_bulk":
            text = "<b>⏭ Аудиокниги пока не входят в массовый GitHub-импорт</b>"
        elif result["status"] == "update_available":
            package = result["package"]
            text = (
                "<b>⬆️ Доступно обновление</b>\n\n"
                f"Текущая версия: <b>{html.escape(package.current_version)}</b>\n"
                f"GitHub: <b>{html.escape(package.version)}</b>"
                f"{_change_lines(package, 20)}"
            )
        elif result["status"] == "already_imported":
            text = "<b>✅ Этот пакет уже импортирован</b>"
        else:
            text = (
                "<b>✅ Импорт завершён</b>\n\n"
                f"Пакет: <code>{html.escape(package_id)}</code>\n"
                f"Добавлено: {result['added']}\n"
                f"Обновлено: {result['replaced']}\n"
                f"Дубли: {result['duplicates']}\n"
                f"ID: {', '.join(map(str, result['book_ids'])) or '—'}"
            )
    except Exception as exc:
        text = f"<b>❌ Импорт не выполнен</b>\n\n{html.escape(str(exc)[:1000])}"
    await call.message.edit_text(text[:3900], reply_markup=github_import_menu())


@router.callback_query(F.data.startswith("ghimp:pick:"))
async def pick(call):
    await _run_one(call, call.data.split(":", 2)[2], False)


@router.callback_query(F.data.startswith("ghimp:update:"))
async def update(call):
    await _run_one(call, call.data.split(":", 2)[2], True)


@router.callback_query(F.data == "ghimp:all")
async def all_new(call):
    if not _allowed(call):
        return await _deny(call)
    await call.answer("Массовый импорт запущен…")
    try:
        result = await import_all_new(call.from_user.id)
        text = (
            "<b>📥 Массовый импорт завершён</b>\n\n"
            f"Новых: {result['total']}\n"
            f"Успешно: {result['success']}\n"
            f"Ошибок: {result['failed']}\n"
            f"Ожидают ручного обновления: {len(result['updates'])}\n"
            f"Аудио пропущено: {len(result['audio_skipped'])}"
        )
        if result["errors"]:
            text += "\n\n<b>Первые ошибки:</b>\n" + "\n".join(
                f"• <code>{html.escape(item['package_id'])}</code>: {html.escape(item['error'][:180])}"
                for item in result["errors"][:5]
            )
    except Exception as exc:
        text = f"<b>❌ Массовый импорт остановлен</b>\n\n{html.escape(str(exc)[:1000])}"
    await call.message.edit_text(text[:3900], reply_markup=github_import_menu())


@router.callback_query(F.data == "ghimp:retry")
async def retry(call):
    if not _allowed(call):
        return await _deny(call)
    await call.answer("Повтор неудачных импортов запущен…")
    try:
        result = await retry_failed(call.from_user.id)
        text = (
            "<b>🔁 Повтор завершён</b>\n\n"
            f"К повтору: {result['total']}\n"
            f"Успешно: {result['success']}\n"
            f"Не выполнено/ошибка: {result['failed']}"
        )
        if result["errors"]:
            text += "\n\n" + "\n".join(
                f"• <code>{html.escape(item['package_id'])}</code>: {html.escape(item['error'][:180])}"
                for item in result["errors"][:5]
            )
    except Exception as exc:
        text = f"<b>❌ Повтор не выполнен</b>\n\n{html.escape(str(exc)[:1000])}"
    await call.message.edit_text(text[:3900], reply_markup=github_import_menu())


async def _show_history(call, status: str = ""):
    rows = await import_history(call.from_user.id, status=status, limit=30)
    lines = [f"<b>{'⚠️ Ошибки GitHub-импорта' if status == 'failed' else '🕘 История GitHub-импорта'}</b>", ""]
    if not rows:
        lines.append("Записей пока нет.")
    for row in rows:
        mark = {"success": "✅", "failed": "❌"}.get(str(row["status"]), "•")
        lines.append(
            f"{mark} <code>{html.escape(str(row['package_id']))}</code> · "
            f"{html.escape(str(row['title']))} · v{html.escape(str(row['version']))} · "
            f"<code>{html.escape(str(row['commit_sha'])[:12])}</code>"
        )
        if row.get("error"):
            lines.append("   ↳ " + html.escape(str(row["error"])[:180]))
    await call.message.edit_text("\n".join(lines)[:3900], reply_markup=github_import_menu())


@router.callback_query(F.data == "ghimp:history")
async def history(call):
    if not _allowed(call):
        return await _deny(call)
    await _show_history(call)
    await call.answer()


@router.callback_query(F.data == "ghimp:errors")
async def errors(call):
    if not _allowed(call):
        return await _deny(call)
    await _show_history(call, "failed")
    await call.answer()


@router.callback_query(F.data == "ghimp:settings")
async def settings_screen(call):
    if not _allowed(call):
        return await _deny(call)
    token_state = "задан" if settings.GITHUB_IMPORT_TOKEN else "не задан (для публичного репозитория допустимо)"
    await call.message.edit_text(
        "<b>⚙️ Настройки GitHub</b>\n\n"
        f"Репозиторий: <code>{html.escape(settings.GITHUB_IMPORT_REPOSITORY)}</code>\n"
        f"Ветка: <code>{html.escape(settings.GITHUB_IMPORT_BRANCH)}</code>\n"
        f"Корневой путь: <code>{html.escape(settings.GITHUB_IMPORT_ROOT or '/')}</code>\n"
        f"Токен: <b>{token_state}</b>\n"
        f"Лимит пакета: {int(settings.GITHUB_IMPORT_MAX_PACKAGE_MB)} МБ\n"
        f"Минимум свободного диска: {int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB)} МБ\n\n"
        "Секрет токена никогда не выводится.",
        reply_markup=github_import_menu(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ghimp:"))
async def protected_fallback(call):
    if not _allowed(call):
        return await _deny(call)
    await call.answer("Недоступное действие", show_alert=True)
