"""Локальная проверка перед загрузкой проекта на GitHub/Bothost.

Запуск:
    python scripts/deploy_check.py

Скрипт не подключается к Telegram и не делает внешних запросов. Он проверяет,
что важные файлы и переменные окружения выглядят правильно.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def mark(ok: bool) -> str:
    return "✅" if ok else "⚠️"


def check_file(path: str) -> tuple[bool, str]:
    exists = (ROOT / path).exists()
    return exists, f"{path} {'найден' if exists else 'не найден'}"


def is_https(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def main() -> int:
    checks: list[tuple[bool, str, str]] = []

    for path in [
        "main.py",
        "requirements.txt",
        "Dockerfile",
        ".env.example",
        "app/bot.py",
        "app/webapp.py",
        "templates/catalog.html",
        "templates/reader.html",
        "data/.gitkeep",
    ]:
        ok, text = check_file(path)
        checks.append((ok, text, "Проверьте, что архив распакован полностью."))

    bot_token = os.getenv("BOT_TOKEN", "")
    owner_ids = os.getenv("OWNER_IDS", "")
    webapp_url = os.getenv("WEBAPP_URL", "")
    database_path = os.getenv("DATABASE_PATH", "data/voxlyra.sqlite3")
    port = os.getenv("PORT", "8080")

    checks.extend([
        (bool(bot_token and bot_token != "PASTE_BOT_TOKEN_HERE"), "BOT_TOKEN указан", "Возьмите токен у @BotFather и вставьте в Bothost."),
        (bool(owner_ids.strip()), "OWNER_IDS указан", "Укажите свой Telegram ID. Можно несколько через запятую."),
        (is_https(webapp_url), "WEBAPP_URL похож на HTTPS-адрес", "После первого деплоя скопируйте публичный HTTPS URL из Bothost."),
        (not webapp_url.endswith("/"), "WEBAPP_URL без слеша в конце", "Уберите / в конце адреса."),
        (str(database_path).replace('\\', '/').startswith("data/"), "DATABASE_PATH лежит в data/", "Поставьте DATABASE_PATH=data/voxlyra.sqlite3."),
        (port.isdigit() and int(port) > 0, "PORT указан числом", "Обычно PORT=8080."),
    ])

    print("Проверка проекта Вокслира перед деплоем\n")
    ok_count = 0
    for ok, text, hint in checks:
        print(f"{mark(ok)} {text}")
        if not ok:
            print(f"   {hint}")
        ok_count += int(ok)

    print(f"\nИтог: {ok_count}/{len(checks)} проверок")
    if ok_count != len(checks):
        print("Проект можно загружать, но сначала лучше исправить предупреждения.")
        return 1
    print("Всё выглядит нормально для GitHub → Bothost.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
