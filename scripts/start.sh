#!/bin/sh
set -u

# The production Bothost project uses port 3000. Respect an explicit platform
# value, but keep the clean runtime bootable with the correct project default.
export PORT="${PORT:-3000}"
export VOSK_MODEL_PATH=/app/storage/tts/models/vosk
export TTS_VOSK_MODEL_DIR=/app/storage/tts/models/vosk
mkdir -p data storage/covers storage/books storage/audio storage/tts storage/tts/models/vosk storage/comics storage/temp storage/legal

# Remove only stale/broken import artifacts. Fresh chunk sessions are preserved
# so a 250+ MB upload can continue after Redeploy instead of starting from zero.
python scripts/cleanup_import_storage.py || true

# Remove only outdated generated legal PDFs. User books, database, receipts and
# other persistent storage files are never touched here.
rm -f \
  storage/legal/voxlyra_author_license_agreement.pdf \
  storage/legal/voxlyra_author_license_agreement.pdf.sha256 \
  storage/legal/voxlyra_author_personal_data_consent.pdf \
  storage/legal/voxlyra_author_personal_data_consent.pdf.sha256 \
  storage/legal/voxlyra_content_rules.pdf \
  storage/legal/voxlyra_content_rules.pdf.sha256 \
  storage/legal/voxlyra_copyright_policy.pdf \
  storage/legal/voxlyra_copyright_policy.pdf.sha256 \
  storage/legal/voxlyra_fees_and_payouts.pdf \
  storage/legal/voxlyra_fees_and_payouts.pdf.sha256 \
  storage/legal/voxlyra_reader_offer.pdf \
  storage/legal/voxlyra_reader_offer.pdf.sha256 \
  storage/legal/voxlyra_reader_personal_data_consent.pdf \
  storage/legal/voxlyra_reader_personal_data_consent.pdf.sha256 \
  storage/legal/voxlyra_refund_policy.pdf \
  storage/legal/voxlyra_refund_policy.pdf.sha256

# The large Russian model is optional and is NOT downloaded automatically on a
# fresh/restarted Bothost container. Automatic multi-hundred-MB downloads can
# consume the startup CPU/disk budget and trigger a platform restart loop.
# Set TTS_VOSK_AUTO_BOOTSTRAP=true explicitly after the base service is stable.
# Piper remains available while the Vosk model is absent.
case "${TTS_VOSK_AUTO_BOOTSTRAP:-false}" in
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
