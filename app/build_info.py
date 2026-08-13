"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.16.2"
OWNER_BUILD_NAME = "large source upload and GitHub import completion"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.16.2 · direct source ZIP upload, owner security and GitHub source publication"
