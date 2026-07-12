"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.10.4"
OWNER_BUILD_NAME = "иллюстрированный Mini App · книги, аудио и комиксы · 20 независимых иконок · безопасная база"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"
