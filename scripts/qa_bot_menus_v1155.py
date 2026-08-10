"""Deterministic checks for the v1.15.5 VK menu contract."""
from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app/services/vk_api.py").read_text("utf-8")


def main() -> None:
    tree = ast.parse(SOURCE)
    wanted = {"vk_app_url", "_vk_section_url", "vk_main_keyboard"}
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted]
    module = ast.Module(body=nodes, type_ignores=[])

    class FakeSettings:
        VK_APP_ID = 54713417
        VK_GROUP_ID = 240755410
        vk_owner_ids = {224402322}

    namespace = {"json": json, "Any": object, "settings": FakeSettings()}
    exec(compile(module, "vk_api_menu_contract", "exec"), namespace)
    build = namespace["vk_main_keyboard"]

    for user_id in range(1, 101):
        payload = json.loads(build(user_id))
        assert payload["inline"] is True
        labels = [button["action"]["label"] for row in payload["buttons"] for button in row]
        assert labels == ["📚 Книги", "🖼 Комиксы", "🎧 Слушать", "⭐ Моё", "✍ Автору", "⚙ Ещё"]
        for row in payload["buttons"]:
            for button in row:
                action = button["action"]
                assert action["type"] == "open_link"
                assert action["link"].startswith("https://vk.com/app54713417#")

    owner = json.loads(build(224402322))
    owner_labels = [button["action"]["label"] for row in owner["buttons"] for button in row]
    assert owner_labels[-1] == "👑 Управление"
    print("VK_MENU_QA_OK scenarios=101")


if __name__ == "__main__":
    main()
