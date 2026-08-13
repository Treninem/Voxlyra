from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.services import github_source_upload as upload


def _configure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(settings, "BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwxyzABCDE")
    monkeypatch.setattr(settings, "GITHUB_SOURCE_WRITE_ENABLED", True)
    monkeypatch.setattr(settings, "GITHUB_SOURCE_WRITE_TOKEN", "write-secret")
    monkeypatch.setattr(settings, "GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB", 8)
    monkeypatch.setattr(settings, "GITHUB_IMPORT_MIN_FREE_DISK_MB", 0)
    monkeypatch.setattr(settings, "GITHUB_IMPORT_TEMP_ROOT", str(tmp_path / "github-import"))
    monkeypatch.setattr(settings, "GITHUB_IMPORT_REPOSITORY", "Treninem/bookvoxlyra")
    monkeypatch.setattr(settings, "GITHUB_IMPORT_BRANCH", "main")


def _token() -> upload.GitHubSourceUploadToken:
    return upload.verify_github_source_upload_token(
        upload.create_github_source_upload_token(telegram_id=42, chat_id=42)
    )


def test_source_upload_token_is_owner_only_signed_and_purpose_bound(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    with pytest.raises(upload.GitHubSourceUploadError, match="системному владельцу"):
        upload.create_github_source_upload_token(telegram_id=41, chat_id=41)

    token = upload.create_github_source_upload_token(telegram_id=42, chat_id=-10042)
    verified = upload.verify_github_source_upload_token(token)
    assert verified.telegram_id == 42
    assert verified.chat_id == -10042
    with pytest.raises(upload.GitHubSourceUploadError, match="провер"):
        upload.verify_github_source_upload_token(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_source_upload_chunks_are_bound_to_nonce_and_reassemble_exact_bytes(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = _token()
    payload = (b"VoxLyra-source-payload-" * 100_000)[: 2 * 1024 * 1024 + 117]
    meta = upload.create_github_source_upload(token=token, filename="source-ready.zip", total_size=len(payload))
    chunk = int(meta["chunk_size"])
    for index, start in enumerate(range(0, len(payload), chunk)):
        upload.save_github_source_chunk(
            meta["upload_id"], token=token, index=index, data=payload[start : start + chunk]
        )
    assembled = upload.assemble_github_source_upload(meta["upload_id"], token=token)
    assert assembled.read_bytes() == payload

    other_token = _token()
    with pytest.raises(upload.GitHubSourceUploadError, match="недоступна"):
        upload.load_github_source_upload(meta["upload_id"], token=other_token)


def test_source_upload_rejects_wrong_chunk_size_and_cleans_session(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = _token()
    meta = upload.create_github_source_upload(
        token=token, filename="source.zip", total_size=upload.SOURCE_UPLOAD_CHUNK_SIZE_BYTES + 10
    )
    with pytest.raises(upload.GitHubSourceUploadError, match="Размер части"):
        upload.save_github_source_chunk(meta["upload_id"], token=token, index=0, data=b"too-short")
    upload.cleanup_github_source_upload(meta["upload_id"])
    with pytest.raises(upload.GitHubSourceUploadError, match="не найдена"):
        upload.load_github_source_upload(meta["upload_id"], token=token)


def test_stale_cleanup_keeps_active_finish_and_removes_abandoned_session(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = _token()

    active = upload.create_github_source_upload(token=token, filename="active.zip", total_size=8)
    assert upload.claim_github_source_finish(active["upload_id"], token=token) is True
    active_folder = Path(settings.GITHUB_IMPORT_TEMP_ROOT) / "source_web_uploads" / active["upload_id"]
    old = time.time() - 48 * 60 * 60
    os.utime(active_folder / "meta.json", (old, old))
    meta = active_folder / "meta.json"
    data = meta.read_text(encoding="utf-8").replace(
        str(upload.load_github_source_upload(active["upload_id"], token=token)["updated_at"]),
        "2000-01-01T00:00:00+00:00",
    )
    meta.write_text(data, encoding="utf-8")

    abandoned = upload.create_github_source_upload(token=token, filename="old.zip", total_size=8)
    abandoned_folder = Path(settings.GITHUB_IMPORT_TEMP_ROOT) / "source_web_uploads" / abandoned["upload_id"]
    old_meta = abandoned_folder / "meta.json"
    old_data = old_meta.read_text(encoding="utf-8")
    current_updated = upload.load_github_source_upload(abandoned["upload_id"], token=token)["updated_at"]
    old_meta.write_text(old_data.replace(str(current_updated), "2000-01-01T00:00:00+00:00"), encoding="utf-8")

    removed = upload.cleanup_stale_github_source_uploads(max_age_seconds=300)
    assert removed == 1
    assert active_folder.is_dir()
    assert not abandoned_folder.exists()
    upload.release_github_source_finish(active["upload_id"])
    upload.cleanup_github_source_upload(active["upload_id"])


def test_direct_web_routes_upload_and_publish_without_telegram_20mb_path(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    from app import github_source_upload_web as web

    async def fake_publish(identity_id, archive_path):
        assert identity_id == 42
        assert Path(archive_path).read_bytes() == b"PK\x03\x04direct-source"
        return {
            "package_id": "sample-book",
            "commit_sha": "d" * 40,
            "repository": "Treninem/bookvoxlyra",
            "branch": "main",
            "file_count": 7,
            "enabled": True,
        }

    async def no_notify(chat_id, text):
        return None

    monkeypatch.setattr(web, "publish_source_package_zip", fake_publish)
    monkeypatch.setattr(web, "_notify_owner", no_notify)
    token = upload.create_github_source_upload_token(telegram_id=42, chat_id=42)
    headers = {"X-Vox-Source-Token": token}
    app = FastAPI()
    app.include_router(web.router)
    client = TestClient(app)

    page = client.get(f"/github-source-upload?token={token}")
    assert page.status_code == 200
    assert "лимит Telegram Bot API 20 МБ здесь не действует" in page.text

    raw = b"PK\x03\x04direct-source"
    start = client.post(
        "/api/github-source-upload/start",
        headers=headers,
        json={"filename": "sample.zip", "total_size": len(raw)},
    )
    assert start.status_code == 200
    upload_id = start.json()["upload_id"]
    part = client.post(
        f"/api/github-source-upload/{upload_id}/chunk/0",
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=raw,
    )
    assert part.status_code == 200
    finish = client.post(f"/api/github-source-upload/{upload_id}/finish", headers=headers)
    assert finish.status_code == 200
    assert finish.json()["enabled"] is True
    assert finish.json()["package_id"] == "sample-book"


def test_second_finish_cannot_release_first_finish_lock(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    from app import github_source_upload_web as web

    token_text = upload.create_github_source_upload_token(telegram_id=42, chat_id=42)
    token = upload.verify_github_source_upload_token(token_text)
    meta = upload.create_github_source_upload(token=token, filename="sample.zip", total_size=8)
    upload.save_github_source_chunk(meta["upload_id"], token=token, index=0, data=b"12345678")
    assert upload.claim_github_source_finish(meta["upload_id"], token=token) is True

    app = FastAPI()
    app.include_router(web.router)
    client = TestClient(app)
    response = client.post(
        f"/api/github-source-upload/{meta['upload_id']}/finish",
        headers={"X-Vox-Source-Token": token_text},
    )
    assert response.status_code == 409
    assert upload.claim_github_source_finish(meta["upload_id"], token=token) is False
    upload.release_github_source_finish(meta["upload_id"])


def test_runtime_maintenance_cleans_stale_source_uploads():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "app" / "services" / "runtime_performance.py").read_text(encoding="utf-8")
    assert "cleanup_stale_github_source_uploads" in runtime
    assert '"stale_source_uploads_removed"' in runtime


def test_bot_and_runtime_mount_direct_source_upload_contract():
    root = Path(__file__).resolve().parents[1]
    handler = (root / "app" / "handlers" / "github_source_publish.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    web = (root / "app" / "github_source_upload_web.py").read_text(encoding="utf-8")
    assert "create_github_source_upload_token" in handler
    assert "🌐 Загрузить ZIP напрямую" in handler
    assert "github-source-upload?token=" in handler
    assert "application.include_router(github_source_upload_web_router)" in main
    assert 'router.post("/api/github-source-upload/{upload_id}/finish"' in web
    assert "publish_source_package_zip" in web
    assert "if claimed:" in web
