from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.db import connect, utc_now

# Telegram callback_data is limited to 64 bytes. The longest owner callback
# prefix is ``ghimp:update:`` (13 ASCII bytes), therefore package_id must fit in
# the remaining 51 bytes. package_id itself is ASCII-only by this regex.
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]{1,51}$")
_ALLOWED_TYPES = {"book", "comics", "audiobook"}
_TYPE_DIRS = {"book": "books", "comics": "comics", "audiobook": "audiobooks"}
_BULK_TYPES = {"book", "comics"}
_IMPORT_INDEX = "manifests/import_index.json"
_MAX_DISCOVERED_PACKAGES = 5000
_MAX_MANIFEST_FILES = 20_000
_MAX_VERSION_LENGTH = 128
_MAX_TITLE_LENGTH = 500
_MAX_LANGUAGE_LENGTH = 32


class GitHubImportError(RuntimeError):
    pass


class GitHubImportForbidden(GitHubImportError):
    pass


@dataclass(slots=True)
class GitHubPackage:
    package_id: str
    content_type: str
    title: str
    language: str
    version: str
    created_at: str
    files: tuple[str, ...]
    checksums: dict[str, str]
    path: str
    commit_sha: str
    status: str = "new"
    current_version: str = ""
    changes: tuple[str, ...] = field(default_factory=tuple)


# One owner bulk operation should resolve the GitHub inventory only once. These
# contexts are task-local, so concurrent async requests cannot leak package
# objects or stale inventory into each other.
_DISCOVERY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "github_import_discovery_context",
    default=None,
)
_RESOLVED_PACKAGES: ContextVar[dict[str, GitHubPackage] | None] = ContextVar(
    "github_import_resolved_packages",
    default=None,
)


def require_system_owner(identity_id: int) -> None:
    if not settings.is_system_owner(int(identity_id)):
        raise GitHubImportForbidden("Недостаточно прав")


