"""Внутренняя метка установленной сборки.

Версия показывается только владельцу в защищённом центре управления и
в диагностике. В публичные страницы и обычные пользовательские меню она не
передаётся.
"""

OWNER_BUILD_VERSION = "v1.14.0.28"
OWNER_BUILD_NAME = "navigation anchors, API freshness and action retry hardening"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.14.0.28 · all internal routes preserve Telegram auth and content anchors; API actions are fresh and retry-safe"
