"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.15.6"
OWNER_BUILD_NAME = "stable comic publishing and automatic work splitting"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.15.6 · comic owner controls and one-file auto structure"