def _repo() -> tuple[str, str]:
    value = str(settings.GITHUB_IMPORT_REPOSITORY or "").strip().strip("/")
    if value.count("/") != 1:
        raise GitHubImportError("Репозиторий импорта не настроен")
    owner, repo = value.split("/", 1)
    if not owner or not repo:
        raise GitHubImportError("Репозиторий импорта не настроен")
    return owner, repo


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = str(settings.GITHUB_IMPORT_TOKEN or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _root_path(*parts: str) -> str:
    root = str(settings.GITHUB_IMPORT_ROOT or "").strip().strip("/")
    clean = [str(PurePosixPath(p)).strip("/") for p in parts if str(p).strip("/")]
    return "/".join(([root] if root else []) + clean)


def _safe_repo_path(value: object, *, label: str) -> str:
    raw = str(value or "").strip().strip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or str(path) in {".", ""}:
        raise GitHubImportError(f"Небезопасный {label}")
    return str(path)


def _manifest_disabled(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    return data.get("import_enabled") is False or data.get("payload_present") is False


def _manifest_snapshot(package: GitHubPackage) -> dict[str, Any]:
    return {
        "package_id": package.package_id,
        "content_type": package.content_type,
        "title": package.title,
        "language": package.language,
        "version": package.version,
        "created_at": package.created_at,
        "files": list(package.files),
        "checksums": dict(package.checksums),
        "commit_sha": package.commit_sha,
    }


def _diff_manifest(previous_json: object, package: GitHubPackage) -> tuple[str, ...]:
    try:
        previous = json.loads(str(previous_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        previous = {}
    old_files = {str(x) for x in previous.get("files", []) if str(x)} if isinstance(previous, dict) else set()
    old_checksums = dict(previous.get("checksums") or {}) if isinstance(previous, dict) else {}
    new_files = set(package.files)
    changes: list[str] = []
    for name in sorted(new_files - old_files):
        changes.append(f"+ {name}")
    for name in sorted(old_files - new_files):
        changes.append(f"- {name}")
    for name in sorted(old_files & new_files):
        if str(old_checksums.get(name) or "").lower() != str(package.checksums.get(name) or "").lower():
            changes.append(f"~ {name}")
    if not changes and previous:
        if str(previous.get("version") or "") != package.version:
            changes.append("~ version")
        elif str(previous.get("commit_sha") or "") != package.commit_sha:
            changes.append("~ Git commit")
    if not changes and not previous:
        changes.append("~ пакет изменён; предыдущий manifest не сохранён")
    return tuple(changes[:100])


def validate_manifest(data: dict[str, Any], *, package_path: str, commit_sha: str) -> GitHubPackage:
    required = (
        "package_id",
        "content_type",
        "title",
        "language",
        "version",
        "files",
        "checksums",
        "created_at",
    )
    missing = [name for name in required if name not in data]
    if missing:
        raise GitHubImportError("Повреждён manifest: отсутствует " + ", ".join(missing))

    package_id = str(data["package_id"]).strip()
    kind = str(data["content_type"]).strip().lower()
    title = str(data["title"]).strip()
    language = str(data["language"]).strip()
    version = str(data["version"]).strip()
    created_at = str(data["created_at"]).strip()

    if not _PACKAGE_RE.fullmatch(package_id):
        raise GitHubImportError("Некорректный package_id")
    if kind not in _ALLOWED_TYPES:
        raise GitHubImportError("Неподдерживаемый content_type")
    if not title or len(title) > _MAX_TITLE_LENGTH:
        raise GitHubImportError("Некорректное название пакета")
    if not language or len(language) > _MAX_LANGUAGE_LENGTH:
        raise GitHubImportError("Некорректный язык пакета")
    if not version or len(version) > _MAX_VERSION_LENGTH:
        raise GitHubImportError("Некорректная версия пакета")
    if not created_at or len(created_at) > 128:
        raise GitHubImportError("Некорректная дата пакета")
    if not isinstance(data["files"], (list, tuple)):
        raise GitHubImportError("Manifest должен содержать список files")
    if not isinstance(data["checksums"], dict):
        raise GitHubImportError("Manifest должен содержать объект checksums")
    if len(data["files"]) > _MAX_MANIFEST_FILES:
        raise GitHubImportError("Manifest содержит слишком много файлов")

    files = tuple(str(PurePosixPath(str(item))) for item in data["files"])
    if not files or len(files) != len(set(files)):
        raise GitHubImportError("Manifest должен содержать уникальный непустой список files")
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) in {".", ""}:
            raise GitHubImportError("Небезопасный путь в manifest")

    checksums = {
        str(PurePosixPath(str(key))): str(value).lower().strip()
        for key, value in data["checksums"].items()
    }
    if set(checksums) != set(files):
        raise GitHubImportError("checksums должен точно соответствовать files")
    for name in files:
        if not re.fullmatch(r"[0-9a-f]{64}", checksums.get(name, "")):
            raise GitHubImportError(f"Нет корректного SHA-256 для {name}")

    return GitHubPackage(
        package_id=package_id,
        content_type=kind,
        title=title,
        language=language,
        version=version,
        created_at=created_at,
        files=files,
        checksums=checksums,
        path=package_path,
        commit_sha=commit_sha,
    )


async def ensure_github_import_schema() -> None:
    async with connect() as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS github_import_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                status TEXT NOT NULL,
                file_count INTEGER NOT NULL DEFAULT 0,
                bytes_total INTEGER NOT NULL DEFAULT 0,
                book_id INTEGER,
                error TEXT,
                manifest_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(package_id, version, commit_sha, status)
            )"""
        )
        cur = await db.execute("PRAGMA table_info(github_import_history)")
        columns = {str(row[1]) for row in await cur.fetchall()}
        if "manifest_json" not in columns:
            await db.execute("ALTER TABLE github_import_history ADD COLUMN manifest_json TEXT NOT NULL DEFAULT '{}'")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_github_import_package ON github_import_history(package_id,id DESC)"
        )
        await db.commit()


def _merge_history(package: GitHubPackage, previous) -> GitHubPackage:
    if not previous:
        return package
    package.current_version = str(previous["version"])
    same_version = package.current_version == package.version
    same_commit = str(previous["commit_sha"] or "") == package.commit_sha
    package.status = "imported" if same_version and same_commit else "update"
    if package.status == "update":
        package.changes = _diff_manifest(previous["manifest_json"], package)
    return package


async def _last_success(package_id: str):
    await ensure_github_import_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM github_import_history WHERE package_id=? AND status='success' ORDER BY id DESC LIMIT 1",
            (package_id,),
        )
        return await cur.fetchone()


async def _apply_history(package: GitHubPackage) -> GitHubPackage:
    return _merge_history(package, await _last_success(package.package_id))


async def _apply_history_many(packages: list[GitHubPackage]) -> list[GitHubPackage]:
    if not packages:
        return []
    await ensure_github_import_schema()
    result: list[GitHubPackage] = []
    async with connect() as db:
        for package in packages:
            cur = await db.execute(
                "SELECT * FROM github_import_history WHERE package_id=? AND status='success' ORDER BY id DESC LIMIT 1",
                (package.package_id,),
            )
            result.append(_merge_history(package, await cur.fetchone()))
    return result


async def import_history(identity_id: int, *, status: str = "", limit: int = 30) -> list[dict[str, Any]]:
    require_system_owner(identity_id)
    await ensure_github_import_schema()
    sql = "SELECT * FROM github_import_history"
    args: list[Any] = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(100, int(limit))))
    async with connect() as db:
        cur = await db.execute(sql, tuple(args))
        return [dict(row) for row in await cur.fetchall()]


def _raise_rate_limit(response) -> None:
    status = int(getattr(response, "status_code", 0) or 0)
    headers = getattr(response, "headers", {}) or {}
    remaining = str(headers.get("X-RateLimit-Remaining", ""))
    if status == 429 or (status == 403 and remaining == "0"):
        reset = str(headers.get("X-RateLimit-Reset", "")).strip()
        suffix = f" (reset {reset})" if reset else ""
        raise GitHubImportError("Лимит запросов GitHub исчерпан" + suffix)


async def _get_json(client, url: str, params=None):
    response = await client.get(url, headers=_headers(), params=params)
    _raise_rate_limit(response)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def _raw_json(client: httpx.AsyncClient, owner: str, repo: str, ref: str, path: str) -> dict[str, Any] | None:
    raw_url = (
        f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/"
        f"{quote(ref, safe='')}/{quote(path, safe='/')}"
    )
    response = await client.get(raw_url, headers=_headers())
    _raise_rate_limit(response)
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        return None
    try:
        data = response.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


async def repository_status(identity_id: int) -> dict[str, Any]:
    require_system_owner(identity_id)
    owner, repo = _repo()
    branch = str(settings.GITHUB_IMPORT_BRANCH or "main").strip()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        commit = await _get_json(
            client,
            f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/commits/{quote(branch)}",
        )
    if not commit:
        raise GitHubImportError("Ветка GitHub не найдена")
    return {
        "repository": f"{owner}/{repo}",
        "branch": branch,
        "root": str(settings.GITHUB_IMPORT_ROOT or ""),
        "commit_sha": commit["sha"],
    }


async def _discover_from_index(
    client: httpx.AsyncClient,
    *,
    owner: str,
    repo: str,
    commit_sha: str,
) -> list[GitHubPackage] | None:
    index_path = _root_path(_IMPORT_INDEX)
    index = await _raw_json(client, owner, repo, commit_sha, index_path)
    if index is None:
        return None
    entries = index.get("packages")
    if not isinstance(entries, list):
        raise GitHubImportError("Повреждён manifests/import_index.json: packages должен быть списком")
    if len(entries) > _MAX_DISCOVERED_PACKAGES:
        raise GitHubImportError("Import index превышает защитный предел 5000 пакетов")

    found: list[GitHubPackage] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        package_path = str(entry.get("path") or "").strip().strip("/")
        manifest_path = str(entry.get("manifest_path") or "").strip().strip("/")
        if not package_path and manifest_path.endswith("/manifest.json"):
            package_path = manifest_path[: -len("/manifest.json")]
        package_path = _safe_repo_path(package_path, label="path пакета")
        if not manifest_path:
            manifest_path = f"{package_path}/manifest.json"
        manifest_path = _safe_repo_path(manifest_path, label="manifest_path")

        embedded = entry.get("manifest")
        data = embedded if isinstance(embedded, dict) else await _raw_json(
            client,
            owner,
            repo,
            commit_sha,
            _root_path(manifest_path),
        )
        if data is None:
            raise GitHubImportError(f"Manifest из import index не найден: {manifest_path}")
        if _manifest_disabled(data):
            continue
        package = validate_manifest(
            data,
            package_path=_root_path(package_path),
            commit_sha=commit_sha,
        )
        expected_folder = _TYPE_DIRS[package.content_type]
        normalized = str(PurePosixPath(package_path))
        if not normalized.startswith(f"{expected_folder}/"):
            raise GitHubImportError(f"Тип пакета {package.package_id} не соответствует каталогу")
        if package.package_id in seen_ids:
            raise GitHubImportError(f"Дублирующий package_id в import index: {package.package_id}")
        seen_ids.add(package.package_id)
        found.append(package)
    return found


async def _discover_legacy_layout(
    client: httpx.AsyncClient,
    *,
    owner: str,
    repo: str,
    branch: str,
    commit_sha: str,
) -> list[GitHubPackage]:
    """Backward-compatible discovery when repository-level index is absent.

    Directory listings use only three GitHub API calls. Individual manifests are
    downloaded from raw.githubusercontent.com, avoiding one API metadata request
    per package. Repositories with 1000+ packages should maintain import_index.
    """
    found: list[GitHubPackage] = []
    seen_ids: set[str] = set()
    for kind, folder in _TYPE_DIRS.items():
        entries = await _get_json(
            client,
            f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(_root_path(folder), safe='/')}",
            {"ref": branch},
        )
        if not entries:
            continue
        for entry in entries:
            if entry.get("type") != "dir":
                continue
            package_path = str(entry["path"])
            data = await _raw_json(client, owner, repo, commit_sha, f"{package_path}/manifest.json")
            if data is None or _manifest_disabled(data):
                continue
            package = validate_manifest(data, package_path=package_path, commit_sha=commit_sha)
            if package.content_type != kind:
                raise GitHubImportError(f"Тип пакета {package.package_id} не соответствует каталогу")
            if package.package_id in seen_ids:
                raise GitHubImportError(f"Дублирующий package_id: {package.package_id}")
            seen_ids.add(package.package_id)
            found.append(package)
            if len(found) > _MAX_DISCOVERED_PACKAGES:
                raise GitHubImportError("Legacy discovery превышает защитный предел 5000 пакетов")
    return found


async def _load_inventory(identity_id: int) -> dict[str, Any]:
    require_system_owner(identity_id)
    status = await repository_status(identity_id)
    owner, repo = _repo()
    branch, commit_sha = status["branch"], status["commit_sha"]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        indexed = await _discover_from_index(
            client,
            owner=owner,
            repo=repo,
            commit_sha=commit_sha,
        )
        found = indexed if indexed is not None else await _discover_legacy_layout(
            client,
            owner=owner,
            repo=repo,
            branch=branch,
            commit_sha=commit_sha,
        )
    found.sort(key=lambda item: (item.content_type, item.package_id))
    return {
        "items": found,
        "commit_sha": commit_sha,
        "discovery": "index" if indexed is not None else "legacy",
    }


async def _inventory(identity_id: int) -> dict[str, Any]:
    context = _DISCOVERY_CONTEXT.get()
    identity = int(identity_id)
    if context is not None:
        if context.get("identity_id") not in {None, identity}:
            raise GitHubImportForbidden("Контекст GitHub-импорта принадлежит другому пользователю")
        cached = context.get("inventory")
        if isinstance(cached, dict):
            return cached
    inventory = await _load_inventory(identity)
    if context is not None:
        context["identity_id"] = identity
        context["inventory"] = inventory
    return inventory


async def discover_packages(identity_id: int, *, page: int = 1, page_size: int | None = None) -> dict[str, Any]:
    require_system_owner(identity_id)
    inventory = await _inventory(identity_id)
    found: list[GitHubPackage] = inventory["items"]
    size = max(1, min(100, int(page_size or settings.GITHUB_IMPORT_PAGE_SIZE or 50)))
    current_page = max(1, int(page))
    start = max(0, (current_page - 1) * size)
    page_items = await _apply_history_many(found[start : start + size])
    return {
        "items": page_items,
        "page": current_page,
        "page_size": size,
        "total": len(found),
        "commit_sha": inventory["commit_sha"],
        "discovery": inventory["discovery"],
    }


async def find_package(identity_id: int, package_id: str) -> GitHubPackage:
    require_system_owner(identity_id)
    wanted = str(package_id).strip()
    if not _PACKAGE_RE.fullmatch(wanted):
        raise GitHubImportError("Некорректный package_id")

    resolved = _RESOLVED_PACKAGES.get()
    if resolved is not None and wanted in resolved:
        return resolved[wanted]

    inventory = await _inventory(identity_id)
    for package in inventory["items"]:
        if package.package_id == wanted:
            return await _apply_history(package)
    raise GitHubImportError("Пакет не найден")


def _raw_file_url(owner: str, repo: str, commit_sha: str, path: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/"
        f"{quote(commit_sha, safe='')}/{quote(path, safe='/')}"
    )


async def _package_file_url(
    client: httpx.AsyncClient,
    *,
    owner: str,
    repo: str,
    package: GitHubPackage,
    name: str,
) -> str:
    full_path = f"{package.path}/{name}"
    # Public source repositories need no per-file Contents API metadata call.
    # This avoids exhausting the unauthenticated GitHub API limit on comics with
    # hundreds/thousands of pages. When a token is configured we keep the API
    # metadata path for compatibility with private repositories.
    if not str(settings.GITHUB_IMPORT_TOKEN or "").strip():
        return _raw_file_url(owner, repo, package.commit_sha, full_path)
    meta = await _get_json(
        client,
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(full_path, safe='/')}",
        {"ref": package.commit_sha},
    )
    if not meta or meta.get("type") != "file" or not meta.get("download_url"):
        raise GitHubImportError(f"Отсутствует файл: {name}")
    return str(meta["download_url"])


async def download_package(identity_id: int, package: GitHubPackage) -> Path:
    require_system_owner(identity_id)
    owner, repo = _repo()
    root = Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import"))
    free = shutil.disk_usage(root.parent if root.parent.exists() else Path(".")).free
    if free < int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB) * 1024 * 1024:
        raise GitHubImportError("Недостаточно свободного места для временного импорта")
    target = root / f"{package.package_id}-{package.commit_sha[:12]}"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=False)
    total = 0
    limit = int(settings.GITHUB_IMPORT_MAX_PACKAGE_MB) * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=None, follow_redirects=False) as client:
            for name in package.files:
                download_url = await _package_file_url(
                    client,
                    owner=owner,
                    repo=repo,
                    package=package,
                    name=name,
                )
                destination = target.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                async with client.stream("GET", download_url, headers=_headers()) as response:
                    _raise_rate_limit(response)
                    if response.status_code == 404:
                        raise GitHubImportError(f"Отсутствует файл: {name}")
                    response.raise_for_status()
                    with destination.open("wb") as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > limit:
                                raise GitHubImportError("Пакет превышает лимит временного импорта")
                            digest.update(chunk)
                            output.write(chunk)
                if digest.hexdigest() != package.checksums[name]:
                    raise GitHubImportError(f"SHA-256 не совпадает: {name}")
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def cleanup_package(path: str | Path) -> None:
    shutil.rmtree(Path(path), ignore_errors=True)


async def record_import(
    package: GitHubPackage,
    *,
    status: str,
    book_id: int | None = None,
    bytes_total: int = 0,
    error: str = "",
) -> None:
    await ensure_github_import_schema()
    safe_error = str(error or "")[:2000]
    token = str(settings.GITHUB_IMPORT_TOKEN or "")
    if token:
        safe_error = safe_error.replace(token, "[REDACTED]")
    manifest_json = json.dumps(_manifest_snapshot(package), ensure_ascii=False, sort_keys=True)
    async with connect() as db:
        await db.execute(
            """INSERT OR REPLACE INTO github_import_history(
                package_id,content_type,title,version,commit_sha,status,file_count,
                bytes_total,book_id,error,manifest_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                package.package_id,
                package.content_type,
                package.title,
                package.version,
                package.commit_sha,
                status,
                len(package.files),
                int(bytes_total),
                book_id,
                safe_error,
                manifest_json,
                utc_now(),
            ),
        )
        await db.commit()


def _build_import_zip(package: GitHubPackage, source: Path) -> Path:
    if package.content_type == "audiobook":
        raise GitHubImportError("Массовый импорт аудиокниг пока отключён")
    archive = source.parent / f"{source.name}.voxlyra.zip"
    prefix = "Comics" if package.content_type == "comics" else "Books"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name in package.files:
            path = source.joinpath(*PurePosixPath(name).parts)
            if not path.is_file():
                raise GitHubImportError(f"После загрузки отсутствует файл: {name}")
            output.write(path, f"{prefix}/{package.package_id}/{name}")
    return archive


def _require_archive_space(source: Path, *, bytes_total: int, file_count: int) -> None:
    free = shutil.disk_usage(source.parent).free
    reserve = max(0, int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB)) * 1024 * 1024
    zip_overhead = max(16 * 1024 * 1024, max(1, int(file_count)) * 1024)
    if free < int(bytes_total) + reserve + zip_overhead:
        raise GitHubImportError("Недостаточно свободного места для создания временного ZIP")


