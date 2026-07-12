import asyncio
import time
from pathlib import Path

import pytest

from app.services.tts_queue import TTSGenerationQueue
from app.services.tts_sessions import ReaderTTSSession, TTSSessionManager
from app.services.tts_text import prepare_tts_chapter


def _manager_with_session() -> tuple[TTSSessionManager, ReaderTTSSession]:
    manager = TTSSessionManager(TTSGenerationQueue())
    prepared = prepare_tts_chapter('Первое предложение. Второе предложение для проверки перехода.')
    now = int(time.time())
    session = ReaderTTSSession(
        id='session-stage3', user_id=17, chapter_id=33, voice='irina', style='natural',
        high_quality=True, prepared=prepared, providers=('vosk', 'piper'),
        created_at=now, expires_at=now + 3600, last_access_at=now,
    )
    manager._sessions[session.id] = session
    return manager, session


def test_player_events_are_validated_and_visible_in_owner_diagnostics():
    manager, session = _manager_with_session()

    async def scenario():
        await manager.record_client_event(
            session.id, user_id=17, event='segment_transition_complete', segment_index=1,
            player_version='v1.11.1-final-continuity-1', details={'seamless': True},
        )
        return await manager.client_diagnostics()

    data = asyncio.run(scenario())
    assert data['active_sessions'][0]['player_version'] == 'v1.11.1-final-continuity-1'
    assert data['active_sessions'][0]['counters']['segment_transition_complete'] == 1
    assert data['recent_events'][0]['details']['seamless'] is True


def test_unknown_player_event_is_rejected():
    manager, session = _manager_with_session()
    with pytest.raises(ValueError):
        asyncio.run(manager.record_client_event(session.id, user_id=17, event='made_up_event'))


def test_continuity_player_contract_is_wired():
    root = Path(__file__).resolve().parents[1]
    script = (root / 'static/js/app.js').read_text(encoding='utf-8')
    webapp = (root / 'app/webapp.py').read_text(encoding='utf-8')
    sessions = (root / 'app/services/tts_sessions.py').read_text(encoding='utf-8')
    control = (root / 'static/js/control.js').read_text(encoding='utf-8')

    assert "READER_TTS_PLAYER_VERSION = 'v1.11.1-final-continuity-1'" in script
    assert 'crossfadeReaderTtsPlayers' in script
    assert 'maybeStartReaderTtsBoundaryTransition' in script
    assert 'waitForPrefetchedReaderTtsFirst' in script
    assert 'recoverReaderTtsPlayback' in script
    assert 'startReaderTtsWatchdog' in script
    assert '/api/reader/tts/session/{session_id}/event' in webapp
    assert 'record_client_event' in sessions
    assert 'player_contract_version' in webapp
    assert 'Журнал плеера' in control


def test_new_segment_cache_cleanup_uses_current_version():
    root = Path(__file__).resolve().parents[1]
    sessions = (root / 'app/services/tts_sessions.py').read_text(encoding='utf-8')
    script = (root / 'static/js/app.js').read_text(encoding='utf-8')
    assert "cache_root / 'segments-v1110'" in sessions
    assert "voxlyra-reader-tts-v3-continuity" in script
