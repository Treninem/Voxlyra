"""Внутренняя метка установленной сборки.

Она не зависит от переменных окружения и показывается только владельцу бота.
PROJECT_VERSION намеренно остаётся совместимым с уже настроенным Bothost.
"""

OWNER_BUILD_VERSION = "v1.8.9"
OWNER_BUILD_NAME = "умное озвучивание и кэш устройства"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"
