from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.build_info import owner_build_label
from app.services.reader_tts import tts_engine_status


@dataclass(frozen=True)
class DiagnosticItem:
    code: str
    label: str
    ok: bool
    hint: str = ""


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def collect_diagnostics() -> list[DiagnosticItem]:
    """Проверки, которые владелец видит в скрытом меню.

    Они не делают внешних запросов и не светят токен. Цель — быстро понять,
    почему Mini App, канал, Stars или база могут не работать после деплоя.
    """
    db_path = Path(settings.DATABASE_PATH)
    bot_username = settings.BOT_USERNAME.strip().lstrip("@")
    webapp_url = settings.WEBAPP_URL.strip().rstrip("/")
    channel_id = settings.CHANNEL_ID.strip()

    return [
        DiagnosticItem(
            "bot_token",
            "BOT_TOKEN указан",
            bool(settings.BOT_TOKEN and settings.BOT_TOKEN != "PASTE_BOT_TOKEN_HERE"),
            "В Bothost вставьте токен от @BotFather.",
        ),
        DiagnosticItem(
            "owners",
            "OWNER_IDS указан",
            bool(settings.owner_ids),
            "Укажите свой Telegram ID. Можно несколько через запятую.",
        ),
        DiagnosticItem(
            "webapp_enabled",
            "Mini App включён",
            bool(settings.RUN_WEBAPP),
            "Для каталога, читалки и аудиоплеера нужно RUN_WEBAPP=true.",
        ),
        DiagnosticItem(
            "port",
            "PORT задан",
            int(settings.PORT) > 0,
            "На Bothost обычно ставим PORT=3000.",
        ),
        DiagnosticItem(
            "webapp_url",
            "WEBAPP_URL похож на HTTPS-адрес",
            _is_https_url(webapp_url),
            "После деплоя скопируйте публичный HTTPS-адрес проекта Bothost без слеша в конце.",
        ),
        DiagnosticItem(
            "bot_username",
            "BOT_USERNAME указан",
            bool(bot_username),
            "Нужен username бота без @, чтобы Mini App мог отправлять пользователя на покупку.",
        ),
        DiagnosticItem(
            "database_path",
            "База лежит в data/",
            str(db_path).replace("\\", "/").startswith("data/"),
            "Для Bothost лучше DATABASE_PATH=data/voxlyra.sqlite3, чтобы база сохранялась между обновлениями.",
        ),
        DiagnosticItem(
            "channel",
            "CHANNEL_ID указан",
            bool(channel_id),
            "Укажите @username_канала или числовой ID. Бот должен быть администратором канала.",
        ),
        DiagnosticItem(
            "reader_tts",
            "Локальное озвучивание готово",
            bool(tts_engine_status()["enabled"]),
            "Проверьте Dockerfile: должны устанавливаться Piper, ffmpeg и две русские модели голосов.",
        ),
    ]


def diagnostics_summary() -> dict[str, object]:
    items = collect_diagnostics()
    ok_count = sum(1 for item in items if item.ok)
    return {
        "ok": ok_count == len(items),
        "ok_count": ok_count,
        "total": len(items),
        "items": items,
    }


def format_diagnostics_for_owner() -> str:
    summary = diagnostics_summary()
    lines = [
        "<b>🧩 Система</b>",
        "",
        f"Версия сборки: <b>{owner_build_label()}</b>",
        "Публично версия нигде не показывается.",
        "",
        f"Проверки: <b>{summary['ok_count']}/{summary['total']}</b>",
        "",
    ]
    for item in summary["items"]:  # type: ignore[index]
        mark = "✅" if item.ok else "⚠️"
        lines.append(f"{mark} {item.label}")
        if not item.ok and item.hint:
            lines.append(f"   <i>{item.hint}</i>")
    lines.extend([
        "",
        "Что смотреть после запуска:",
        "1. /start открывает главное меню.",
        "2. Кнопка 📚 Читать открывает Mini App.",
        "3. Владелец видит 👑 Управление.",
        "4. После публикации книги пост уходит в канал.",
    ])
    return "\n".join(lines)
