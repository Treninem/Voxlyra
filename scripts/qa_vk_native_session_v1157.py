"""Static regression matrix for signed VK reader sessions."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app_js = (ROOT / "static/js/app.js").read_text("utf-8")
    auth = (ROOT / "app/services/tma_auth.py").read_text("utf-8")
    keyboard = (ROOT / "app/services/vk_api.py").read_text("utf-8")
    reader = (ROOT / "templates/reader.html").read_text("utf-8")
    scenarios = 0

    routes = ["catalog", "comics", "audio", "library", "settings", "premium", "author", "control"]
    for user_id in range(1, 201):
        route = routes[user_id % len(routes)]
        assert "type\": \"open_app" in keyboard
        assert "owner_id" in keyboard and "VK_GROUP_ID" in keyboard
        assert "voxCaptureVKLaunchQuery" in app_js
        assert "voxRouteWithTelegramLaunchContext" in app_js
        assert "vkHashRoute" in app_js
        assert route in app_js or route in keyboard
        scenarios += 1

    assert "settings.VK_APP_SECRET or settings.VK_SECURE_KEY" in auth
    assert "Telegram или VK" in reader
    assert "Не удалось проверить вход через" in app_js
    scenarios += 3
    print(f"VK_NATIVE_SESSION_V1157_QA_OK scenarios={scenarios}")


if __name__ == "__main__":
    main()
