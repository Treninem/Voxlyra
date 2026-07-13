from __future__ import annotations

import asyncio
from pathlib import Path


def test_channel_card_is_premium_and_contains_direct_link():
    from app.services.channel import build_new_book_post

    text = build_new_book_post(
        title='Некромант — Я катастрофа',
        author='Treninem',
        genres=['Фэнтези', 'Тёмный хоррор', 'Приключения'],
        age_limit='16+',
        chapters_count=4853,
        has_audio=True,
        description='История о герое, который получает силу некроманта и меняет судьбу миров.',
        pricing_type='free',
        price_stars=0,
        book_url='https://voxlyra.example/book/7',
    )
    assert '✨ <b>Новая книга на Вокслире</b>' in text
    assert '<b>Некромант — Я катастрофа</b>' in text
    assert '✍️ Treninem' in text
    assert '4853 глав' in text
    assert 'Есть аудиоверсия' in text
    assert 'https://voxlyra.example/book/7' in text
    assert len(text) <= 1024

    repeated = build_new_book_post(
        title='Книга', author='Автор', genres=[], age_limit='12+', chapters_count=1,
        has_audio=False, description='', pricing_type='book', price_stars=20,
        book_url='https://voxlyra.example/book/8', repeated=True,
    )
    assert 'Снова в центре внимания' in repeated
    assert '20 Stars' in repeated


def test_duplicate_guard_normalizes_punctuation_and_case(tmp_path):
    from app.config import settings
    settings.DATABASE_PATH = str(tmp_path / 'duplicate.sqlite3')

    async def scenario():
        from app.db import create_author_profile, create_book, get_author_profile, init_db, upsert_user
        from app.services.duplicate_books import find_book_duplicates

        await init_db()
        user = await upsert_user(18401, 'author', 'Автор')
        await create_author_profile(user['id'], 'Автор', '', 'RU', True)
        profile = await get_author_profile(user['id'])
        first = await create_book(profile['id'], 'Некромант — Я катастрофа!', '', '16+', 'finished', False, 'free', 0)
        matches = await find_book_duplicates(
            title='  НЕКРОМАНТ - я КАТАСТРОФА ',
            author_id=int(profile['id']),
            exclude_book_id=None,
        )
        assert any(item.book_id == first and item.severity == 'block' for item in matches)

    asyncio.run(scenario())


def test_paid_channel_promotion_has_monthly_cooldown(tmp_path):
    from app.config import settings
    settings.DATABASE_PATH = str(tmp_path / 'promotion.sqlite3')

    async def scenario():
        from app.db import (
            create_author_profile, create_book, finish_channel_promotion,
            get_author_profile, get_channel_promotion_availability, init_db,
            reserve_channel_promotion, set_book_publication_status, upsert_user,
        )

        await init_db()
        author_user = await upsert_user(18411, 'author', 'Автор')
        reader = await upsert_user(18412, 'reader', 'Читатель')
        await create_author_profile(author_user['id'], 'Автор', '', 'RU', True)
        profile = await get_author_profile(author_user['id'])
        book_id = await create_book(profile['id'], 'Книга для канала', '', '16+', 'finished', False, 'free', 0)
        await set_book_publication_status(book_id, 'published')

        promotion_id = await reserve_channel_promotion(book_id, reader['id'], 50)
        await finish_channel_promotion(promotion_id, sent=True)
        availability = await get_channel_promotion_availability(book_id, reader['id'])
        assert availability['allowed'] is False
        assert availability['reason'] == 'cooldown'
        assert availability['available_at']

    asyncio.run(scenario())


def test_owner_upload_publishes_and_regular_author_goes_to_review(tmp_path):
    from app.config import settings
    settings.DATABASE_PATH = str(tmp_path / 'workflow.sqlite3')
    settings.OWNER_IDS = '18421'
    settings.CHANNEL_ID = '@voxlyra_test'
    settings.WEBAPP_URL = 'https://voxlyra.example'

    class FakeBot:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text, reply_markup=None):
            self.messages.append((chat_id, text, reply_markup))

        async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
            self.messages.append((chat_id, caption, reply_markup))

    async def scenario():
        from app.db import (
            add_manual_chapter, create_author_profile, create_book, get_author_profile,
            get_book, init_db, set_book_options, upsert_user,
        )
        from app.services.publication import finish_book_content_workflow

        await init_db()
        owner = await upsert_user(18421, 'owner', 'Владелец')
        author = await upsert_user(18422, 'author', 'Автор')
        await create_author_profile(owner['id'], 'Владелец', '', 'RU', True)
        await create_author_profile(author['id'], 'Автор', '', 'RU', True)
        owner_profile = await get_author_profile(owner['id'])
        author_profile = await get_author_profile(author['id'])

        owner_book = await create_book(owner_profile['id'], 'Книга владельца', 'Описание', '16+', 'finished', False, 'free', 0)
        await add_manual_chapter(owner_book, 'Глава 1', 'Текст ' * 200, is_free=True, price_stars=0)
        await set_book_options(owner_book, 'genres', ['fantasy'])

        fake_bot = FakeBot()
        result = await finish_book_content_workflow(
            bot=fake_bot,
            book_id=owner_book,
            actor_user_id=int(owner['id']),
            actor_telegram_id=18421,
            source='test_upload',
        )
        assert result.workflow_status == 'published'
        assert (await get_book(owner_book))['publication_status'] == 'published'
        assert fake_bot.messages
        assert f'https://voxlyra.example/book/{owner_book}' in fake_bot.messages[0][1]

        author_book = await create_book(author_profile['id'], 'Книга автора', 'Описание', '16+', 'finished', False, 'free', 0)
        await add_manual_chapter(author_book, 'Глава 1', 'Текст ' * 200, is_free=True, price_stars=0)
        regular = await finish_book_content_workflow(
            bot=fake_bot,
            book_id=author_book,
            actor_user_id=int(author['id']),
            actor_telegram_id=18422,
            source='test_upload',
        )
        assert regular.workflow_status == 'review'
        assert (await get_book(author_book))['publication_status'] == 'review'

    asyncio.run(scenario())


def test_v184_search_navigation_and_build_are_bundled():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / 'static/js/app.js').read_text(encoding='utf-8')
    author_js = (root / 'static/js/author.js').read_text(encoding='utf-8')
    owner_py = (root / 'app/handlers/owner.py').read_text(encoding='utf-8')
    diagnostics_py = (root / 'app/services/diagnostics.py').read_text(encoding='utf-8')
    env_example = (root / '.env.example').read_text(encoding='utf-8')

    assert 'exactTitleExists' in app_js
    assert 'catalogEditDistance' in app_js
    assert 'catalogWordSimilarity' in app_js
    assert "params.get('upload') === '1'" in author_js
    assert 'allowDuplicateImport' in author_js
    assert 'owner_build_label()' in owner_py
    assert 'owner_build_label()' in diagnostics_py
    assert 'PROJECT_VERSION=v1.11.3-owner-only' in env_example
