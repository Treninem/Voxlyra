"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.15.1"
OWNER_BUILD_NAME = "shared Telegram + VK account, one container and VK votes checkout"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.15.1 · one repository/container/database, cross-platform account linking and VK votes payments"
