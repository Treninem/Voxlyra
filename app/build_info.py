"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.14.0.36"
OWNER_BUILD_NAME = "25 collector levels and long-term progression"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.14.0.36 · expanded collector progression with safe v2-to-v3 migration"
