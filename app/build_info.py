"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.15.3"
OWNER_BUILD_NAME = "hardened VK launch, recoverable account merge and cross-platform prices"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.15.3 · local VK Bridge, transactional account merge and failure hardening"
