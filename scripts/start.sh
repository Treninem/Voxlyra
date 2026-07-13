#!/bin/sh
set -u

export VOSK_MODEL_PATH=/app/storage/tts/models/vosk
export TTS_VOSK_MODEL_DIR=/app/storage/tts/models/vosk
mkdir -p data storage/covers storage/books storage/audio storage/tts storage/tts/models/vosk storage/comics storage/temp storage/legal

# Большая русская модель загружается после старта и больше не блокирует сборку Bothost.
# До её готовности бот доступен, а озвучивание использует резервный Piper.
case "${TTS_VOSK_ENABLED:-true}" in
  0|false|False|FALSE|no|No|NO) ;;
  *)
    (
      lock=storage/tts/.vosk-bootstrap.lock
      if mkdir "$lock" 2>/dev/null; then
        trap 'rmdir "$lock" 2>/dev/null || true' EXIT INT TERM
        python scripts/bootstrap_vosk_tts.py >> storage/tts/vosk-bootstrap.log 2>&1
      fi
    ) &
    ;;
esac

exec python main.py
