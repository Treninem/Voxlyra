from __future__ import annotations

import os
import time
from pathlib import Path


def test_default_one_x_is_slower_and_has_longer_pauses():
    from app.services.reader_tts import TTS_STYLES

    expressive = TTS_STYLES["expressive"]
    assert float(expressive["length_factor"]) >= 1.15
    assert float(expressive["sentence_silence"]) >= 0.55
    assert float(expressive["noise_scale"]) < 0.70


def test_native_model_rate_is_used_without_loudness_distortion(tmp_path, monkeypatch):
    import app.services.reader_tts as tts
    from app.config import settings

    cache = tmp_path / "cache"
    models = tmp_path / "models"
    models.mkdir()
    for name in ("ru_RU-irina-medium", "ru_RU-dmitri-medium"):
        (models / f"{name}.onnx").write_bytes(b"m" * 2048)
        (models / f"{name}.onnx.json").write_text('{"audio":{"sample_rate":22050}}', encoding="utf-8")

    monkeypatch.setattr(settings, "TTS_CACHE_DIR", str(cache))
    monkeypatch.setattr(settings, "TTS_MODEL_DIR", str(models))
    monkeypatch.setattr(settings, "TTS_ENABLED", True)
    monkeypatch.setattr(tts, "probe_duration_seconds", lambda _: 90)

    commands: list[list[str]] = []

    def fake_which(name: str):
        return f"/usr/bin/{name}" if name in {"piper", "ffmpeg"} else None

    def fake_run(command, **kwargs):
        command = [str(item) for item in command]
        commands.append(command)
        if command[0].endswith("piper"):
            Path(command[command.index("--output-file") + 1]).write_bytes(b"WAV" + b"0" * 4096)
        else:
            Path(command[-1]).write_bytes(b"ID3" + b"0" * 4096)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(tts.shutil, "which", fake_which)
    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    asset = tts._run_generation(189, "Первая фраза. Вторая фраза.", "dmitri", 1.0, "expressive")
    assert asset.path.exists()
    ffmpeg = commands[1]
    assert ffmpeg[ffmpeg.index("-ar") + 1] == "22050"
    assert "-q:a" in ffmpeg and ffmpeg[ffmpeg.index("-q:a") + 1] == "2"
    assert all("loudnorm" not in part for part in ffmpeg)


def test_reader_tts_is_collapsed_and_prefetches_to_device_cache():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/reader.html").read_text(encoding="utf-8")
    script = (root / "static/js/app.js").read_text(encoding="utf-8")

    assert 'id="readerTtsSettingsToggle"' in template
    assert 'id="readerTtsOptions" hidden' in template
    assert 'class="reader-tts-panel is-collapsed"' in template
    assert "caches.open(TTS_DEVICE_CACHE_NAME)" in script
    assert "startNextReaderTtsPrefetch(readerTtsMeta)" in script
    assert "player.addEventListener('play'" in script
    assert "getCachedReaderTtsAudio" in script
    assert "apiFetchWithRetry" in script


def test_server_tts_cache_keeps_only_recent_variants(tmp_path, monkeypatch):
    import app.services.reader_tts as tts
    from app.config import settings

    monkeypatch.setattr(settings, "TTS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "TTS_CACHE_DAYS", 3)
    monkeypatch.setattr(settings, "TTS_MAX_CACHE_MB", 512)
    monkeypatch.setattr(settings, "TTS_MAX_VARIANTS_PER_CHAPTER", 3)

    folder = tmp_path / "chapter_1"
    folder.mkdir()
    for index in range(7):
        path = folder / f"variant_{index}.mp3"
        path.write_bytes(b"ID3" + bytes([index]) * 2048)
        stamp = time.time() - (7 - index)
        os.utime(path, (stamp, stamp))

    tts.cleanup_tts_cache()
    remaining = sorted(folder.glob("*.mp3"))
    assert len(remaining) == 3
    assert {item.name for item in remaining} == {"variant_4.mp3", "variant_5.mp3", "variant_6.mp3"}


def test_comics_roadmap_covers_main_formats_and_reading_modes():
    root = Path(__file__).resolve().parents[1]
    roadmap = (root / "docs/COMICS_MANGA_ROADMAP.md").read_text(encoding="utf-8")
    for token in ("PDF", "CBZ", "CBR", "WebP", "манга", "манхва", "справа налево", "вертикальная лента"):
        assert token in roadmap
