#!/usr/bin/env python3
"""Deterministic delivery matrix for VK keyboard and API-912 fallback."""

import ast
import asyncio
import logging
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any


source = Path("app/services/vk_api.py").read_text(encoding="utf-8")
tree = ast.parse(source)
wanted = {"VKAPIError", "send_vk_message"}
nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) and node.name in wanted]
module = ast.Module(body=nodes, type_ignores=[])
ns: dict[str, Any] = {
    "Any": Any,
    "logging": logging,
    "logger": logging.getLogger("qa.vk"),
    "random": random,
    "settings": SimpleNamespace(VK_GROUP_TOKEN="group-token"),
    "vk_app_url": lambda: "https://vk.com/app54713417",
}
exec(compile(module, "app/services/vk_api.py", "exec"), ns)
VKAPIError = ns["VKAPIError"]
send_vk_message = ns["send_vk_message"]


async def main() -> None:
    scenarios = 0
    for user_id in range(1, 101):
        calls: list[dict[str, Any]] = []

        async def api_call(method: str, params: dict[str, Any], *, token: str = "") -> int:
            calls.append(dict(params))
            if len(calls) == 1:
                raise VKAPIError(method, 912, "This is a chat bot feature")
            return 1

        ns["vk_api_call"] = api_call
        assert await send_vk_message(user_id, "Меню", keyboard="{\"inline\":true}") is True
        assert len(calls) == 2
        assert "keyboard" in calls[0] and "keyboard" not in calls[1]
        assert "https://vk.com/app54713417" in calls[1]["message"]
        assert calls[0]["random_id"] != calls[1]["random_id"]
        scenarios += 1

    for user_id in range(101, 201):
        calls = []

        async def api_call(method: str, params: dict[str, Any], *, token: str = "") -> int:
            calls.append(dict(params))
            return 1

        ns["vk_api_call"] = api_call
        assert await send_vk_message(user_id, "Меню", keyboard="{\"inline\":true}") is True
        assert len(calls) == 1 and "keyboard" in calls[0]
        scenarios += 1

    print(f"VK delivery QA passed: {scenarios} scenarios")


if __name__ == "__main__":
    asyncio.run(main())
