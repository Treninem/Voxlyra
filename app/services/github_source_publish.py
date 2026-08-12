from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.services.github_import import GitHubImportError, require_system_owner, validate_manifest

_MAX_FILES = 20_000
_MAX_COMPRESSION_RATIO = 250
_ALLOWED_ROOTS = {"books": "book", "comics": "comics", "audiobooks": "audiobook"}
_INDEX_PATH = "manifests/import_index.json"


class GitHubSourcePublishError(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedSourcePackage:
    package_id: str
    content_type: str
    package_path: str
    manifest: dict[str, Any]
    members: dict[str, str]
    file_count: int
    unpacked_bytes: int


def _enabled() -> bool:
    return bool(getattr(settings, "GITHUB_SOURCE_WRITE_ENABLED", False))


def _write_token() -> str:
    return str(getattr(settings, "GITHUB_SOURCE_WRITE_TOKEN", "") or "").strip()


def _require_source_write(identity_id: int) -> None:
    require_system_owner(identity_id)
    if not _enabled():
        raise GitHubSourcePublishError("Публикация source-пакетов в GitHub выключена")
    if not _write_token():
        raise GitHubSourcePublishError("Не задан GITHUB_SOURCE_WRITE_TOKEN")


def _repo() -> tuple[str, str]:
    value = str(settings.GITHUB_IMPORT_REPOSITORY or "").strip().strip("/")
    if value.count("/") != 1:
        raise GitHubSourcePublishError("Репозиторий source-пакетов не настроен")
    owner, repo = value.split("/", 1)
    if not owner or not repo:
        raise GitHubSourcePublishError("Репозиторий source-пакетов не настроен")
    return owner, repo


def _branch() -> str:
    value = str(settings.GITHUB_IMPORT_BRANCH or "main").strip()
    if not value:
        raise GitHubSourcePublishError("Ветка source-пакетов не настроена")
    return value


def _repo_path(path: str) -> str:
    root = str(settings.GITHUB_IMPORT_ROOT or "").strip().strip("/")
    clean = str(PurePosixPath(path)).strip("/")
    return "/".join(part for part in (root, clean) if part)


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {_write_token()}",
    }


def _redact(value: object) -> str:
    text = str(value or "")
    token = _write_token()
    if token:
        text = text.replace(token, "[REDACTED]")
    return text


def _safe_member(name: str) -> tuple[str, ...]:
    normalized = str(name or "").replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise GitHubSourcePublishError("ZIP содержит небезопасный путь")
    return path.parts


