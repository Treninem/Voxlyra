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
        "app/services/reader_tts.py",
        "app/services/tts_text.py",
        "app/services/tts_providers.py",
        "app/services/tts_quality.py",
        "app/services/tts_queue.py",
        "app/services/tts_sessions.py",
        "app/services/graphic_import.py",
        "app/services/graphic_storage.py",
        "app/services/graphic_ocr.py",
        "app/services/legal_documents.py",
        "app/services/secure_fields.py",
        "app/services/payment_runtime.py",
        "app/services/rankings.py",
        "templates/catalog.html",
        "templates/reader.html",
        "templates/comic_reader.html",
        "templates/author.html",
        "templates/control.html",
        "templates/premium.html",
        "templates/legal.html",
        "static/js/author.js",
        "static/js/comic.js",
        "static/js/comic-sw.js",
        "static/js/control.js",
        "static/img/bot_avatar.png",
        "static/img/channel_avatar.png",
        "static/img/miniapp/scene-library.webp",
        "static/img/miniapp/scene-audio.webp",
        "static/img/miniapp/scene-reading.webp",
        "static/img/miniapp/scene-stories.webp",
        "static/img/miniapp/voxlyra-mark.webp",
        "static/img/miniapp/voxlyra-v.webp",
        "static/img/miniapp/hero-main.webp",
        "static/img/miniapp/menu-background.webp",
        "static/img/miniapp/splash-v.webp",
        "static/img/miniapp/icons/home.webp",
        "static/img/miniapp/icons/reading.webp",
        "static/img/miniapp/icons/audio.webp",
        "static/img/miniapp/icons/comics.webp",
        "static/img/miniapp/icons/library.webp",
        "static/img/miniapp/icons/new.webp",
        "static/img/miniapp/icons/profile.webp",
        "static/img/miniapp/icons/author.webp",
        "static/img/miniapp/icons/bookmark.webp",
        "static/img/miniapp/icons/gift.webp",
        "static/img/miniapp/icons/search.webp",
        "static/img/miniapp/icons/comments.webp",
        "static/img/miniapp/icons/rating.webp",
        "static/img/miniapp/icons/premium.webp",
        "static/img/miniapp/icons/coins.webp",
        "static/img/miniapp/icons/settings.webp",
        "static/img/miniapp/icons/control.webp",
        "static/img/miniapp/icons/create-book.webp",
        "static/img/miniapp/icons/create-comic.webp",
        "static/img/miniapp/icons/moderator.webp",
        "static/img/miniapp/empty/no-books.webp",
        "static/img/miniapp/empty/no-bookmarks.webp",
        "static/img/miniapp/empty/history-empty.webp",
        "static/img/miniapp/empty/nothing-found.webp",
        "static/img/miniapp/empty/chapter-loading.webp",
        "static/img/miniapp/empty/moderation.webp",
        "static/img/miniapp/sections/reading.webp",
        "static/img/miniapp/sections/search.webp",
        "static/img/miniapp/sections/bookmarks.webp",
        "static/img/miniapp/sections/library.webp",
        "static/img/miniapp/sections/audio-stories.webp",
        "static/img/miniapp/sections/comics.webp",
        "static/img/miniapp/sections/universe.webp",
        "data/.gitkeep",
        "storage/tts/.gitkeep",
        "storage/comics/.gitkeep",
        "storage/temp/graphic_imports/.gitkeep",
        "docs/COMICS_STAGE1_V1_9_1.md",
        "docs/COMICS_STAGE2_V1_9_2.md",
        "docs/STATUS_V1_9_2.md",
        "docs/LEGAL_YOOKASSA_STAGE_V1_9_3.md",
        "docs/STATUS_V1_9_3.md",
        "docs/PAYMENTS_AND_PROTECTION_V1_9_4.md",
        "docs/STATUS_V1_9_4.md",
        "docs/STARS_ONLY_PRICING_V1_9_6.md",
        "docs/STATUS_V1_9_6.md",
        "docs/COMICS_STAGE3_V1_9_7.md",
        "docs/COMICS_REMAINING_ROADMAP_V1_9_7.md",
        "docs/STATUS_V1_9_7.md",
        "docs/COMICS_STAGE4_V1_9_8.md",
        "docs/COMICS_REMAINING_ROADMAP_V1_9_8.md",
        "docs/STATUS_V1_9_8.md",
        "docs/CHAPTER_PACKAGES_V1_9_9.md",
        "docs/STATUS_V1_9_9.md",
        "docs/COMICS_STAGE5_V1_10_0.md",
        "docs/PURCHASE_CANCELLATION_V1_10_0.md",
        "docs/STATUS_V1_10_0.md",
        "docs/RELEASE_CHECK_V1_10_0.md",
        "docs/DB_STARTUP_HOTFIX_V1_10_1.md",
        "docs/STATUS_V1_10_1.md",
        "docs/RELEASE_CHECK_V1_10_1.md",
        "docs/LEGAL_AND_COMICS_UI_V1_10_2.md",
        "docs/STATUS_V1_10_2.md",
        "docs/RELEASE_CHECK_V1_10_2.md",
        "docs/RANKINGS_V1_10_3.md",
        "docs/STATUS_V1_10_3.md",
        "docs/RELEASE_CHECK_V1_10_3.md",
        "docs/RICH_MINIAPP_VISUALS_V1_10_4.md",
        "docs/STATUS_V1_10_4.md",
        "docs/RELEASE_CHECK_V1_10_4.md",
        "docs/TTS_V1_10_5_STAGE4.md",
        "docs/TTS_QUALITY_V1_10_5.md",
        "docs/TTS_PRIVATE_SERVER_CONTRACT_V1_10_5.md",
        "docs/STATUS_V1_10_5.md",
        "docs/RELEASE_CHECK_V1_10_5.md",
        "scripts/tts_provider_check.py",
        "scripts/bootstrap_vosk_tts.py",
        "docs/LOCAL_VOICE_STAGE2_V1_11_0.md",
        "docs/PREMIUM_FINAL_STAGE8_V1_11_0.md",
        "docs/STATUS_V1_11_0.md",
        "docs/TRANSFER_TO_NEW_CHAT.md",
        "docs/PROJECT_MEMORY_CURRENT.md",
        "storage/legal/.gitkeep",
    ]:
        ok, text = check_file(path)
        checks.append((ok, text, "Проверьте, что архив распакован полностью."))

    bot_token = os.getenv("BOT_TOKEN", "")
    owner_ids = os.getenv("OWNER_IDS", "")
    webapp_url = os.getenv("WEBAPP_URL", "")
    database_path = os.getenv("DATABASE_PATH", "data/voxlyra.sqlite3")
    port = os.getenv("PORT", "3000")
    legal_name = os.getenv("LEGAL_OPERATOR_NAME", "")
    legal_status = os.getenv("LEGAL_OPERATOR_STATUS", "")
    legal_inn = os.getenv("LEGAL_OPERATOR_INN", "")
    legal_ogrn = os.getenv("LEGAL_OPERATOR_OGRN", "")
    legal_address = os.getenv("LEGAL_OPERATOR_ADDRESS", "")
    legal_email = os.getenv("LEGAL_CONTACT_EMAIL", "")
    encryption_key = os.getenv("DATA_ENCRYPTION_KEY", "")

    requirements_text = (ROOT / "requirements.txt").read_text(encoding="utf-8") if (ROOT / "requirements.txt").exists() else ""
    docker_text = (ROOT / "Dockerfile").read_text(encoding="utf-8") if (ROOT / "Dockerfile").exists() else ""

    checks.extend([
        ("piper-tts==1.4.2" in requirements_text, "Piper закреплён в requirements.txt", "Добавьте piper-tts==1.4.2."),
        ("vosk-tts" in requirements_text, "Локальный Vosk-TTS указан", "Добавьте vosk-tts для автономного русского голоса."),
        ("Pillow" in requirements_text, "Pillow для изображений указан", "Добавьте Pillow в requirements.txt."),
        ("PyMuPDF" in requirements_text, "PyMuPDF для PDF указан", "Добавьте PyMuPDF в requirements.txt."),
        ("libarchive-c" in requirements_text, "libarchive-c для CBR/RAR и 7Z указан", "Добавьте libarchive-c в requirements.txt."),
        ("reportlab" in requirements_text, "ReportLab для юридических PDF указан", "Добавьте reportlab в requirements.txt."),
        ("cryptography" in requirements_text, "Cryptography для защиты реквизитов указан", "Добавьте cryptography в requirements.txt."),
        ("httpx>=" in requirements_text, "HTTP-клиент для сетевых сервисов указан", "Добавьте httpx в requirements.txt."),
        ("pytesseract" in requirements_text, "Python-обёртка OCR указана", "Добавьте pytesseract в requirements.txt."),
        ("libarchive13" in docker_text, "Docker устанавливает libarchive13", "Добавьте libarchive13 в Dockerfile."),
        ("tesseract-ocr-rus" in docker_text, "Docker устанавливает русский OCR", "Добавьте tesseract-ocr-rus в Dockerfile."),
        ("tesseract-ocr-eng" in docker_text, "Docker устанавливает английский OCR", "Добавьте tesseract-ocr-eng в Dockerfile."),
        ("fonts-dejavu-core" in docker_text, "Docker устанавливает кириллический шрифт для PDF", "Добавьте fonts-dejavu-core в Dockerfile."),
        ("piper.download_voices" in docker_text, "Docker загружает голосовые модели", "Добавьте загрузку моделей Piper при Redeploy."),
        ("ru_RU-irina-medium" in docker_text and "ru_RU-dmitri-medium" in docker_text, "Русские модели Ирина и Дмитрий указаны", "Проверьте названия голосовых моделей в Dockerfile."),
        ("bootstrap_vosk_tts.py" in docker_text, "Docker автоматически готовит модель Vosk", "Подключите scripts/bootstrap_vosk_tts.py в Dockerfile."),
        ("vosk-model-tts-ru-0.9-multi" in docker_text, "Русская многоголосая модель Vosk указана", "Укажите vosk-model-tts-ru-0.9-multi."),
        (bool(bot_token and bot_token != "PASTE_BOT_TOKEN_HERE"), "BOT_TOKEN указан", "Возьмите токен у @BotFather и вставьте в Bothost."),
        (bool(owner_ids.strip()), "OWNER_IDS указан", "Укажите свой Telegram ID. Можно несколько через запятую."),
        (is_https(webapp_url), "WEBAPP_URL похож на HTTPS-адрес", "После первого деплоя скопируйте публичный HTTPS URL из Bothost."),
        (not webapp_url.endswith("/"), "WEBAPP_URL без слеша в конце", "Уберите / в конце адреса."),
        (str(database_path).replace('\\', '/').startswith("data/"), "DATABASE_PATH лежит в data/", "Поставьте DATABASE_PATH=data/voxlyra.sqlite3."),
        (port.isdigit() and int(port) > 0, "PORT указан числом", "Обычно PORT=3000."),
        (bool(legal_name.strip()), "LEGAL_OPERATOR_NAME заполнен", "До реального запуска оплаты укажите владельца платформы."),
        (bool(legal_status.strip()), "LEGAL_OPERATOR_STATUS заполнен", "Укажите статус владельца: ИП, ООО или иной фактический статус."),
        (len(''.join(ch for ch in legal_inn if ch.isdigit())) in {10, 12}, "LEGAL_OPERATOR_INN похож на корректный ИНН", "Укажите ИНН из 10 или 12 цифр."),
        (len(''.join(ch for ch in legal_ogrn if ch.isdigit())) in {13, 15}, "LEGAL_OPERATOR_OGRN похож на ОГРН/ОГРНИП", "Укажите ОГРН из 13 цифр или ОГРНИП из 15 цифр."),
        (bool(legal_address.strip()), "LEGAL_OPERATOR_ADDRESS заполнен", "Укажите юридический/почтовый адрес."),
        ("@" in legal_email and "." in legal_email, "LEGAL_CONTACT_EMAIL заполнен", "Укажите рабочую электронную почту."),
        (bool(encryption_key.strip()), "DATA_ENCRYPTION_KEY задан", "Создайте отдельный Fernet-ключ и храните его вне Git."),
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
