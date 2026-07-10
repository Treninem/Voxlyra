from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse


def test_literary_text_keeps_dialogue_and_adds_safe_pauses():
    from app.services.reader_tts import prepare_literary_text

    text = prepare_literary_text(
        "<p>- Ты вернёшься?</p><p>Он помолчал... Потом ответил: да!</p>"
        "<aside>Реклама</aside><p>***</p><p>Новая сцена</p>"
    )
    assert "— Ты вернёшься?" in text
    assert "Он помолчал… Потом ответил: да!" in text
    assert "Реклама" not in text
    assert "***" not in text
    assert "Новая сцена." in text
    assert "\n\n" in text


def test_natural_tts_profiles_and_signed_url(monkeypatch):
    from app.config import settings
    from app.services.reader_tts import (
        available_rates,
        available_styles,
        available_voices,
        build_media_url,
        validate_media_token,
        validate_rate,
    )

    monkeypatch.setattr(settings, "TTS_SIGNING_SECRET", "v188-secret")
    assert {item["code"] for item in available_voices()} == {"irina", "dmitri"}
    assert {item["code"] for item in available_styles()} == {"natural", "expressive", "calm"}
    assert validate_rate(0.79) == 0.75
    assert 1.45 in available_rates()

    url = build_media_url(
        user_id=8,
        chapter_id=18,
        voice="irina",
        rate=0.75,
        style="expressive",
        lifetime_seconds=600,
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert query["rate"] == ["0.75"]
    assert query["style"] == ["expressive"]
    assert validate_media_token(
        user_id=8,
        chapter_id=18,
        voice="irina",
        rate=0.75,
        style="expressive",
        expires_at=int(query["exp"][0]),
        signature=query["sig"][0],
    )
    assert not validate_media_token(
        user_id=8,
        chapter_id=18,
        voice="irina",
        rate=1.0,
        style="expressive",
        expires_at=int(query["exp"][0]),
        signature=query["sig"][0],
    )


def test_piper_generation_uses_native_speed_and_expression(tmp_path, monkeypatch):
    import app.services.reader_tts as tts
    from app.config import settings

    cache = tmp_path / "cache"
    models = tmp_path / "models"
    models.mkdir()
    model = models / "ru_RU-irina-medium.onnx"
    config = models / "ru_RU-irina-medium.onnx.json"
    model.write_bytes(b"m" * 2048)
    config.write_text("{}", encoding="utf-8")
    (models / "ru_RU-dmitri-medium.onnx").write_bytes(b"m" * 2048)
    (models / "ru_RU-dmitri-medium.onnx.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(settings, "TTS_CACHE_DIR", str(cache))
    monkeypatch.setattr(settings, "TTS_MODEL_DIR", str(models))
    monkeypatch.setattr(settings, "TTS_ENABLED", True)
    monkeypatch.setattr(tts, "probe_duration_seconds", lambda _: 123)

    commands: list[list[str]] = []

    def fake_which(name: str):
        return f"/usr/bin/{name}" if name in {"piper", "ffmpeg"} else None

    def fake_run(command, **kwargs):
        command = [str(item) for item in command]
        commands.append(command)
        if command[0].endswith("piper"):
            output = Path(command[command.index("--output-file") + 1])
            output.write_bytes(b"WAV" + b"0" * 4096)
        else:
            Path(command[-1]).write_bytes(b"ID3" + b"0" * 4096)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(tts.shutil, "which", fake_which)
    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    asset = tts._run_generation(
        77,
        "— Ты слышишь меня?\n\nОн ответил не сразу. Потом тихо сказал: да.",
        "irina",
        0.75,
        "expressive",
    )
    assert asset.path.exists()
    assert asset.duration_seconds == 123
    assert asset.rate == 0.75
    assert asset.style == "expressive"

    piper_command = commands[0]
    assert "--length-scale" in piper_command
    length_scale = float(piper_command[piper_command.index("--length-scale") + 1])
    assert length_scale > 1.3
    silence = float(piper_command[piper_command.index("--sentence-silence") + 1])
    assert silence >= 0.4
    assert all("atempo" not in item for command in commands for item in command)


def test_reader_does_not_distort_generated_voice_in_browser():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/reader.html").read_text(encoding="utf-8")
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")

    assert 'id="readerTtsStyle"' in template
    assert "С выражением" in template
    assert "player.playbackRate = 1" in script
    assert "&rate=${encodeURIComponent(rate)}&style=${encodeURIComponent(style)}" in script
    assert "piper-tts==1.4.2" in requirements
    assert "piper.download_voices" in dockerfile
    assert "ru_RU-irina-medium" in dockerfile
    assert "ru_RU-dmitri-medium" in dockerfile