async def import_package(identity_id: int, package_id: str, *, allow_update: bool = False) -> dict[str, Any]:
    require_system_owner(identity_id)
    package = await find_package(identity_id, package_id)
    if package.content_type not in _BULK_TYPES:
        return {"status": "unsupported_bulk", "package": package, "book_ids": []}
    if package.status == "imported":
        return {"status": "already_imported", "package": package, "book_ids": []}
    if package.status == "update" and not allow_update:
        return {"status": "update_available", "package": package, "book_ids": []}
    work: Path | None = None
    archive: Path | None = None
    try:
        work = await download_package(identity_id, package)
        bytes_total = sum(path.stat().st_size for path in work.rglob("*") if path.is_file())
        _require_archive_space(work, bytes_total=bytes_total, file_count=len(package.files))
        archive = _build_import_zip(package, work)
        from app.services.library_manager import (
            finalize_import_replacement_backups,
            import_library_zip,
            restore_import_replacement_backups,
        )

        result = await import_library_zip(
            archive,
            f"github-{package.package_id}-{package.version}.zip",
            identity_id,
        )
        if result.errors and not result.book_ids:
            await restore_import_replacement_backups(result.batch_id)
            raise GitHubImportError(
                "; ".join(" / ".join(error.reasons) for error in result.errors[:5])
            )
        await finalize_import_replacement_backups(result.batch_id)
        book_id = result.book_ids[0] if len(result.book_ids) == 1 else None
        await record_import(package, status="success", book_id=book_id, bytes_total=bytes_total)
        return {
            "status": "success",
            "package": package,
            "batch_id": result.batch_id,
            "book_ids": list(result.book_ids),
            "added": result.added,
            "replaced": result.replaced,
            "duplicates": result.duplicates,
            "errors": len(result.errors),
        }
    except Exception as exc:
        await record_import(package, status="failed", error=str(exc))
        raise
    finally:
        if archive:
            archive.unlink(missing_ok=True)
        if work:
            cleanup_package(work)


