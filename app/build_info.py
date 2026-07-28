"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.14.0.38.1"
OWNER_BUILD_NAME = "achievement progress hotfix and library recovery"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.14.0.38.1 · fixed achievement calculation for library, profile and reader progress routes"
