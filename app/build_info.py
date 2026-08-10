"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.15.8"
OWNER_BUILD_NAME = "VK menu delivery fallback and API 912 diagnostics"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.15.8 · reliable VK menu delivery with keyboard-free fallback"
