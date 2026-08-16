from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    severity: str
    message: str


PERSISTENT_SETTING_NAMES = (
    "LIBRARY_IMPORT_QUEUE_ROOT",
    "LIBRARY_STORAGE_ROOT",
    "BOOK_COVER_STORAGE_ROOT",
    "PROFILE_AVATAR_STORAGE_ROOT",
    "ACHIEVEMENT_ARTWORK_STORAGE_ROOT",
    "AUTHOR_BOOK_STORAGE_ROOT",
    "AUDIO_STORAGE_ROOT",
    "BACKUP_STORAGE_ROOT",
    "COMIC_STORAGE_ROOT",
)


def _result(name: str, ok: bool, severity: str, message: str) -> CheckResult:
    return CheckResult(name=name, ok=bool(ok), severity=severity, message=message)


def _path_from_setting(value: str) -> Path:
    path = Path(str(value or "").strip() or ".")
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve(strict=False)


def _probe_writable_directory(path: Path, name: str) -> CheckResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return _result(name, False, "critical", f"{path} is not a directory")
        fd, probe_name = tempfile.mkstemp(prefix=".voxlyra-write-probe-", dir=str(path))
        os.close(fd)
        Path(probe_name).unlink(missing_ok=True)
        return _result(name, True, "critical", f"writable: {path}")
    except OSError as exc:
        return _result(name, False, "critical", f"not writable: {path} ({exc.__class__.__name__})")


def _feature_checks() -> Iterable[CheckResult]:
    if not str(settings.BOT_TOKEN or "").strip():
        yield _result("telegram", False, "warning", "BOT_TOKEN is empty; Telegram polling will be disabled")
    else:
        yield _result("telegram", True, "warning", "Telegram token configured")

    if settings.VK_ENABLED:
        miniapp_ok = int(settings.VK_APP_ID or 0) > 0 and bool(str(settings.VK_APP_SECRET or settings.VK_SECURE_KEY or "").strip())
        yield _result(
            "vk_miniapp",
            miniapp_ok,
            "warning",
            "VK Mini App configuration is complete" if miniapp_ok else "VK_ENABLED=true but VK_APP_ID/secret is incomplete",
        )
        community_ok = int(settings.VK_GROUP_ID or 0) > 0 and bool(str(settings.VK_GROUP_TOKEN or "").strip())
        yield _result(
            "vk_community",
            community_ok,
            "warning",
            "VK community bot configuration is complete" if community_ok else "VK community Long Poll is not fully configured",
        )
    else:
        yield _result("vk", True, "warning", "VK integration disabled")

    source_write_ok = not settings.GITHUB_SOURCE_WRITE_ENABLED or bool(str(settings.GITHUB_SOURCE_WRITE_TOKEN or "").strip())
    yield _result(
        "github_source_write",
        source_write_ok,
        "warning",
        "GitHub source write configuration accepted" if source_write_ok else "GITHUB_SOURCE_WRITE_ENABLED=true but write token is empty",
    )

    checkout_ok = not settings.YOOKASSA_EXTERNAL_CHECKOUT_ENABLED or (
        bool(str(settings.YOOKASSA_SHOP_ID or "").strip()) and bool(str(settings.YOOKASSA_SECRET_KEY or "").strip())
    )
    yield _result(
        "yookassa_checkout",
        checkout_ok,
        "warning",
        "YooKassa checkout configuration accepted" if checkout_ok else "YooKassa checkout enabled but credentials are incomplete",
    )

    payouts_ok = not settings.YOOKASSA_PAYOUTS_ENABLED or (
        bool(str(settings.YOOKASSA_PAYOUT_GATEWAY_ID or "").strip())
        and bool(str(settings.YOOKASSA_PAYOUT_SECRET_KEY or "").strip())
        and bool(str(settings.DATA_ENCRYPTION_KEY or "").strip())
    )
    yield _result(
        "yookassa_payouts",
        payouts_ok,
        "warning",
        "YooKassa payout configuration accepted" if payouts_ok else "Payouts enabled but gateway/encryption credentials are incomplete",
    )


def run_preflight(*, create_paths: bool = True) -> list[CheckResult]:
    results: list[CheckResult] = []

    port_ok = 1 <= int(settings.PORT or 0) <= 65535
    results.append(_result("port", port_ok, "critical", f"PORT={settings.PORT}" if port_ok else "PORT must be between 1 and 65535"))

    db_path = _path_from_setting(settings.DATABASE_PATH)
    db_parent = db_path.parent
    if create_paths:
        results.append(_probe_writable_directory(db_parent, "database_parent"))
    else:
        results.append(_result("database_parent", db_parent.exists() or db_parent.parent.exists(), "critical", f"database parent: {db_parent}"))

    seen: set[Path] = {db_parent}
    for setting_name in PERSISTENT_SETTING_NAMES:
        path = _path_from_setting(str(getattr(settings, setting_name, "") or ""))
        if path in seen:
            continue
        seen.add(path)
        if create_paths:
            results.append(_probe_writable_directory(path, setting_name.lower()))
        else:
            results.append(_result(setting_name.lower(), True, "critical", f"configured: {path}"))

    try:
        usage = shutil.disk_usage(db_parent)
        free_mb = int(usage.free / (1024 * 1024))
        minimum_mb = max(16, int(getattr(settings, "RUNTIME_MIN_FREE_DISK_MB", 64) or 64))
        results.append(_result("free_disk", free_mb >= minimum_mb, "critical", f"free={free_mb}MB minimum={minimum_mb}MB"))
    except OSError as exc:
        results.append(_result("free_disk", False, "critical", f"disk usage unavailable ({exc.__class__.__name__})"))

    results.extend(_feature_checks())
    return results


def _summary(results: list[CheckResult]) -> dict[str, object]:
    critical_failures = [item for item in results if not item.ok and item.severity == "critical"]
    warnings = [item for item in results if not item.ok and item.severity == "warning"]
    return {
        "ok": not critical_failures,
        "critical_failures": len(critical_failures),
        "warnings": len(warnings),
        "checks": [asdict(item) for item in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate VoxLyra runtime prerequisites without printing secrets.")
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    parser.add_argument("--no-create-paths", action="store_true", help="do not create/probe runtime directories")
    args = parser.parse_args()

    results = run_preflight(create_paths=not args.no_create_paths)
    summary = _summary(results)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    else:
        for item in results:
            if item.ok:
                continue
            level = "ERROR" if item.severity == "critical" else "WARN"
            print(f"[preflight:{level}] {item.name}: {item.message}")
        print(f"[preflight] ok={str(summary['ok']).lower()} critical_failures={summary['critical_failures']} warnings={summary['warnings']}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
