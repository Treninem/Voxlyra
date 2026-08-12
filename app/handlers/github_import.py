from __future__ import annotations
import html
from aiogram import F,Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.config import settings
from app.services.github_import import GitHubImportError,discover_packages,import_history,repository_status,import_package,import_all_new,retry_failed
router=Router()
def _allowed(call): return bool(call.from_user and settings.is_system_owner(call.from_user.id))
async def _deny(call): await call.answer("Недоступно",show_alert=True)
def github_import_menu():
 kb=InlineKeyboardBuilder()
 for text,data in (("🔎 Проверить GitHub","ghimp:check"),("🆕 Найти новые пакеты","ghimp:scan:1"),("📥 Импортировать всё новое","ghimp:all"),("🕘 История импорта","ghimp:history"),("⚠️ Ошибки","ghimp:errors"),("🔁 Повторить неудачные","ghimp:retry"),("⚙️ Настройки GitHub","ghimp:settings"),("⬅️ Назад","owner:menu")): kb.button(text=text,callback_data=data)
 kb.adjust(1); return kb.as_markup()
@router.callback_query(F.data=="owner:github_import")
async def menu(call):
 if not _allowed(call): return await _deny(call)
 await call.message.edit_text("<b>📦 Контент → Импорт → GitHub</b>\n\nИсточник: <code>Treninem/bookvoxlyra</code>\nGitHub используется только как источник. После импорта контент хранится по обычной схеме VoxLyra.\n\nМассово: книги и комиксы. Аудиокниги пока пропускаются.",reply_markup=github_import_menu()); await call.answer()
@router.callback_query(F.data=="ghimp:check")
async def check(call):
 if not _allowed(call): return await _deny(call)
 try:
  i=await repository_status(call.from_user.id); text=f"<b>✅ GitHub доступен</b>\n\nРепозиторий: <code>{i['repository']}</code>\nВетка: <code>{i['branch']}</code>\nКорень: <code>{i['root'] or '/'}</code>\nCommit: <code>{i['commit_sha'][:12]}</code>"
 except Exception as e:text=f"<b>❌ Проверка не пройдена</b>\n\n{html.escape(str(e)[:500])}"
 await call.message.edit_text(text,reply_markup=github_import_menu()); await call.answer()
@router.callback_query(F.data.startswith("ghimp:scan:"))
async def scan(call):
 if not _allowed(call): return await _deny(call)
 try:
  page=max(1,int(call.data.rsplit(":",1)[1])); r=await discover_packages(call.from_user.id,page=page); items=r["items"]; lines=[f"<b>📦 Пакеты GitHub · стр. {page}</b>",""]; kb=InlineKeyboardBuilder()
  if not items:lines.append("Пакеты на этой странице не найдены.")
  for x in items:
   suffix=f" · {x.current_version} → {x.version}" if x.status=="update" else f" · v{x.version}"; mark={"new":"🆕","update":"⬆️","imported":"✅"}.get(x.status,"•"); lines.append(f"{mark} <code>{html.escape(x.package_id)}</code> · {html.escape(x.title)}{html.escape(suffix)}")
   if x.content_type=="audiobook": continue
   if x.status=="new":kb.button(text=f"📥 {x.package_id}",callback_data=f"ghimp:pick:{x.package_id}")
   elif x.status=="update":kb.button(text=f"⬆️ Обновить {x.package_id}",callback_data=f"ghimp:update:{x.package_id}")
  if page>1:kb.button(text="⬅️",callback_data=f"ghimp:scan:{page-1}")
  if page*r["page_size"]<r["total"]:kb.button(text="➡️",callback_data=f"ghimp:scan:{page+1}")
  kb.button(text="⬅️ Меню импорта",callback_data="owner:github_import"); kb.adjust(1); await call.message.edit_text("\n".join(lines),reply_markup=kb.as_markup())
 except GitHubImportError as e:await call.message.edit_text(f"<b>❌ Ошибка GitHub</b>\n\n{html.escape(str(e)[:500])}",reply_markup=github_import_menu())
 await call.answer()
async def _run_one(call,pid,allow_update=False):
 if not _allowed(call):return await _deny(call)
 await call.answer("Импорт запущен…")
 try:
  r=await import_package(call.from_user.id,pid,allow_update=allow_update)
  if r["status"]=="unsupported_bulk": text="<b>⏭ Аудиокниги пока не входят в массовый GitHub-импорт</b>"
  elif r["status"]=="update_available": text=f"<b>⬆️ Доступно обновление</b>\n\n{html.escape(r['package'].current_version)} → {html.escape(r['package'].version)}"
  elif r["status"]=="already_imported":text="<b>✅ Этот пакет уже импортирован</b>"
  else:text=f"<b>✅ Импорт завершён</b>\n\nПакет: <code>{html.escape(pid)}</code>\nДобавлено: {r['added']}\nОбновлено: {r['replaced']}\nДубли: {r['duplicates']}\nID: {', '.join(map(str,r['book_ids'])) or '—'}"
 except Exception as e:text=f"<b>❌ Импорт не выполнен</b>\n\n{html.escape(str(e)[:1000])}"
 await call.message.edit_text(text,reply_markup=github_import_menu())
