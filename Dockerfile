FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIPER_VOICE_DIR=/opt/voxlyra-voices

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

EXPOSE 8080
EXPOSE 3000

RUN mkdir -p data storage/covers storage/books storage/audio storage/tts storage/comics storage/temp storage/legal

CMD ["python", "main.py"]
