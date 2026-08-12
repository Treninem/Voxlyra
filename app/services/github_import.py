from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.db import connect, utc_now

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ALLOWED_TYPES = {"book", "comics", "audiobook"}
_TYPE_DIRS = {"book": "books", "comics": "comics", "audiobook": "audiobooks"}


class GitHubImportError(RuntimeError): pass
class GitHubImportForbidden(GitHubImportError): pass


@dataclass(slots=True)
class GitHubPackage:
    package_id: str; content_type: str; title: str; language: str; version: str; created_at: str
    files: tuple[str, ...]; checksums: dict[str, str]; path: str; commit_sha: str
    status: str = "new"; current_version: str = ""


def require_system_owner(identity_id: int) -> None:
    if not settings.is_system_owner(int(identity_id)):
        raise GitHubImportForbidden("Недостаточно прав")


def _repo() -> tuple[str, str]:
    value = str(settings.GITHUB_IMPORT_REPOSITORY or "").strip().strip("/")
    if value.count("/") != 1: raise GitHubImportError("Репозиторий импорта не настроен")
    owner, repo = value.split("/", 1)
    if not owner or not repo: raise GitHubImportError("Репозиторий импорта не настроен")
    return owner, repo


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = str(settings.GITHUB_IMPORT_TOKEN or "").strip()
    if token: headers["Authorization"] = f"Bearer {token}"
    return headers


def _root_path(*parts: str) -> str:
    root = str(settings.GITHUB_IMPORT_ROOT or "").strip().strip("/")
    clean = [str(PurePosixPath(p)).strip("/") for p in parts if str(p).strip("/")]
    return "/".join(([root] if root else []) + clean)


def validate_manifest(data: dict[str, Any], *, package_path: str, commit_sha: str) -> GitHubPackage:
    required = ("package_id", "content_type", "title", "language", "version", "files", "checksums", "created_at")
    missing = [key for key in required if key not in data]
    if missing: raise GitHubImportError("Повреждён manifest: отсутствует " + ", ".join(missing))
    package_id, content_type = str(data["package_id"]).strip(), str(data["content_type"]).strip().lower()
    if not _PACKAGE_RE.fullmatch(package_id): raise GitHubImportError("Некорректный package_id")
    if content_type not in _ALLOWED_TYPES: raise GitHubImportError("Неподдерживаемый content_type")
    files = tuple(str(PurePosixPath(str(x))) for x in data["files"])
    if not files or len(files) != len(set(files)): raise GitHubImportError("Manifest должен содержать уникальный непустой список files")
    for name in files:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or str(p) in {".", ""}: raise GitHubImportError("Небезопасный путь в manifest")
    checksums = {str(PurePosixPath(str(k))): str(v).lower().strip() for k, v in dict(data["checksums"]).items()}
    for name in files:
        if not re.fullmatch(r"[0-9a-f]{64}", checksums.get(name, "")): raise GitHubImportError(f"Нет корректного SHA-256 для {name}")
    return GitHubPackage(package_id, content_type, str(data["title"]).strip(), str(data["language"]).strip(), str(data["version"]).strip(), str(data["created_at"]).strip(), files, checksums, package_path, commit_sha)


async def ensure_github_import_schema() -> None:
    async with connect() as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS github_import_history(id INTEGER PRIMARY KEY AUTOINCREMENT, package_id TEXT NOT NULL, content_type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', version TEXT NOT NULL, commit_sha TEXT NOT NULL, status TEXT NOT NULL, file_count INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0, book_id INTEGER, error TEXT, created_at TEXT NOT NULL, UNIQUE(package_id, version, commit_sha, status))""")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_github_import_package ON github_import_history(package_id, id DESC)")
        await db.commit()


async def _last_success(package_id: str):
    await ensure_github_import_schema()
    async with connect() as db:
        cur = await db.execute("SELECT * FROM github_import_history WHERE package_id=? AND status='success' ORDER BY id DESC LIMIT 1", (package_id,))
        return await cur.fetchone()


async def import_history(identity_id: int, *, status: str = "", limit: int = 30) -> list[dict[str, Any]]:
    require_system_owner(identity_id); await ensure_github_import_schema()
    sql = "SELECT * FROM github_import_history"; args: list[Any] = []
    if status: sql += " WHERE status=?"; args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(max(1, min(100, int(limit))))
    async with connect() as db:
        cur = await db.execute(sql, tuple(args)); rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None) -> Any:
    response = await client.get(url, headers=_headers(), params=params)
    if response.status_code == 404: return None
    response.raise_for_status(); return response.json()


