from __future__ import annotations

import html
from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.services.github_import import GitHubImportError, discover_packages, import_history, repository_status

router = Router()


def _allowed(call: CallbackQuery) -> bool: return bool(call.from_user and settings.is_system_owner(call.from_user.id))
async def _deny(call: CallbackQuery) -> None: await call.answer("Недоступно", show_alert=True)


def github_import_menu():
    kb = InlineKeyboardBuilder()
    for text, data in (("🔎 Проверить GitHub","ghimp:check"),("🆕 Найти новые пакеты","ghimp:scan:1"),("☑️ Импортировать выбранное","ghimp:selected"),("📥 Импортировать всё новое","ghimp:all"),("🕘 История импорта","ghimp:history"),("⚠️ Ошибки","ghimp:errors"),("🔁 Повторить неудачные","ghimp:retry"),("⚙️ Настройки GitHub","ghimp:settings"),("⬅️ Назад","owner:menu")): kb.button(text=text, callback_data=data)
    kb.adjust(1); return kb.as_markup()


@router.callback_query(F.data == "owner:github_import")
async def menu(call: CallbackQuery) -> None:
    if not _allowed(call): return await _deny(call)
    await call.message.edit_text("<b>📦 Контент → Импорт → GitHub</b>\n\nИсточник: <code>Treninem/bookvoxlyra</code>\nGitHub используется только как источник. После импорта контент хранится по обычной схеме VoxLyra.", reply_markup=github_import_menu()); await call.answer()


@router.callback_query(F.data == "ghimp:check")
async def check(call: CallbackQuery) -> None:
    if not _allowed(call): return await _deny(call)
    try:
        info = await repository_status(call.from_user.id); text = f"<b>✅ GitHub доступен</b>\n\nРепозиторий: <code>{info['repository']}</code>\nВетка: <code>{info['branch']}</code>\nКорень: <code>{info['root'] or '/'}</code>\nCommit: <code>{info['commit_sha'][:12]}</code>"
    except Exception as exc: text = f"<b>❌ Проверка не пройдена</b>\n\n{html.escape(str(exc)[:500])}"
    await call.message.edit_text(text, reply_markup=github_import_menu()); await call.answer()


@router.callback_query(F.data.startswith("ghimp:scan:"))
async def scan(call: CallbackQuery) -> None:
    if not _allowed(call): return await _deny(call)
    try:
        page=max(1,int(call.data.rsplit(":",1)[1])); result=await discover_packages(call.from_user.id,page=page); items=result["items"]; lines=[f"<b>📦 Пакеты GitHub · стр. {page}</b>",""]
        if not items: lines.append("Пакеты на этой странице не найдены.")
        for item in items:
            suffix=f" · {item.current_version} → {item.version}" if item.status=="update" else f" · v{item.version}"; lines.append(f"{{'new':'🆕','update':'⬆️','imported':'✅'}.get(item.status,'•')} <code>{html.escape(item.package_id)}</code> · {html.escape(item.title)}{html.escape(suffix)}")
        kb=InlineKeyboardBuilder()
        for item in items:
            if item.status in {"new","update"}: kb.button(text=f"{'⬆️' if item.status=='update' else '📥'} {item.package_id}",callback_data=f"ghimp:pick:{item.package_id}")
        if page>1: kb.button(text="⬅️",callback_data=f"ghimp:scan:{page-1}")
        if page*result["page_size"]<result["total"]: kb.button(text="➡️",callback_data=f"ghimp:scan:{page+1}")
        kb.button(text="⬅️ Меню импорта",callback_data="owner:github_import"); kb.adjust(1); await call.message.edit_text("\n".join(lines),reply_markup=kb.as_markup())
    except GitHubImportError as exc: await call.message.edit_text(f"<b>❌ Ошибка GitHub</b>\n\n{html.escape(str(exc)[:500])}",reply_markup=github_import_menu())
    await call.answer()


async def _show_history(call: CallbackQuery, status: str = "") -> None:
    rows=await import_history(call.from_user.id,status=status,limit=30); title="⚠️ Ошибки GitHub-импорта" if status=="failed" else "🕘 История GitHub-импорта"; lines=[f"<b>{title}</b>",""]
    if not rows: lines.append("Записей пока нет.")
    for row in rows:
        mark={"success":"✅","failed":"❌","skipped":"⏭"}.get(str(row["status"]),"•"); lines.append(f"{mark} <code>{html.escape(str(row['package_id']))}</code> · {html.escape(str(row['title']))} · v{html.escape(str(row['version']))} · <code>{html.escape(str(row['commit_sha'])[:12])}</code>")
        if row.get("error"): lines.append("   ↳ "+html.escape(str(row["error"])[:180]))
    await call.message.edit_text("\n".join(lines)[:3900],reply_markup=github_import_menu())


@router.callback_query(F.data == "ghimp:history")
async def history(call: CallbackQuery) -> None:
    if not _allowed(call): return await _deny(call)
    await _show_history(call); await call.answer()


@router.callback_query(F.data == "ghimp:errors")
async def errors(call: CallbackQuery) -> None:
    if not _allowed(call): return await _deny(call)
    await _show_history(call,"failed"); await call.answer()


@router.callback_query(F.data == "ghimp:settings")
async def settings_screen(call: CallbackQuery) -> None:
    if not _allowed(call): return await _deny(call)
    token_state="задан" if settings.GITHUB_IMPORT_TOKEN else "не задан (для публичного репозитория допустимо)"
    await call.message.edit_text(f"<b>⚙️ Настройки GitHub</b>\n\nРепозиторий: <code>{html.escape(settings.GITHUB_IMPORT_REPOSITORY)}</code>\nВетка: <code>{html.escape(settings.GITHUB_IMPORT_BRANCH)}</code>\nКорневой путь: <code>{html.escape(settings.GITHUB_IMPORT_ROOT or '/')}</code>\nТокен: <b>{token_state}</b>\nЛимит пакета: {int(settings.GITHUB_IMPORT_MAX_PACKAGE_MB)} МБ\nМинимум свободного диска: {int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB)} МБ\n\nСекрет токена никогда не выводится.",reply_markup=github_import_menu()); await call.answer()


@router.callback_query(F.data.startswith("ghimp:"))
async def protected_placeholder(call: CallbackQuery) -> None:
    if not _allowed(call): return await _deny(call)
    await call.answer("Подключаю безопасную транзакционную передачу в существующий импортёр VoxLyra",show_alert=True)
