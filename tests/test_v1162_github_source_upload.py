from __future__ import annotations

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
    token_text = upload.create_github_source_upload_token(telegram_id=42, chat_id=42)
    token = upload.verify_github_source_upload_token(token_text)
    payload = (b"VoxLyra-source-payload-" * 100_000)[: 2 * 1024 * 1024 + 117]
    meta = upload.create_github_source_upload(token=token, filename="source-ready.zip", total_size=len(payload))
    chunk = int(meta["chunk_size"])
    for index, start in enumerate(range(0, len(payload), chunk)):
        upload.save_github_source_chunk(
            meta["upload_id"], token=token, index=index, data=payload[start : start + chunk]
        )
    assembled = upload.assemble_github_source_upload(meta["upload_id"], token=token)
    assert assembled.read_bytes() == payload

    other_token = upload.verify_github_source_upload_token(
        upload.create_github_source_upload_token(telegram_id=42, chat_id=42)
    )
    with pytest.raises(upload.GitHubSourceUploadError, match="недоступна"):
        upload.load_github_source_upload(meta["upload_id"], token=other_token)


def test_source_upload_rejects_wrong_chunk_size_and_cleans_session(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    token = upload.verify_github_source_upload_token(
        upload.create_github_source_upload_token(telegram_id=42, chat_id=42)
    )
    meta = upload.create_github_source_upload(
        token=token, filename="source.zip", total_size=upload.SOURCE_UPLOAD_CHUNK_SIZE_BYTES + 10
    )
    with pytest.raises(upload.GitHubSourceUploadError, match="Размер части"):
        upload.save_github_source_chunk(meta["upload_id"], token=token, index=0, data=b"too-short")
    upload.cleanup_github_source_upload(meta["upload_id"])
    with pytest.raises(upload.GitHubSourceUploadError, match="не найдена"):
        upload.load_github_source_upload(meta["upload_id"], token=token)


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
