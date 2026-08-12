"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.16.1"
OWNER_BUILD_NAME = "repository cleanup and GitHub import hardening"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.16.1 · regression cleanup, GitHub import hardening and cross-platform stability"
