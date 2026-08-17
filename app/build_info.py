"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.17.0"
OWNER_BUILD_NAME = "owner catalog recovery, trusted import replacement and private reader analytics"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.17.0 · owner catalog recovery, trusted import replacement, progress-safe comic replacement and private reader analytics"
