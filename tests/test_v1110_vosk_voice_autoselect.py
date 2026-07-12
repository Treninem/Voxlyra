from pathlib import Path
from types import SimpleNamespace

import app.services.tts_providers as providers
from app.config import settings


def _reset_profile_cache():
    providers._VOSK_PROFILE_CACHE = None


def test_vosk_candidates_are_clean_and_bounded():
    assert providers._parse_vosk_candidates('0, 1, 1, 9, -2, x', (2,)) == (0, 1, 4)
    assert providers._parse_vosk_candidates('', (3, 4)) == (3, 4)


def test_manual_vosk_selection_persists(monkeypatch, tmp_path):
    profile_path = tmp_path / 'profile.json'
    monkeypatch.setattr(settings, 'TTS_VOSK_PROFILE_PATH', str(profile_path))
    _reset_profile_cache()

    profile = providers.set_vosk_voice_selection('female', 1)
    profile = providers.set_vosk_voice_selection('male', 3)

    assert profile['source'] == 'manual'
    assert profile['selected'] == {'female': 1, 'male': 3}
    assert profile_path.is_file()
    _reset_profile_cache()
    assert providers.get_vosk_voice_profile()['selected'] == {'female': 1, 'male': 3}


def test_voice_mapping_uses_saved_profile(monkeypatch, tmp_path):
    profile_path = tmp_path / 'profile.json'
    monkeypatch.setattr(settings, 'TTS_VOSK_PROFILE_PATH', str(profile_path))
    _reset_profile_cache()
    providers.set_vosk_voice_selection('female', 0)
    providers.set_vosk_voice_selection('male', 4)

    assert providers._vosk_speaker_for_voice('irina') == 0
    assert providers._vosk_speaker_for_voice('dmitri') == 4


def test_candidate_score_rejects_broken_audio():
    broken = SimpleNamespace(passed=False, issues=['audio_too_small'], duration_ms=0)
    stable = SimpleNamespace(
        passed=True,
        issues=[],
        duration_ms=10_000,
        mean_volume_db=-20.0,
        peak_volume_db=-3.0,
        silence_ratio=0.02,
        max_silence_ms=400,
    )
    assert providers._score_vosk_candidate(broken, expected_chars=120, elapsed_seconds=1.0) < 0
    assert providers._score_vosk_candidate(stable, expected_chars=120, elapsed_seconds=1.0) > 80


def test_owner_ui_contains_real_tts_diagnostics():
    root = Path(__file__).resolve().parents[1]
    control_js = (root / 'static/js/control.js').read_text(encoding='utf-8')
    webapp = (root / 'app/webapp.py').read_text(encoding='utf-8')
    assert "sectionButton('tts', 'Озвучивание'" in control_js
    assert '/api/control/tts-diagnostics' in control_js
    assert '/api/control/tts-vosk/benchmark' in control_js
    assert '/api/control/tts-vosk/selection' in control_js
    assert '@app.get("/api/control/tts-diagnostics")' in webapp
    assert '@app.get("/api/control/tts-vosk/sample/{speaker_id}")' in webapp


def test_env_documents_autoselection_settings():
    root = Path(__file__).resolve().parents[1]
    env = (root / '.env.example').read_text(encoding='utf-8')
    assert 'TTS_VOSK_AUTO_SELECT=true' in env
    assert 'TTS_VOSK_FEMALE_CANDIDATES=0,1,2' in env
    assert 'TTS_VOSK_MALE_CANDIDATES=3,4' in env
