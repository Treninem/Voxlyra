"""Загружает русскую Vosk-TTS модель без ручной настройки владельцем."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

MODEL_NAME = os.getenv('TTS_VOSK_MODEL_NAME', 'vosk-model-tts-ru-0.9-multi')
MODEL_ROOT = Path(os.getenv('TTS_VOSK_MODEL_DIR', '/opt/voxlyra-voices/vosk'))
MODEL_PATH = MODEL_ROOT / MODEL_NAME
REQUIRED = ('model.onnx', 'config.json', 'dictionary')


def ready() -> bool:
    return all((MODEL_PATH / name).is_file() and (MODEL_PATH / name).stat().st_size > 0 for name in REQUIRED)


def main() -> int:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ['VOSK_MODEL_PATH'] = str(MODEL_ROOT)
    if ready():
        print(f'Vosk-TTS model already ready: {MODEL_PATH}')
        return 0

    from vosk_tts import Model

    last_error: BaseException | None = None
    for attempt in range(1, 4):
        try:
            print(f'Downloading/loading {MODEL_NAME}, attempt {attempt}/3...')
            Model(model_name=MODEL_NAME)
            if ready():
                print(f'Vosk-TTS model ready: {MODEL_PATH}')
                return 0
            raise RuntimeError('download finished but required model files are missing')
        except (Exception, SystemExit) as exc:  # package may call sys.exit on download errors
            last_error = exc
            archive = Path(f'{MODEL_PATH}.zip')
            if archive.exists():
                archive.unlink(missing_ok=True)
            if MODEL_PATH.exists() and not ready():
                shutil.rmtree(MODEL_PATH, ignore_errors=True)
            if attempt < 3:
                time.sleep(5 * attempt)

    print(f'Failed to prepare Vosk-TTS model: {last_error}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
