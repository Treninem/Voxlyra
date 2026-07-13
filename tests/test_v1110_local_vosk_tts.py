from pathlib import Path

from app.services.tts_providers import TTSProviderRegistry, _vosk_speaker_for_voice
from app.services.tts_queue import configured_provider_order


def test_vosk_gender_mapping_is_separate():
    female = _vosk_speaker_for_voice('irina')
    male = _vosk_speaker_for_voice('dmitri')
    assert 0 <= female <= 4
    assert 0 <= male <= 4
    assert female != male


def test_vosk_is_real_local_provider_before_piper():
    registry = TTSProviderRegistry()
    assert 'vosk' in registry.providers
    standard = configured_provider_order(high_quality=False)
    assert standard.index('vosk') < standard.index('piper')


def test_deploy_installs_and_bootstraps_vosk_model_after_start():
    root = Path(__file__).resolve().parents[1]
    requirements = (root / 'requirements.txt').read_text(encoding='utf-8')
    dockerfile = (root / 'Dockerfile').read_text(encoding='utf-8')
    start = (root / 'scripts/start.sh').read_text(encoding='utf-8')
    env = (root / '.env.example').read_text(encoding='utf-8')
    bootstrap = (root / 'scripts/bootstrap_vosk_tts.py').read_text(encoding='utf-8')
    assert 'vosk-tts' in requirements
    assert 'VOSK_MODEL_PATH=/app/storage/tts/models/vosk' in dockerfile
    assert 'python /tmp/bootstrap_vosk_tts.py' not in dockerfile
    assert 'python scripts/bootstrap_vosk_tts.py' in start
    assert 'TTS_VOSK_MODEL_DIR=storage/tts/models/vosk' in env
    assert 'vosk-model-tts-ru-0.9-multi' in bootstrap
    assert "Model(model_name=MODEL_NAME)" in bootstrap


def test_no_manual_url_is_required_for_vosk():
    root = Path(__file__).resolve().parents[1]
    providers = (root / 'app/services/tts_providers.py').read_text(encoding='utf-8')
    env = (root / '.env.example').read_text(encoding='utf-8')
    assert "requires_url': False" in providers
    assert 'TTS_VOSK_ENABLED=true' in env