def inspect_source_package_zip(zip_path: str | Path) -> PreparedSourcePackage:
    path = Path(zip_path)
    if not path.is_file():
        raise GitHubSourcePublishError("Source ZIP не найден")
    max_package = max(1, int(getattr(settings, "GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB", 512))) * 1024 * 1024
    max_file = max(1, int(getattr(settings, "GITHUB_SOURCE_WRITE_MAX_FILE_MB", 50))) * 1024 * 1024
    if path.stat().st_size > max_package:
        raise GitHubSourcePublishError("Source ZIP превышает лимит публикации")

    roots: set[tuple[str, str]] = set()
    members: dict[str, str] = {}
    unpacked = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [item for item in archive.infolist() if not item.is_dir()]
            if not infos or len(infos) > _MAX_FILES + 1:
                raise GitHubSourcePublishError("Source ZIP содержит недопустимое количество файлов")
            for info in infos:
                if info.flag_bits & 0x1:
                    raise GitHubSourcePublishError("Зашифрованные source ZIP запрещены")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise GitHubSourcePublishError("Символические ссылки в source ZIP запрещены")
                parts = _safe_member(info.filename)
                if len(parts) < 3 or parts[0].casefold() not in _ALLOWED_ROOTS:
                    raise GitHubSourcePublishError(
                        "Source ZIP должен содержать ровно один пакет в books/, comics/ или audiobooks/"
                    )
                roots.add((parts[0].casefold(), parts[1]))
                relative = PurePosixPath(*parts[2:]).as_posix()
                if relative in members:
                    raise GitHubSourcePublishError(f"Дублирующий файл в source ZIP: {relative}")
                members[relative] = info.filename
                size = max(0, int(info.file_size or 0))
                compressed = max(1, int(info.compress_size or 0))
                if size > max_file:
                    raise GitHubSourcePublishError(f"Файл source-пакета слишком большой: {relative}")
                if size > 10 * 1024 * 1024 and size / compressed > _MAX_COMPRESSION_RATIO:
                    raise GitHubSourcePublishError("Source ZIP имеет подозрительно высокий коэффициент сжатия")
                unpacked += size
                if unpacked > max_package:
                    raise GitHubSourcePublishError("Source ZIP после распаковки превышает лимит публикации")
            if len(roots) != 1:
                raise GitHubSourcePublishError("В одном Source ZIP допускается только один пакет")
            root_name, folder_name = next(iter(roots))
            manifest_member = members.get("manifest.json")
            if not manifest_member:
                raise GitHubSourcePublishError("В корне source-пакета нет manifest.json")
            try:
                manifest = json.loads(archive.read(manifest_member).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GitHubSourcePublishError("manifest.json повреждён или не UTF-8 JSON") from exc
            if not isinstance(manifest, dict):
                raise GitHubSourcePublishError("manifest.json должен содержать JSON-объект")

            package_path = f"{root_name}/{folder_name}"
            try:
                package = validate_manifest(manifest, package_path=package_path, commit_sha="source-upload")
            except GitHubImportError as exc:
                raise GitHubSourcePublishError(str(exc)) from exc
            if package.package_id != folder_name:
                raise GitHubSourcePublishError("package_id должен совпадать с именем папки source-пакета")
            if _ALLOWED_ROOTS[root_name] != package.content_type:
                raise GitHubSourcePublishError("content_type не соответствует каталогу source-пакета")

            payload_members = set(members) - {"manifest.json"}
            if payload_members != set(package.files):
                missing = sorted(set(package.files) - payload_members)
                extra = sorted(payload_members - set(package.files))
                details: list[str] = []
                if missing:
                    details.append("нет: " + ", ".join(missing[:5]))
                if extra:
                    details.append("лишние: " + ", ".join(extra[:5]))
                raise GitHubSourcePublishError(
                    "Содержимое ZIP не совпадает с manifest files" + (": " + "; ".join(details) if details else "")
                )

            for relative in package.files:
                raw = archive.read(members[relative])
                actual = hashlib.sha256(raw).hexdigest()
                if actual != package.checksums[relative]:
                    raise GitHubSourcePublishError(f"SHA-256 не совпадает: {relative}")
            for evidence in ("LICENSE.txt", "SOURCES.txt"):
                try:
                    if not archive.read(members[evidence]).decode("utf-8").strip():
                        raise GitHubSourcePublishError(f"{evidence} не должен быть пустым")
                except UnicodeDecodeError as exc:
                    raise GitHubSourcePublishError(f"{evidence} должен быть UTF-8 текстом") from exc
    except zipfile.BadZipFile as exc:
        raise GitHubSourcePublishError("Source ZIP повреждён") from exc

    return PreparedSourcePackage(
        package_id=package.package_id,
        content_type=package.content_type,
        package_path=package_path,
        manifest=manifest,
        members=members,
        file_count=len(package.files),
        unpacked_bytes=unpacked,
    )


def _decode_contents_json(data: dict[str, Any], *, label: str) -> dict[str, Any]:
    if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise GitHubSourcePublishError(f"GitHub не вернул содержимое {label}")
    try:
        raw = base64.b64decode(data["content"].replace("\n", ""), validate=True)
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubSourcePublishError(f"Не удалось прочитать {label}") from exc
    if not isinstance(parsed, dict):
        raise GitHubSourcePublishError(f"{label} должен содержать JSON-объект")
    return parsed


def build_enabled_import_index(index: dict[str, Any], package: PreparedSourcePackage) -> dict[str, Any]:
    if index.get("schema_version") != 1 or not isinstance(index.get("packages"), list):
        raise GitHubSourcePublishError("manifests/import_index.json имеет неподдерживаемую схему")
    updated = json.loads(json.dumps(index, ensure_ascii=False))
    rows = updated["packages"]
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("path") or "") == package.package_path]
    if len(matches) > 1:
        raise GitHubSourcePublishError("В import_index несколько записей для одного package path")
    entry = matches[0] if matches else None
    if entry is None:
        entry = {}
        rows.append(entry)
    entry.clear()
    entry.update(
        {
            "path": package.package_path,
            "manifest_path": f"{package.package_path}/manifest.json",
            "enabled": True,
        }
    )
    updated["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return updated


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.request(method, url, headers=_headers(), json=json_body, params=params)
    if response.status_code in {401, 403}:
        raise GitHubSourcePublishError("GitHub отклонил source-write token или его права")
    if response.status_code == 404:
        raise GitHubSourcePublishError("GitHub source repository/ref не найден")
    if response.status_code == 422:
        raise GitHubSourcePublishError("GitHub отклонил атомарное обновление ветки; повторите публикацию")
    if response.status_code >= 400:
        raise GitHubSourcePublishError(f"GitHub source API: HTTP {response.status_code}")
    try:
        data = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GitHubSourcePublishError("GitHub source API вернул некорректный JSON") from exc
    if not isinstance(data, dict):
        raise GitHubSourcePublishError("GitHub source API вернул неожиданный ответ")
    return data


async def _create_blob(client: httpx.AsyncClient, api_base: str, raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    data = await _request_json(
        client,
        "POST",
        f"{api_base}/git/blobs",
        json_body={"content": encoded, "encoding": "base64"},
    )
    sha = str(data.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GitHubSourcePublishError("GitHub не вернул SHA созданного blob")
    return sha


async def publish_source_package_zip(identity_id: int, zip_path: str | Path) -> dict[str, Any]:
    """Atomically replace one canonical source package and enable it in the index.

    All blobs are created first. The package tree replacement and import-index
    switch become visible together through one commit/ref fast-forward, so VoxLyra
    can never discover a half-uploaded package.
    """
    _require_source_write(identity_id)
    package = inspect_source_package_zip(zip_path)
    owner, repo = _repo()
    branch = _branch()
    api_base = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
    full_package_prefix = _repo_path(package.package_path).rstrip("/") + "/"
    full_index_path = _repo_path(_INDEX_PATH)

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
            ref = await _request_json(
                client,
                "GET",
                f"{api_base}/git/ref/heads/{quote(branch, safe='')}",
            )
            base_commit = str((ref.get("object") or {}).get("sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40}", base_commit):
                raise GitHubSourcePublishError("Не удалось определить текущий commit source-ветки")
            commit = await _request_json(client, "GET", f"{api_base}/git/commits/{base_commit}")
            base_tree = str((commit.get("tree") or {}).get("sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40}", base_tree):
                raise GitHubSourcePublishError("Не удалось определить tree source-ветки")

            tree_listing = await _request_json(
                client,
                "GET",
                f"{api_base}/git/trees/{base_tree}",
                params={"recursive": "1"},
            )
            if tree_listing.get("truncated") is True:
                raise GitHubSourcePublishError("GitHub tree слишком велик для безопасной атомарной замены пакета")
            existing_paths = {
                str(item.get("path"))
                for item in tree_listing.get("tree", [])
                if isinstance(item, dict)
                and item.get("type") == "blob"
                and str(item.get("path") or "").startswith(full_package_prefix)
            }

            index_contents = await _request_json(
                client,
                "GET",
                f"{api_base}/contents/{quote(full_index_path, safe='/')}",
                params={"ref": branch},
            )
            index = _decode_contents_json(index_contents, label=_INDEX_PATH)
            updated_index = build_enabled_import_index(index, package)

            incoming_paths: set[str] = set()
            tree_entries: list[dict[str, Any]] = []
            with zipfile.ZipFile(Path(zip_path)) as archive:
                for relative, member_name in sorted(package.members.items()):
                    full_path = f"{full_package_prefix}{relative}"
                    raw = archive.read(member_name)
                    blob_sha = await _create_blob(client, api_base, raw)
                    incoming_paths.add(full_path)
                    tree_entries.append(
                        {"path": full_path, "mode": "100644", "type": "blob", "sha": blob_sha}
                    )

            index_raw = (json.dumps(updated_index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            index_sha = await _create_blob(client, api_base, index_raw)
            tree_entries.append(
                {"path": full_index_path, "mode": "100644", "type": "blob", "sha": index_sha}
            )
            for stale in sorted(existing_paths - incoming_paths):
                tree_entries.append({"path": stale, "mode": "100644", "type": "blob", "sha": None})

            new_tree = await _request_json(
                client,
                "POST",
                f"{api_base}/git/trees",
                json_body={"base_tree": base_tree, "tree": tree_entries},
            )
            tree_sha = str(new_tree.get("sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
                raise GitHubSourcePublishError("GitHub не вернул SHA нового source tree")
            new_commit = await _request_json(
                client,
                "POST",
                f"{api_base}/git/commits",
                json_body={
                    "message": f"Publish VoxLyra source package {package.package_id}",
                    "tree": tree_sha,
                    "parents": [base_commit],
                },
            )
            commit_sha = str(new_commit.get("sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
                raise GitHubSourcePublishError("GitHub не вернул SHA нового source commit")
            await _request_json(
                client,
                "PATCH",
                f"{api_base}/git/refs/heads/{quote(branch, safe='')}",
                json_body={"sha": commit_sha, "force": False},
            )
    except GitHubSourcePublishError:
        raise
    except Exception as exc:
        raise GitHubSourcePublishError(_redact(exc)) from exc

    return {
        "package_id": package.package_id,
        "content_type": package.content_type,
        "package_path": package.package_path,
        "file_count": package.file_count,
        "bytes_total": package.unpacked_bytes,
        "repository": f"{owner}/{repo}",
        "branch": branch,
        "commit_sha": commit_sha,
        "enabled": True,
    }
