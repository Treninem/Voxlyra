"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.15.5"
OWNER_BUILD_NAME = "visible VK bot menu and safe Telegram navigation"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.15.5 · visible VK inline menu and idempotent Telegram callbacks"
