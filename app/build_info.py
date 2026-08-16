"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.16.2"
OWNER_BUILD_NAME = "runtime hardening, diagnostics and resilient startup"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.16.2 · production runtime hardening, strict readiness, preflight and request diagnostics"
