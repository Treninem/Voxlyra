from pathlib import Path

from app.services.tts_text import prepare_tts_chapter


def test_first_segment_is_short_and_chapter_is_split():
    text = ("— Это первая реплика, — сказал герой. "
            "Она должна начаться быстро и не ждать всю главу.\n\n" +
            "Длинное повествование продолжалось. " * 80)
    prepared = prepare_tts_chapter(text, first_max_chars=220, target_chars=360, max_chars=620)
    assert len(prepared.segments) > 3
    assert prepared.segments[0].chars <= 220
    assert all(segment.chars <= 620 for segment in prepared.segments)


def test_segmented_routes_and_access_checks_exist():
    root = Path(__file__).resolve().parents[1]
    webapp = (root / "app/webapp.py").read_text(encoding="utf-8")
    assert '/api/reader/{chapter_id}/tts/session' in webapp
    assert '/api/reader/tts/session/{session_id}' in webapp
    assert '/media/reader-tts/session/{session_id}/{segment_index}.mp3' in webapp
    assert 'validate_segment_media_token' in webapp
    assert 'await _chapter_access' in webapp


def test_dual_buffer_and_seamless_next_chapter_are_wired():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/reader.html").read_text(encoding="utf-8")
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    assert 'id="readerTtsPlayer"' in template
    assert 'id="readerTtsBuffer"' in template
    assert 'activatePrefetchedReaderTtsChapter' in script
    assert 'preloadReaderTtsNextSegment' in script
    assert 'readerTtsStandbyPlayer' in script
    assert 'cacheReaderTtsSegment' in script
    assert "player.playbackRate = rate" in script
