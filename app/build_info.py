"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.14.0.37"
OWNER_BUILD_NAME = "reward pack 36 integrated and current gaps reduced"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.14.0.37 · expanded collector progression with safe v2-to-v3 migration"
