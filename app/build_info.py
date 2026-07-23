"""Внутренняя метка установленной сборки.

Версия показывается только владельцу в защищённом центре управления и
в диагностике. В публичные страницы и обычные пользовательские меню она не
передаётся.
"""

OWNER_BUILD_VERSION = "v1.14.0.13"
OWNER_BUILD_NAME = "achievement collector and media recovery"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.14.0.13 · rewards, imported covers and Telegram avatar"
