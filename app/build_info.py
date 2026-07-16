"""Внутренняя метка установленной сборки.

Версия показывается только владельцу в защищённом центре управления и
в диагностике. В публичные страницы и обычные пользовательские меню она не
передаётся.
"""

OWNER_BUILD_VERSION = "v1.12.1"
OWNER_BUILD_NAME = "новый комплект премиальных иконок Mini App"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.12.1 · полная замена иконок Mini App"
