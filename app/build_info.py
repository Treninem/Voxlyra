"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.10.5"
OWNER_BUILD_NAME = "качественное потоковое озвучивание · непрерывные главы · книги, аудио и комиксы · безопасная база"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"
