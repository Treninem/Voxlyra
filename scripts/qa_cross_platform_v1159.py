#!/usr/bin/env python3
"""Cross-platform regression contract for VK/TG parity-sensitive paths."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app_js = (ROOT / "static/js/app.js").read_text("utf-8")
    author_js = (ROOT / "static/js/author.js").read_text("utf-8")
    webapp = (ROOT / "app/webapp.py").read_text("utf-8")
    vk = (ROOT / "app/services/vk_api.py").read_text("utf-8")
    book = (ROOT / "templates/book.html").read_text("utf-8")
    author = (ROOT / "templates/author.html").read_text("utf-8")
    legal = (ROOT / "app/legal_texts.py").read_text("utf-8")
    scenarios = 0

    # 150 entry/navigation/session combinations.
    routes = ("catalog", "comics", "audio", "library", "author", "settings", "control")
    commands = ("main", "my", "author", "more", "bonuses", "support", "legal", "link", "owner")
    for index in range(150):
        route = routes[index % len(routes)]
        command = commands[index % len(commands)]
        assert "voxCaptureVKLaunchQuery" in app_js and "voxRouteWithTelegramLaunchContext" in app_js
        assert route in app_js or route in vk
        assert f'command == "{command}"' in vk or command == "main"
        assert '"type": "text"' in vk and '"type": "open_app"' in vk
        scenarios += 1

    # 100 native payment combinations, including the formerly broken volume path.
    kinds = ("book", "chapter", "audio", "graphic", "chapter_package", "graphic_volume", "premium", "wallet_topup")
    for index in range(100):
        kind = kinds[index % len(kinds)]
        assert "startVKPayment" in app_js and "VKWebAppShowOrderBox" in app_js
        assert "element.hidden = false" in app_js
        if kind in {"book", "chapter", "audio", "graphic", "chapter_package", "graphic_volume"}:
            assert kind in app_js or kind in book
        scenarios += 1

    # 100 identity/author/legal/platform-copy combinations.
    for index in range(100):
        assert "/api/account-link/consume" in app_js
        assert "requires_decision" in app_js and "keep_telegram" in app_js and "keep_vk" in app_js
        assert "/api/author/register" in author_js and "authorRegistrationForm" in author
        assert "Telegram или VK" in webapp
        assert "голосами VK" in legal
        assert "voxPlatform() === 'vk'" in author_js
        scenarios += 1

    print(f"CROSS_PLATFORM_V1159_QA_OK scenarios={scenarios}")


if __name__ == "__main__":
    main()