async def repository_status(identity_id: int) -> dict[str, Any]:
    require_system_owner(identity_id); owner, repo = _repo(); branch = str(settings.GITHUB_IMPORT_BRANCH or "main").strip()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        commit = await _get_json(client, f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/commits/{quote(branch)}")
    if not commit: raise GitHubImportError("Ветка GitHub не найдена")
    return {"repository": f"{owner}/{repo}", "branch": branch, "root": str(settings.GITHUB_IMPORT_ROOT or ""), "commit_sha": commit["sha"]}


async def discover_packages(identity_id: int, *, page: int = 1, page_size: int | None = None) -> dict[str, Any]:
    require_system_owner(identity_id); status = await repository_status(identity_id); owner, repo = _repo()
    branch, commit_sha = status["branch"], status["commit_sha"]; size = max(1, min(100, int(page_size or settings.GITHUB_IMPORT_PAGE_SIZE or 50))); found: list[GitHubPackage] = []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        for content_type, folder in _TYPE_DIRS.items():
            path = _root_path(folder); entries = await _get_json(client, f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}", {"ref": branch})
            if not entries: continue
            for entry in entries:
                if entry.get("type") != "dir": continue
                package_path = str(entry["path"]); manifest = await _get_json(client, f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(package_path + '/manifest.json', safe='/')}", {"ref": commit_sha})
                if not manifest or manifest.get("type") != "file": continue
                raw = await client.get(manifest["download_url"], headers=_headers()); raw.raise_for_status(); package = validate_manifest(raw.json(), package_path=package_path, commit_sha=commit_sha)
                if package.content_type != content_type: raise GitHubImportError(f"Тип пакета {package.package_id} не соответствует каталогу")
                previous = await _last_success(package.package_id)
                if previous: package.current_version = str(previous["version"]); package.status = "imported" if package.current_version == package.version else "update"
                found.append(package)
    found.sort(key=lambda item: (item.content_type, item.package_id)); start = max(0, (max(1, int(page)) - 1) * size); selected = found[start:start + size]
    return {"items": selected, "page": max(1, int(page)), "page_size": size, "total": len(found), "commit_sha": commit_sha}


async def find_package(identity_id: int, package_id: str) -> GitHubPackage:
    require_system_owner(identity_id); wanted = str(package_id).strip()
    if not _PACKAGE_RE.fullmatch(wanted): raise GitHubImportError("Некорректный package_id")
    page = 1
    while True:
        result = await discover_packages(identity_id, page=page, page_size=100)
        for package in result["items"]:
            if package.package_id == wanted: return package
        if page * result["page_size"] >= result["total"]: break
        page += 1
    raise GitHubImportError("Пакет не найден")


async def download_package(identity_id: int, package: GitHubPackage) -> Path:
    require_system_owner(identity_id); owner, repo = _repo(); temp_root = Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import"))
    free = shutil.disk_usage(temp_root.parent if temp_root.parent.exists() else Path(".")).free
    if free < int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB) * 1024 * 1024: raise GitHubImportError("Недостаточно свободного места для временного импорта")
    target = temp_root / f"{package.package_id}-{package.commit_sha[:12]}"
    if target.exists(): shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=False); total = 0; limit = int(settings.GITHUB_IMPORT_MAX_PACKAGE_MB) * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=None, follow_redirects=False) as client:
            for name in package.files:
                api = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(package.path + '/' + name, safe='/')}"; meta = await _get_json(client, api, {"ref": package.commit_sha})
                if not meta or meta.get("type") != "file" or not meta.get("download_url"): raise GitHubImportError(f"Отсутствует файл: {name}")
                destination = target.joinpath(*PurePosixPath(name).parts); destination.parent.mkdir(parents=True, exist_ok=True); digest = hashlib.sha256()
                async with client.stream("GET", meta["download_url"], headers=_headers()) as response:
                    response.raise_for_status()
                    with destination.open("wb") as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > limit: raise GitHubImportError("Пакет превышает лимит временного импорта")
                            digest.update(chunk); output.write(chunk)
                if digest.hexdigest() != package.checksums[name]: raise GitHubImportError(f"SHA-256 не совпадает: {name}")
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True); raise


def cleanup_package(path: str | Path) -> None: shutil.rmtree(Path(path), ignore_errors=True)


async def record_import(package: GitHubPackage, *, status: str, book_id: int | None = None, bytes_total: int = 0, error: str = "") -> None:
    await ensure_github_import_schema(); safe_error = str(error or "")[:2000]; token = str(settings.GITHUB_IMPORT_TOKEN or "")
    if token: safe_error = safe_error.replace(token, "[REDACTED]")
    async with connect() as db:
        await db.execute("""INSERT OR REPLACE INTO github_import_history(package_id, content_type, title, version, commit_sha, status, file_count, bytes_total, book_id, error, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (package.package_id, package.content_type, package.title, package.version, package.commit_sha, status, len(package.files), int(bytes_total), book_id, safe_error, utc_now())); await db.commit()
