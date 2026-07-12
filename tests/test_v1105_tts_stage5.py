from pathlib import Path

from app.services.tts_providers import TTSSynthesisRequest
from app.services.tts_quality import inspect_audio_quality, migrate_old_tts_cache_once
from app.services.tts_queue import segment_job_key
from app.services.tts_text import TTSTextSegment


def _request(*, high_quality: bool) -> TTSSynthesisRequest:
    segment = TTSTextSegment(
        index=0,
        text="Проверка естественного голоса.",
        kind="narration",
        pause_ms_after=220,
        chars=31,
        digest="abc123",
    )
    return TTSSynthesisRequest(
        session_id="session",
        chapter_id=1,
        segment=segment,
        voice="irina",
        style="natural",
        metadata={"high_quality": high_quality},
    )


def test_quality_control_rejects_tiny_audio(tmp_path: Path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"not audio")
    report = inspect_audio_quality(path, expected_chars=30, use_cache=False)
    assert report.passed is False
    assert "audio_too_small" in report.issues


def test_hq_and_standard_jobs_do_not_share_one_future():
    assert segment_job_key(_request(high_quality=False)) != segment_job_key(_request(high_quality=True))


def test_cache_migration_only_clears_generated_tts(tmp_path: Path):
    generated = tmp_path / "chapter_1"
    generated.mkdir()
    (generated / "old.mp3").write_bytes(b"old")
    segments = tmp_path / "segments-v1105" / "aa"
    segments.mkdir(parents=True)
    (segments / "old.mp3").write_bytes(b"old")
    protected = tmp_path / "author_audio.mp3"
    protected.write_bytes(b"keep")

    result = migrate_old_tts_cache_once(tmp_path)
    assert result["migrated"] is True
    assert not generated.exists()
    assert not (tmp_path / "segments-v1105").exists()
    assert protected.read_bytes() == b"keep"

    second = migrate_old_tts_cache_once(tmp_path)
    assert second["migrated"] is False


def test_stage5_player_cache_and_quality_hooks_are_wired():
    root = Path(__file__).resolve().parents[1]
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    providers = (root / "app/services/tts_providers.py").read_text(encoding="utf-8")
    sessions = (root / "app/services/tts_sessions.py").read_text(encoding="utf-8")
    assert "voxlyra-reader-tts-v2-quality" in script
    assert "migrateReaderTtsDeviceCache" in script
    assert "deleteCachedReaderTtsAudio" in script
    assert "inspect_audio_quality" in providers
    assert "quality_attempt" in providers
    assert "migrate_old_tts_cache_once" in sessions
