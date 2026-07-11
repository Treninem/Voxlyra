"""Внутренняя метка установленной сборки.

Она не зависит от переменных окружения и показывается только владельцу бота.
PROJECT_VERSION намеренно остаётся совместимым с уже настроенным Bothost.
"""

OWNER_BUILD_VERSION = "v1.10.0"
OWNER_BUILD_NAME = "завершение комиксов и безопасная отмена покупок"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"
