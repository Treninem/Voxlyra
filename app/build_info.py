"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.11.0"
OWNER_BUILD_NAME = "автономная озвучка · раздельные цены · книги, аудио и комиксы · безопасная база · рекомендации · Premium"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.11.0 · финальная сборка · 8 из 8 этапов"
