"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.16.0"
OWNER_BUILD_NAME = "verified comic rights and isolated chapter purchases"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.16.0 · verified comic rights and safe chapter commerce"
