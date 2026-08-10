"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.15.7"
OWNER_BUILD_NAME = "native VK sessions for reader, TTS and internal navigation"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.15.7 · signed native VK launch and cross-page session persistence"
