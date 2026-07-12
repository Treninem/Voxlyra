"""Проверка собственного TTS-сервера VoxLyra.

Пример:
    python scripts/tts_provider_check.py --url https://tts.example.com --token TOKEN --voice irina
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import httpx

from app.services.tts_quality import inspect_audio_quality


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Базовый адрес TTS-сервера")
    parser.add_argument("--token", default="", help="Bearer-токен")
    parser.add_argument("--voice", choices=("irina", "dmitri"), default="irina")
    parser.add_argument("--style", choices=("natural", "calm", "expressive"), default="natural")
    parser.add_argument(
        "--text",
        default="— Я вернусь до рассвета, — тихо сказал он. За окном медленно стихал летний дождь.",
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=8.0)) as client:
        health = client.get(f"{base_url}/health", headers=headers)
        print("health:", health.status_code, health.text[:300])
        health.raise_for_status()
        response = client.post(
            f"{base_url}/synthesize",
            headers=headers,
            json={
                "text": args.text,
                "voice": args.voice,
                "style": args.style,
                "format": "mp3",
                "sample_rate": 24000,
                "stream": False,
                "segment_kind": "dialogue",
                "quality_attempt": 0,
                "stable_narration": True,
            },
        )
        response.raise_for_status()

    with tempfile.TemporaryDirectory(prefix="voxlyra_tts_check_") as temp_name:
        path = Path(temp_name) / "provider-audio.bin"
        path.write_bytes(response.content)
        report = inspect_audio_quality(path, expected_chars=len(args.text), use_cache=False)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
