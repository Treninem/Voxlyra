"""Быстрая проверка пользовательского интерфейса перед сборкой архива."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS = ROOT / "app" / "handlers"
KEYBOARDS = ROOT / "app" / "keyboards.py"


def collect_handler_rules() -> tuple[set[str], set[str]]:
    text = "\n".join(path.read_text(encoding="utf-8") for path in HANDLERS.glob("*.py"))
    exact = set(re.findall(r'F\.data\s*==\s*["\']([^"\']+)["\']', text))
    prefixes = set(re.findall(r'F\.data\.startswith\(["\']([^"\']+)["\']\)', text))
    for group in re.findall(r'F\.data\.in_\(\{([^}]+)\}\)', text):
        exact.update(re.findall(r'["\']([^"\']+)["\']', group))
    return exact, prefixes


def collect_static_callbacks() -> set[str]:
    text = KEYBOARDS.read_text(encoding="utf-8")
    values = set(re.findall(r'callback_data\s*=\s*["\']([^"\']+)["\']', text))
    return {value for value in values if "{" not in value and "}" not in value}


def main() -> int:
    exact, prefixes = collect_handler_rules()
    callbacks = collect_static_callbacks()
    unresolved = sorted(
        value for value in callbacks
        if value not in exact and not any(value.startswith(prefix) for prefix in prefixes)
    )
    if unresolved:
        print("Найдены кнопки без обработчиков:")
        for value in unresolved:
            print(" -", value)
        return 1
    print(f"UI audit: {len(callbacks)} статических кнопок имеют обработчики.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
