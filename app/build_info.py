"""Внутренняя метка установленной сборки."""

OWNER_BUILD_VERSION = "v1.15.4"
OWNER_BUILD_NAME = "mixed Books/Comics bulk import and expanded failure audit"


def owner_build_label() -> str:
    return f"{OWNER_BUILD_VERSION} · {OWNER_BUILD_NAME}"


WORKING_BUILD_STAGE = "v1.15.4 · separate text/graphic bulk pipelines with hundreds of QA scenarios"