async def import_all_new(identity_id: int, *, max_packages: int = 1000) -> dict[str, Any]:
    require_system_owner(identity_id)
    limit = max(0, min(_MAX_DISCOVERED_PACKAGES, int(max_packages)))
    if limit == 0:
        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "already": 0,
            "updates": [],
            "audio_skipped": [],
            "errors": [],
        }

    page = 1
    selected: list[str] = []
    updates: list[str] = []
    audio: list[str] = []
    resolved: dict[str, GitHubPackage] = {}
    discovery_token = _DISCOVERY_CONTEXT.set({})
    try:
        while len(selected) < limit:
            result = await discover_packages(identity_id, page=page, page_size=100)
            for package in result["items"]:
                resolved[package.package_id] = package
                if package.content_type not in _BULK_TYPES:
                    audio.append(package.package_id)
                elif package.status == "new" and len(selected) < limit:
                    selected.append(package.package_id)
                elif package.status == "update":
                    updates.append(package.package_id)
            if page * result["page_size"] >= result["total"]:
                break
            page += 1

        summary = {
            "total": len(selected),
            "success": 0,
            "failed": 0,
            "already": 0,
            "updates": updates,
            "audio_skipped": audio,
            "errors": [],
        }
        resolved_token = _RESOLVED_PACKAGES.set(resolved)
        try:
            for package_id in selected:
                try:
                    outcome = await import_package(identity_id, package_id)
                    if outcome["status"] == "success":
                        summary["success"] += 1
                    else:
                        summary["already"] += 1
                except Exception as exc:
                    summary["failed"] += 1
                    summary["errors"].append({"package_id": package_id, "error": str(exc)[:500]})
        finally:
            _RESOLVED_PACKAGES.reset(resolved_token)
        return summary
    finally:
        _DISCOVERY_CONTEXT.reset(discovery_token)


