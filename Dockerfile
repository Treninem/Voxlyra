FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIPER_VOICE_DIR=/opt/voxlyra-voices
ENV VOSK_MODEL_PATH=/app/storage/tts/models/vosk
ENV TTS_VOSK_MODEL_DIR=/app/storage/tts/models/vosk
ENV TTS_VOSK_MODEL_NAME=vosk-model-tts-ru-0.9-multi

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates libarchive13 fonts-dejavu-core tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN set -eux; \
    pip install --no-cache-dir -r requirements.txt; \
    mkdir -p "$PIPER_VOICE_DIR"; \
    success=0; \
    for attempt in 1 2 3; do \
      if python -m piper.download_voices \
        --download-dir "$PIPER_VOICE_DIR" \
        ru_RU-irina-medium ru_RU-dmitri-medium; then \
        success=1; \
        break; \
      fi; \
      sleep 5; \
    done; \
    test "$success" = "1"; \
    test -s "$PIPER_VOICE_DIR/ru_RU-irina-medium.onnx"; \
    test -s "$PIPER_VOICE_DIR/ru_RU-irina-medium.onnx.json"; \
    test -s "$PIPER_VOICE_DIR/ru_RU-dmitri-medium.onnx"; \
    test -s "$PIPER_VOICE_DIR/ru_RU-dmitri-medium.onnx.json"

COPY . .

EXPOSE 3000

# Give first database migrations time to finish. The probe checks only that the
# HTTP process is alive; strict application readiness is reported in JSON.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import json,os,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','3000')+'/health', timeout=3)); raise SystemExit(0 if data.get('ok') else 1)" || exit 1

RUN mkdir -p data storage/covers storage/books storage/audio storage/tts storage/tts/models/vosk storage/comics storage/temp storage/legal \
    && chmod +x scripts/start.sh

CMD ["sh", "scripts/start.sh"]