@router.callback_query(F.data.startswith("ghimp:pick:"))
async def pick(call):await _run_one(call,call.data.split(":",2)[2],False)
@router.callback_query(F.data.startswith("ghimp:update:"))
async def update(call):await _run_one(call,call.data.split(":",2)[2],True)
@router.callback_query(F.data=="ghimp:all")
async def all_new(call):
 if not _allowed(call):return await _deny(call)
 await call.answer("Массовый импорт запущен…")
 try:
  r=await import_all_new(call.from_user.id); text=f"<b>📥 Массовый импорт завершён</b>\n\nНовых: {r['total']}\nУспешно: {r['success']}\nОшибок: {r['failed']}\nОжидают ручного обновления: {len(r['updates'])}\nАудио пропущено: {len(r['audio_skipped'])}"
  if r['errors']: text+="\n\n<b>Первые ошибки:</b>\n"+"\n".join(f"• <code>{html.escape(x['package_id'])}</code>: {html.escape(x['error'][:180])}" for x in r['errors'][:5])
 except Exception as e:text=f"<b>❌ Массовый импорт остановлен</b>\n\n{html.escape(str(e)[:1000])}"
 await call.message.edit_text(text[:3900],reply_markup=github_import_menu())
@router.callback_query(F.data=="ghimp:retry")
async def retry(call):
 if not _allowed(call):return await _deny(call)
 await call.answer("Повтор неудачных импортов запущен…")
 try:
  r=await retry_failed(call.from_user.id); text=f"<b>🔁 Повтор завершён</b>\n\nК повтору: {r['total']}\nУспешно: {r['success']}\nСнова с ошибкой: {r['failed']}"
  if r['errors']: text+="\n\n"+"\n".join(f"• <code>{html.escape(x['package_id'])}</code>: {html.escape(x['error'][:180])}" for x in r['errors'][:5])
 except Exception as e:text=f"<b>❌ Повтор не выполнен</b>\n\n{html.escape(str(e)[:1000])}"
 await call.message.edit_text(text[:3900],reply_markup=github_import_menu())
async def _show_history(call,status=""):
 rows=await import_history(call.from_user.id,status=status,limit=30); lines=[f"<b>{'⚠️ Ошибки GitHub-импорта' if status=='failed' else '🕘 История GitHub-импорта'}</b>",""]
 if not rows:lines.append("Записей пока нет.")
 for row in rows:
  mark={"success":"✅","failed":"❌"}.get(str(row["status"]),"•"); lines.append(f"{mark} <code>{html.escape(str(row['package_id']))}</code> · {html.escape(str(row['title']))} · v{html.escape(str(row['version']))} · <code>{html.escape(str(row['commit_sha'])[:12])}</code>")
  if row.get("error"):lines.append("   ↳ "+html.escape(str(row["error"])[:180]))
 await call.message.edit_text("\n".join(lines)[:3900],reply_markup=github_import_menu())
@router.callback_query(F.data=="ghimp:history")
async def history(call):
 if not _allowed(call):return await _deny(call)
 await _show_history(call);await call.answer()
@router.callback_query(F.data=="ghimp:errors")
async def errors(call):
 if not _allowed(call):return await _deny(call)
 await _show_history(call,"failed");await call.answer()
@router.callback_query(F.data=="ghimp:settings")
async def settings_screen(call):
 if not _allowed(call):return await _deny(call)
 token_state="задан" if settings.GITHUB_IMPORT_TOKEN else "не задан (для публичного репозитория допустимо)"; await call.message.edit_text(f"<b>⚙️ Настройки GitHub</b>\n\nРепозиторий: <code>{html.escape(settings.GITHUB_IMPORT_REPOSITORY)}</code>\nВетка: <code>{html.escape(settings.GITHUB_IMPORT_BRANCH)}</code>\nКорневой путь: <code>{html.escape(settings.GITHUB_IMPORT_ROOT or '/')}</code>\nТокен: <b>{token_state}</b>\nЛимит пакета: {int(settings.GITHUB_IMPORT_MAX_PACKAGE_MB)} МБ\nМинимум свободного диска: {int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB)} МБ\n\nСекрет токена никогда не выводится.",reply_markup=github_import_menu());await call.answer()
@router.callback_query(F.data.startswith("ghimp:"))
async def protected_fallback(call):
 if not _allowed(call):return await _deny(call)
 await call.answer("Недоступное действие",show_alert=True)