async def retry_failed(identity_id: int, *, max_packages: int = 100) -> dict[str, Any]:
    require_system_owner(identity_id)
    limit = max(0, min(100, int(max_packages)))
    if limit == 0:
        return {"total": 0, "success": 0, "failed": 0, "errors": []}

    rows = await import_history(identity_id, status="failed", limit=limit)
    seen: set[str] = set()
    failed_revisions: list[dict[str, str]] = []
    for row in rows:
        package_id = str(row["package_id"])
        if package_id in seen:
            continue
        seen.add(package_id)
        latest = await _last_success(package_id)
        if latest and str(latest["created_at"]) >= str(row["created_at"]):
            continue
        failed_revisions.append(
            {
                "package_id": package_id,
                "version": str(row["version"] or ""),
                "commit_sha": str(row["commit_sha"] or ""),
            }
        )

    summary = {"total": len(failed_revisions), "success": 0, "failed": 0, "errors": []}
    discovery_token = _DISCOVERY_CONTEXT.set({})
    try:
        for failed in failed_revisions:
            package_id = failed["package_id"]
            try:
                current = await find_package(identity_id, package_id)
                if current.version != failed["version"] or current.commit_sha != failed["commit_sha"]:
                    summary["failed"] += 1
                    summary["errors"].append(
                        {
                            "package_id": package_id,
                            "error": "Пакет изменился после неудачной попытки; проверьте текущий diff и подтвердите обновление вручную",
                        }
                    )
                    continue

                # The owner explicitly pressed "retry failed". If the failed
                # revision was an update, this action is a safe confirmation to
                # retry that exact same version+commit, never a newer revision.
                outcome = await import_package(identity_id, package_id, allow_update=True)
                if outcome["status"] in {"success", "already_imported"}:
                    summary["success"] += 1
                else:
                    summary["failed"] += 1
                    summary["errors"].append(
                        {
                            "package_id": package_id,
                            "error": f"Повтор не выполнен: {outcome['status']}",
                        }
                    )
            except Exception as exc:
                summary["failed"] += 1
                summary["errors"].append({"package_id": package_id, "error": str(exc)[:500]})
        return summary
    finally:
        _DISCOVERY_CONTEXT.reset(discovery_token)
