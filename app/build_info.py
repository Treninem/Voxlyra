"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.14.0.38"
OWNER_BUILD_NAME = "all reward placeholders closed, pack 40 expanded and collector scale recalibrated"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.14.0.38 · expanded collector progression with safe v2-to-v3 migration"
