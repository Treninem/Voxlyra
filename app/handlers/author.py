from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db import (
    add_audit,
    create_ad_campaign,
    create_author_profile,
    create_book,
    create_promo_code,
    add_manual_chapter,
    add_audio_chapter,
    book_belongs_to_author,
    count_chapters_for_book,
    count_audio_chapters_for_book,
    get_author_profile,
    get_book,
    get_chapter,
    get_audio_chapter,
    list_books_for_author,
    list_chapters_for_book,
    list_audio_chapters_for_book,
    list_author_ad_campaigns,
    get_ad_campaign,
    get_ad_campaign_report,
    list_author_promo_codes,
    submit_book_for_review,
    set_book_options,
    set_chapter_status,
    set_audio_chapter_status,
    upsert_imported_chapters,
    upsert_user,
)
from app.keyboards import (
    age_menu,
    author_book_card_menu,
    author_books_menu,
    author_menu,
    author_audio_list_menu,
    author_audio_menu,
    author_chapter_list_menu,
    author_chapters_menu,
    ad_campaigns_menu,
    ad_campaign_card_menu,
    author_ads_menu,
    author_books_pick_menu,
    audio_view_menu,
    back_to_main,
    book_created_menu,
    chapter_import_confirm_menu,
    chapter_view_menu,
    cover_menu,
    pricing_menu,
    promo_codes_menu,
    writing_status_menu,
    yes_no_menu,
    single_select_menu,
    multi_select_menu,
)
from app.services.book_parser import BookParseError, build_import_report, parse_book_file, split_plain_text_to_chapters
from app.services.import_store import delete_import_preview, load_import_preview, save_import_preview
from app.services.audio_tools import AudioImportError, build_audio_import_report, extract_audio_zip, format_duration, inspect_audio_file
from app.services.pricing import recommend_book_price
from app.catalog_options import BOOK_TYPES, LANGUAGES, GENRES, TROPES, AUDIENCES, CONTENT_WARNINGS, AD_PLACEMENTS, PROMO_DISCOUNTS, label_for, labels_for

router = Router()


class AuthorRegister(StatesGroup):
    pen_name = State()
    bio = State()
    country = State()
    adult = State()




class AddChapterManual(StatesGroup):
    title = State()
    text = State()
    price = State()


class ImportChapters(StatesGroup):
    waiting_file = State()
    confirm = State()


class AddAudioChapter(StatesGroup):
    title = State()
    narrator = State()
    price = State()
    waiting_file = State()


class ImportAudioZip(StatesGroup):
    narrator = State()
    price = State()
    waiting_zip = State()


class CreateAdCampaign(StatesGroup):
    book_id = State()
    placement = State()
    budget = State()


class CreatePromoCode(StatesGroup):
    book_id = State()
    code = State()
    discount = State()
    max_uses = State()


class AddBook(StatesGroup):
    title = State()
    description = State()
    book_type = State()
    language = State()
    genres = State()
    tropes = State()
    audience = State()
    content_warnings = State()
    age_limit = State()
    writing_status = State()
    allow_download = State()
    pricing_type = State()
    price = State()
    cover = State()
    confirm = State()


STATUS_RU = {
    "writing": "пишется",
    "finished": "завершена",
    "frozen": "заморожена",
}

PRICING_RU = {
    "free": "бесплатно",
    "chapters": "платные главы",
    "whole_book": "вся книга",
    "subscription": "подписка позже",
}

PUBLICATION_RU = {
    "draft": "черновик",
    "review": "на проверке",
    "published": "опубликована",
    "hidden": "скрыта",
    "blocked": "заблокирована",
}


@router.callback_query(F.data == "author:menu")
async def author_menu_handler(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if profile:
        text = (
            "<b>Кабинет автора</b>\n\n"
            f"Псевдоним: <b>{profile['pen_name']}</b>\n"
            f"Статус: <b>{profile['status']}</b>\n\n"
            "Профиль сохранён. При добавлении книг бот больше не будет каждый раз спрашивать псевдоним."
        )
    else:
        text = (
            "<b>Кабинет автора</b>\n\n"
            "Зарегистрируйтесь один раз. После этого бот будет использовать сохранённый псевдоним и данные автора."
        )
    await call.message.edit_text(text, reply_markup=author_menu(bool(profile)))
    await call.answer()


@router.callback_query(F.data == "author:register")
async def author_register_start(call: CallbackQuery, state: FSMContext) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if profile:
        await call.message.edit_text("Вы уже зарегистрированы как автор.", reply_markup=author_menu(True))
        await call.answer()
        return
    await state.set_state(AuthorRegister.pen_name)
    await call.message.edit_text("Введите ваш основной псевдоним автора.")
    await call.answer()


@router.message(AuthorRegister.pen_name)
async def author_pen_name(message: Message, state: FSMContext) -> None:
    pen_name = message.text.strip() if message.text else ""
    if len(pen_name) < 2:
        await message.answer("Псевдоним слишком короткий. Введите от 2 символов.")
        return
    await state.update_data(pen_name=pen_name[:80])
    await state.set_state(AuthorRegister.bio)
    await message.answer("Введите короткое описание автора. Можно написать пару строк.")


@router.message(AuthorRegister.bio)
async def author_bio(message: Message, state: FSMContext) -> None:
    bio = message.text.strip() if message.text else ""
    await state.update_data(bio=bio[:1000])
    await state.set_state(AuthorRegister.country)
    await message.answer("Укажите страну. Это нужно для будущих правил выплат, возвратов и налоговых настроек.")


@router.message(AuthorRegister.country)
async def author_country(message: Message, state: FSMContext) -> None:
    country = message.text.strip() if message.text else ""
    await state.update_data(country=country[:80])
    await state.set_state(AuthorRegister.adult)
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, мне есть 18", callback_data="author:adult:yes")
    kb.button(text="Нет", callback_data="author:adult:no")
    kb.adjust(1)
    await message.answer("Подтвердите возраст автора.", reply_markup=kb.as_markup())


@router.callback_query(AuthorRegister.adult, F.data.startswith("author:adult:"))
async def author_adult(call: CallbackQuery, state: FSMContext) -> None:
    is_adult = call.data.endswith(":yes")
    data = await state.get_data()
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await create_author_profile(
        user_id=user["id"],
        pen_name=data["pen_name"],
        bio=data.get("bio", ""),
        country=data.get("country", ""),
        is_adult=is_adult,
    )
    await add_audit(user["id"], "author_registered", "author_profile", str(user["id"]), None, data["pen_name"])
    await state.clear()
    await call.message.edit_text(
        "<b>Профиль автора создан.</b>\n\n"
        "Теперь при добавлении книг бот будет использовать ваш сохранённый псевдоним.",
        reply_markup=author_menu(True),
    )
    await call.answer()


@router.callback_query(F.data == "author:add_book")
async def add_book_start(call: CallbackQuery, state: FSMContext) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if not profile:
        await call.message.edit_text(
            "Сначала нужно создать профиль автора. После регистрации бот не будет каждый раз спрашивать псевдоним.",
            reply_markup=author_menu(False),
        )
        await call.answer()
        return
    await state.set_state(AddBook.title)
    await call.message.edit_text("Введите название книги.")
    await call.answer()


@router.message(AddBook.title)
async def add_book_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое. Введите нормальное название книги.")
        return
    await state.update_data(title=title[:160])
    await state.set_state(AddBook.description)
    await message.answer("Введите описание книги. Лучше 3–8 предложений без лишней воды.")


@router.message(AddBook.description)
async def add_book_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if len(description) < 20:
        await message.answer("Описание слишком короткое. Добавьте хотя бы пару предложений.")
        return
    await state.update_data(description=description[:4000])
    await state.set_state(AddBook.book_type)
    await message.answer(
        "Выберите тип книги. Здесь автор ставит галочку кнопкой, а не пишет вручную.",
        reply_markup=single_select_menu("type", BOOK_TYPES),
    )


@router.callback_query(AddBook.book_type, F.data.startswith("single:type:"))
async def add_book_type(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[-1]
    await state.update_data(book_type=[code])
    await state.set_state(AddBook.language)
    await call.message.edit_text("Выберите язык книги.", reply_markup=single_select_menu("lang", LANGUAGES))
    await call.answer()


@router.callback_query(AddBook.language, F.data.startswith("single:lang:"))
async def add_book_language(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[-1]
    await state.update_data(language=[code], selected_g=[], selected_t=[], selected_a=[], selected_c=[])
    await state.set_state(AddBook.genres)
    await call.message.edit_text("Выберите жанры. Можно отметить несколько вариантов.", reply_markup=multi_select_menu("g", GENRES, set(), page=0))
    await call.answer()


async def _handle_multiselect(call: CallbackQuery, state: FSMContext, *, prefix: str, choices, state_key: str,
                              next_state, next_text: str, next_markup, min_required: int = 0) -> None:
    parts = call.data.split(":")
    action = parts[2] if len(parts) > 2 else "noop"
    data = await state.get_data()
    selected = set(data.get(state_key, []))
    page_key = f"page_{prefix}"
    page = int(data.get(page_key, 0))
    if action == "t" and len(parts) >= 4:
        code = parts[3]
        if code in selected:
            selected.remove(code)
        else:
            selected.add(code)
        await state.update_data(**{state_key: list(selected)})
        await call.message.edit_reply_markup(reply_markup=multi_select_menu(prefix, choices, selected, page=page))
        await call.answer("Выбор обновлён")
        return
    if action == "p" and len(parts) >= 4:
        page = int(parts[3])
        await state.update_data(**{page_key: page})
        await call.message.edit_reply_markup(reply_markup=multi_select_menu(prefix, choices, selected, page=page))
        await call.answer()
        return
    if action == "d":
        if len(selected) < min_required:
            await call.answer(f"Выберите минимум {min_required} пункт(а).", show_alert=True)
            return
        await state.set_state(next_state)
        await call.message.edit_text(next_text, reply_markup=next_markup)
        await call.answer()
        return
    await call.answer()


@router.callback_query(AddBook.genres, F.data.startswith("sel:g:"))
async def add_book_genres(call: CallbackQuery, state: FSMContext) -> None:
    await _handle_multiselect(call, state, prefix="g", choices=GENRES, state_key="selected_g",
                              next_state=AddBook.tropes,
                              next_text="Выберите сюжетные теги и особенности. Это нужно для рекомендаций и рекламы похожих книг.",
                              next_markup=multi_select_menu("t", TROPES, set(), page=0), min_required=1)


@router.callback_query(AddBook.tropes, F.data.startswith("sel:t:"))
async def add_book_tropes(call: CallbackQuery, state: FSMContext) -> None:
    await _handle_multiselect(call, state, prefix="t", choices=TROPES, state_key="selected_t",
                              next_state=AddBook.audience,
                              next_text="Выберите, кому книга больше подходит. Можно отметить несколько вариантов.",
                              next_markup=multi_select_menu("a", AUDIENCES, set(), page=0), min_required=1)


@router.callback_query(AddBook.audience, F.data.startswith("sel:a:"))
async def add_book_audience(call: CallbackQuery, state: FSMContext) -> None:
    await _handle_multiselect(call, state, prefix="a", choices=AUDIENCES, state_key="selected_a",
                              next_state=AddBook.content_warnings,
                              next_text="Выберите предупреждения по содержанию. Если ничего особого нет, отметьте первый пункт.",
                              next_markup=multi_select_menu("c", CONTENT_WARNINGS, set(), page=0), min_required=1)


@router.callback_query(AddBook.content_warnings, F.data.startswith("sel:c:"))
async def add_book_content_warnings(call: CallbackQuery, state: FSMContext) -> None:
    await _handle_multiselect(call, state, prefix="c", choices=CONTENT_WARNINGS, state_key="selected_c",
                              next_state=AddBook.age_limit,
                              next_text="Выберите возрастное ограничение.",
                              next_markup=age_menu("book:age"), min_required=1)


@router.callback_query(AddBook.age_limit, F.data.startswith("book:age:"))
async def add_book_age(call: CallbackQuery, state: FSMContext) -> None:
    age = call.data.split(":")[-1]
    await state.update_data(age_limit=age)
    await state.set_state(AddBook.writing_status)
    await call.message.edit_text("Выберите статус книги.", reply_markup=writing_status_menu())
    await call.answer()


@router.callback_query(AddBook.writing_status, F.data.startswith("book:status:"))
async def add_book_status(call: CallbackQuery, state: FSMContext) -> None:
    status = call.data.split(":")[-1]
    await state.update_data(writing_status=status)
    await state.set_state(AddBook.allow_download)
    await call.message.edit_text(
        "Разрешить скачивание книги после покупки или бесплатного доступа?\n\n"
        "Если запретить, читать можно будет только внутри платформы.",
        reply_markup=yes_no_menu("book:download"),
    )
    await call.answer()


@router.callback_query(AddBook.allow_download, F.data.startswith("book:download:"))
async def add_book_download(call: CallbackQuery, state: FSMContext) -> None:
    allow = call.data.endswith(":yes")
    await state.update_data(allow_download=allow)
    await state.set_state(AddBook.pricing_type)
    await call.message.edit_text("Выберите способ продажи.", reply_markup=pricing_menu())
    await call.answer()


@router.callback_query(AddBook.pricing_type, F.data.startswith("book:pricing:"))
async def add_book_pricing(call: CallbackQuery, state: FSMContext) -> None:
    pricing_type = call.data.split(":")[-1]
    data = await state.get_data()
    recommended = recommend_book_price(description=data.get("description", ""), pricing_type=pricing_type)
    await state.update_data(pricing_type=pricing_type, recommended_price=recommended)
    if pricing_type == "free":
        await state.update_data(price_stars=0)
        await state.set_state(AddBook.cover)
        await call.message.edit_text(
            "Книга будет бесплатной.\n\n"
            "Теперь загрузите обложку изображением или нажмите «Пропустить».",
            reply_markup=cover_menu(),
        )
    else:
        await state.set_state(AddBook.price)
        await call.message.edit_text(
            f"Рекомендуемая цена: <b>{recommended} Stars</b>.\n\n"
            "Введите свою цену числом в Stars. Бот рекомендует, но не навязывает."
        )
    await call.answer()


@router.message(AddBook.price)
async def add_book_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом. Например: 120")
        return
    price = int(raw)
    if price < 0 or price > 100000:
        await message.answer("Цена выглядит неверно. Введите разумное число Stars.")
        return
    await state.update_data(price_stars=price)
    await state.set_state(AddBook.cover)
    await message.answer("Загрузите обложку изображением или нажмите «Пропустить».", reply_markup=cover_menu())


@router.message(AddBook.cover, F.photo)
async def add_book_cover_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(cover_file_id=file_id)
    await state.set_state(AddBook.confirm)
    await _show_book_confirm(message, state)


@router.callback_query(AddBook.cover, F.data == "book:cover:skip")
async def add_book_cover_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(cover_file_id=None)
    await state.set_state(AddBook.confirm)
    await _show_book_confirm(call.message, state)
    await call.answer()


async def _show_book_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (
        "<b>Проверьте книгу</b>\n\n"
        f"Название: <b>{data['title']}</b>\n"
        f"Тип: <b>{', '.join(labels_for('type', data.get('book_type', [])))}</b>\n"
        f"Язык: <b>{', '.join(labels_for('lang', data.get('language', [])))}</b>\n"
        f"Жанры: <b>{', '.join(labels_for('g', data.get('selected_g', []))[:6])}</b>\n"
        f"Теги: <b>{', '.join(labels_for('t', data.get('selected_t', []))[:6])}</b>\n"
        f"Аудитория: <b>{', '.join(labels_for('a', data.get('selected_a', []))[:4])}</b>\n"
        f"Предупреждения: <b>{', '.join(labels_for('c', data.get('selected_c', []))[:4])}</b>\n"
        f"Возраст: <b>{data['age_limit']}</b>\n"
        f"Статус: <b>{STATUS_RU.get(data['writing_status'], data['writing_status'])}</b>\n"
        f"Скачивание: <b>{'разрешено' if data.get('allow_download') else 'запрещено'}</b>\n"
        f"Продажа: <b>{PRICING_RU.get(data['pricing_type'], data['pricing_type'])}</b>\n"
        f"Цена: <b>{data.get('price_stars', 0)} Stars</b>\n"
        f"Обложка: <b>{'загружена' if data.get('cover_file_id') else 'нет'}</b>\n\n"
        "Сохранить черновик?"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Сохранить", callback_data="book:confirm:yes")
    kb.button(text="❌ Отмена", callback_data="book:confirm:no")
    kb.adjust(1)
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(AddBook.confirm, F.data.startswith("book:confirm:"))
async def add_book_confirm(call: CallbackQuery, state: FSMContext) -> None:
    if call.data.endswith(":no"):
        await state.clear()
        await call.message.edit_text("Добавление книги отменено.", reply_markup=author_menu(True))
        await call.answer()
        return

    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if not profile:
        await state.clear()
        await call.message.edit_text("Профиль автора не найден. Создайте профиль заново.", reply_markup=author_menu(False))
        await call.answer()
        return
    data = await state.get_data()
    book_id = await create_book(
        author_id=profile["id"],
        title=data["title"],
        description=data["description"],
        age_limit=data["age_limit"],
        writing_status=data["writing_status"],
        allow_download=bool(data.get("allow_download")),
        pricing_type=data["pricing_type"],
        price_stars=int(data.get("price_stars", 0)),
        cover_file_id=data.get("cover_file_id"),
    )
    option_payload = {
        "book_type": data.get("book_type", []),
        "language": data.get("language", []),
        "genres": data.get("selected_g", []),
        "tropes": data.get("selected_t", []),
        "audience": data.get("selected_a", []),
        "warnings": data.get("selected_c", []),
    }
    for group, codes in option_payload.items():
        await set_book_options(book_id, group, codes)
    await add_audit(user["id"], "book_created", "book", str(book_id), None, data["title"])
    await state.clear()
    await call.message.edit_text(
        "<b>Черновик книги создан.</b>\n\n"
        "Теперь добавьте главы: вручную или загрузкой файла TXT/DOCX/FB2/EPUB/PDF/ZIP.",
        reply_markup=book_created_menu(book_id),
    )
    await call.answer()


@router.callback_query(F.data == "author:books")
async def author_books(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if not profile:
        await call.message.edit_text("Сначала создайте профиль автора.", reply_markup=author_menu(False))
        await call.answer()
        return
    books = await list_books_for_author(user["id"])
    if not books:
        await call.message.edit_text("У вас пока нет книг.", reply_markup=author_books_menu([]))
    else:
        await call.message.edit_text("<b>Мои книги</b>\n\nВыберите книгу.", reply_markup=author_books_menu(books))
    await call.answer()


@router.callback_query(F.data.startswith("author:book:"))
async def author_book_card(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    book = await get_book(book_id)
    if not book:
        await call.answer("Книга не найдена", show_alert=True)
        return
    text = (
        f"<b>{book['title']}</b>\n\n"
        f"Автор: <b>{book['pen_name'] or 'не указан'}</b>\n"
        f"Возраст: <b>{book['age_limit']}</b>\n"
        f"Статус книги: <b>{STATUS_RU.get(book['writing_status'], book['writing_status'])}</b>\n"
        f"Публикация: <b>{PUBLICATION_RU.get(book['publication_status'], book['publication_status'])}</b>\n"
        f"Скачивание: <b>{'разрешено' if book['allow_download'] else 'запрещено'}</b>\n"
        f"Цена: <b>{book['price_stars']} Stars</b>\n\n"
        f"{book['description'] or ''}"
    )
    await call.message.edit_text(text[:4096], reply_markup=author_book_card_menu(book_id, book["publication_status"]))
    await call.answer()


@router.callback_query(F.data.startswith("author:submit_book:"))
async def author_submit_book(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    ok = await submit_book_for_review(book_id, user["id"])
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await add_audit(user["id"], "book_submitted", "book", str(book_id))
    await call.message.edit_text(
        "Книга отправлена на проверку. После одобрения она появится в каталоге и сможет публиковаться в канал.",
        reply_markup=author_menu(True),
    )
    await call.answer("Отправлено")



async def _author_can_edit_book(call_or_message, book_id: int) -> tuple[bool, int]:
    tg = call_or_message.from_user
    user = await upsert_user(tg.id, tg.username, tg.full_name)
    ok = await book_belongs_to_author(book_id, user["id"])
    return ok, user["id"]


@router.callback_query(F.data.startswith("author:chapters:"))
async def author_chapters_menu_handler(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    chapters_count = await count_chapters_for_book(book_id)
    await call.message.edit_text(
        f"<b>Главы книги</b>\n\n"
        f"Книга: <b>{book['title'] if book else book_id}</b>\n"
        f"Сейчас глав: <b>{chapters_count}</b>\n\n"
        "Можно вставить главу текстом или загрузить файл. Бот сам попробует разбить книгу на главы.",
        reply_markup=author_chapters_menu(book_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("chapter:add_manual:"))
async def chapter_add_manual_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(AddChapterManual.title)
    await call.message.edit_text("Введите название главы. Например: Глава 1. Начало")
    await call.answer()


@router.message(AddChapterManual.title)
async def chapter_add_manual_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название главы слишком короткое.")
        return
    await state.update_data(title=title[:160])
    await state.set_state(AddChapterManual.text)
    await message.answer("Теперь вставьте текст главы одним сообщением.")


@router.message(AddChapterManual.text)
async def chapter_add_manual_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 100:
        await message.answer("Текст главы слишком короткий. Вставьте полный текст главы.")
        return
    data = await state.get_data()
    book_id = int(data["book_id"])
    book = await get_book(book_id)
    pricing_type = book["pricing_type"] if book else "free"
    if pricing_type == "free":
        chapter_id = await add_manual_chapter(book_id, data["title"], text, is_free=True, price_stars=0)
        await state.clear()
        user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
        await add_audit(user["id"], "chapter_created_manual", "chapter", str(chapter_id))
        await message.answer("Глава сохранена.", reply_markup=author_chapters_menu(book_id))
        return
    recommended = max(1, int(book["price_stars"] or 3)) if book else 3
    await state.update_data(text=text[:300000], recommended_price=recommended)
    await state.set_state(AddChapterManual.price)
    await message.answer(
        f"Рекомендуемая цена главы: <b>{recommended} Stars</b>.\n\n"
        "Введите цену главы числом или 0, если эта глава бесплатная."
    )


@router.message(AddChapterManual.price)
async def chapter_add_manual_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число. Например: 3")
        return
    price = int(raw)
    if price > 100000:
        await message.answer("Цена выглядит слишком большой.")
        return
    data = await state.get_data()
    book_id = int(data["book_id"])
    chapter_id = await add_manual_chapter(
        book_id,
        data["title"],
        data["text"],
        is_free=price == 0,
        price_stars=price,
    )
    await state.clear()
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await add_audit(user["id"], "chapter_created_manual", "chapter", str(chapter_id))
    await message.answer("Глава сохранена.", reply_markup=author_chapters_menu(book_id))


@router.callback_query(F.data.startswith("chapter:upload:"))
async def chapter_upload_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(ImportChapters.waiting_file)
    await call.message.edit_text(
        "Загрузите файл книги или архив с главами.\n\n"
        "Поддерживаются: TXT, DOCX, FB2, EPUB, PDF, ZIP.\n"
        "Лучше всего подходят TXT, DOCX, FB2 и EPUB. PDF может распознаться хуже, если это скан."
    )
    await call.answer()


@router.message(ImportChapters.waiting_file, F.document)
async def chapter_upload_file(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    ok, user_id = await _author_can_edit_book(message, book_id)
    if not ok:
        await message.answer("Книга не найдена или недоступна.")
        await state.clear()
        return

    doc = message.document
    original_name = doc.file_name or "book.txt"
    ext = Path(original_name).suffix.lower()
    if ext not in {".txt", ".docx", ".fb2", ".epub", ".pdf", ".zip"}:
        await message.answer("Формат не подходит. Загрузите TXT, DOCX, FB2, EPUB, PDF или ZIP.")
        return
    if doc.file_size and doc.file_size > 50 * 1024 * 1024:
        await message.answer("Файл слишком большой для первого импорта. Сейчас лимит 50 МБ.")
        return

    upload_dir = Path("storage/books") / str(book_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_path = upload_dir / f"upload_{message.message_id}{ext}"
    with safe_path.open("wb") as destination:
        await bot.download(doc.file_id, destination=destination)

    try:
        chapters = parse_book_file(safe_path, original_name, temp_dir=Path("storage/temp") / f"zip_{book_id}_{message.message_id}")
    except BookParseError as exc:
        await message.answer(f"Не удалось разобрать файл.\n\nПричина: {exc}")
        return
    except Exception as exc:
        await message.answer(f"Не удалось разобрать файл. Ошибка: {exc}")
        return

    report = build_import_report(chapters)
    preview_path = save_import_preview(chapters)
    await state.update_data(preview_path=preview_path, original_name=original_name)
    await state.set_state(ImportChapters.confirm)

    preview_lines = []
    for item in report["preview"]:
        preview_lines.append(f"{item['number']}. {item['title']} · {item['chars']} зн.")
    problems = "\n".join(f"⚠️ {p}" for p in report["problems"]) or "Явных проблем не найдено."

    book = await get_book(book_id)
    first_free = 999999 if book and book["pricing_type"] == "free" else 3
    default_price = 0 if book and book["pricing_type"] == "free" else int((book["price_stars"] if book else 3) or 3)
    await state.update_data(first_free=first_free, default_price=default_price)

    await add_audit(user_id, "book_file_parsed", "book", str(book_id), None, original_name)
    await message.answer(
        "<b>Предпросмотр импорта</b>\n\n"
        f"Файл: <b>{original_name}</b>\n"
        f"Найдено глав: <b>{report['chapters_count']}</b>\n"
        f"Всего знаков: <b>{report['total_chars']}</b>\n"
        f"Первые бесплатные главы: <b>{'все' if first_free > 1000 else first_free}</b>\n"
        f"Цена остальных глав: <b>{default_price} Stars</b>\n\n"
        "<b>Первые главы:</b>\n"
        f"{chr(10).join(preview_lines) if preview_lines else 'нет'}\n\n"
        "<b>Проверка:</b>\n"
        f"{problems}\n\n"
        "Сохранить эти главы в книгу? Главы с такими же номерами будут обновлены.",
        reply_markup=chapter_import_confirm_menu(book_id),
    )


@router.message(ImportChapters.waiting_file)
async def chapter_upload_not_file(message: Message) -> None:
    await message.answer("Нужно отправить файл документом: TXT, DOCX, FB2, EPUB, PDF или ZIP.")


@router.callback_query(ImportChapters.confirm, F.data.startswith("chapter:import_confirm:"))
async def chapter_import_confirm(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, user_id = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        await state.clear()
        return
    data = await state.get_data()
    chapters = load_import_preview(data.get("preview_path", ""))
    if not chapters:
        await call.answer("Данные импорта не найдены. Загрузите файл заново.", show_alert=True)
        await state.clear()
        return
    saved = await upsert_imported_chapters(
        book_id,
        chapters,
        first_free=int(data.get("first_free", 3)),
        default_price_stars=int(data.get("default_price", 0)),
    )
    await add_audit(user_id, "chapters_imported", "book", str(book_id), None, str(saved))
    delete_import_preview(data.get("preview_path"))
    await state.clear()
    await call.message.edit_text(
        f"Главы сохранены: <b>{saved}</b>.\n\n"
        "Теперь можно отправить книгу на проверку или загрузить ещё главы.",
        reply_markup=author_chapters_menu(book_id),
    )
    await call.answer("Сохранено")


@router.callback_query(ImportChapters.confirm, F.data.startswith("chapter:import_cancel:"))
async def chapter_import_cancel(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    delete_import_preview(data.get("preview_path"))
    book_id = int(call.data.split(":")[-1])
    await state.clear()
    await call.message.edit_text("Импорт отменён. Файл не сохранён в книгу.", reply_markup=author_chapters_menu(book_id))
    await call.answer()


@router.callback_query(F.data.startswith("chapter:list:"))
async def chapter_list_handler(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    chapters = await list_chapters_for_book(book_id)
    if not chapters:
        await call.message.edit_text("Глав пока нет.", reply_markup=author_chapters_menu(book_id))
    else:
        await call.message.edit_text(
            f"<b>Список глав</b>\n\nВсего: <b>{len(chapters)}</b>",
            reply_markup=author_chapter_list_menu(book_id, chapters),
        )
    await call.answer()


@router.callback_query(F.data.startswith("chapter:view:"))
async def chapter_view_handler(call: CallbackQuery) -> None:
    chapter_id = int(call.data.split(":")[-1])
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    ok, _ = await _author_can_edit_book(call, int(chapter["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    text = chapter["text"] or ""
    await call.message.edit_text(
        f"<b>{chapter['number']}. {chapter['title']}</b>\n\n"
        f"Статус: <b>{chapter['status']}</b>\n"
        f"Цена: <b>{0 if chapter['is_free'] else chapter['price_stars']} Stars</b>\n"
        f"Знаков: <b>{len(text)}</b>\n\n"
        f"{text[:1600]}{'...' if len(text) > 1600 else ''}",
        reply_markup=chapter_view_menu(int(chapter["book_id"]), chapter_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("chapter:delete:"))
async def chapter_delete_handler(call: CallbackQuery) -> None:
    chapter_id = int(call.data.split(":")[-1])
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    ok, user_id = await _author_can_edit_book(call, int(chapter["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await set_chapter_status(chapter_id, "deleted")
    await add_audit(user_id, "chapter_deleted", "chapter", str(chapter_id))
    await call.message.edit_text("Глава удалена из книги.", reply_markup=author_chapters_menu(int(chapter["book_id"])))
    await call.answer("Удалено")


@router.callback_query(F.data == "author:audio")
async def author_audio_books(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if not profile:
        await call.message.edit_text("Сначала создайте профиль автора.", reply_markup=author_menu(False))
        await call.answer()
        return
    books = await list_books_for_author(user["id"])
    if not books:
        await call.message.edit_text("Сначала создайте книгу, потом к ней можно добавить аудиоверсию.", reply_markup=author_menu(True))
    else:
        await call.message.edit_text("<b>Аудиокниги</b>\n\nВыберите книгу, к которой нужно добавить аудиоглавы.", reply_markup=author_books_menu(books))
    await call.answer()


@router.callback_query(F.data.startswith("author:audio:"))
async def author_audio_menu_handler(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    audio_count = await count_audio_chapters_for_book(book_id)
    await call.message.edit_text(
        f"<b>Аудиоверсия</b>\n\n"
        f"Книга: <b>{book['title'] if book else book_id}</b>\n"
        f"Аудиоглав: <b>{audio_count}</b>\n\n"
        "Можно загрузить одну аудиоглаву или ZIP с несколькими аудиофайлами. "
        "Поддерживаются MP3, M4A, OGG и WAV.",
        reply_markup=author_audio_menu(book_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("audio:add:"))
async def audio_add_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(AddAudioChapter.title)
    await call.message.edit_text("Введите название аудиоглавы. Например: Глава 1. Начало")
    await call.answer()


@router.message(AddAudioChapter.title)
async def audio_add_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое.")
        return
    await state.update_data(title=title[:160])
    await state.set_state(AddAudioChapter.narrator)
    await message.answer("Введите имя диктора. Если диктор не нужен, напишите: нет")


@router.message(AddAudioChapter.narrator)
async def audio_add_narrator(message: Message, state: FSMContext) -> None:
    narrator = (message.text or "").strip()
    if narrator.lower() in {"нет", "-", "не указано"}:
        narrator = ""
    await state.update_data(narrator=narrator[:120])
    data = await state.get_data()
    book = await get_book(int(data["book_id"]))
    recommended = 0 if book and book["pricing_type"] == "free" else max(5, int((book["price_stars"] or 3) * 2))
    await state.update_data(recommended_price=recommended)
    await state.set_state(AddAudioChapter.price)
    await message.answer(
        f"Рекомендуемая цена аудиоглавы: <b>{recommended} Stars</b>.\n\n"
        "Введите цену числом или 0, если аудиоглава бесплатная."
    )


@router.message(AddAudioChapter.price)
async def audio_add_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом. Например: 7")
        return
    price = int(raw)
    if price > 100000:
        await message.answer("Цена выглядит слишком большой.")
        return
    await state.update_data(price_stars=price)
    await state.set_state(AddAudioChapter.waiting_file)
    await message.answer(
        "Загрузите аудиофайл документом или аудио.\n\n"
        "Поддерживаются MP3, M4A, OGG и WAV. Сейчас файл сохраняется без конвертации; нормализацию громкости добавим отдельно."
    )


async def _save_telegram_audio(message: Message, bot: Bot, state: FSMContext, file_id: str, source_name: str, file_size: int | None) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    ok, user_id = await _author_can_edit_book(message, book_id)
    if not ok:
        await message.answer("Книга не найдена или недоступна.")
        await state.clear()
        return
    ext = Path(source_name).suffix.lower()
    if ext not in {".mp3", ".m4a", ".ogg", ".oga", ".wav"}:
        await message.answer("Формат не подходит. Загрузите MP3, M4A, OGG или WAV.")
        return
    if file_size and file_size > 200 * 1024 * 1024:
        await message.answer("Файл слишком большой. Сейчас лимит 200 МБ на аудиоглаву.")
        return
    upload_dir = Path("storage/audio") / str(book_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_path = upload_dir / f"audio_{message.message_id}{ext}"
    with safe_path.open("wb") as destination:
        await bot.download(file_id, destination=destination)
    try:
        info = inspect_audio_file(safe_path, source_filename=source_name, title=data.get("title"))
    except AudioImportError as exc:
        await message.answer(f"Не удалось принять аудио.\n\nПричина: {exc}")
        return
    audio_id = await add_audio_chapter(
        book_id=book_id,
        title=data["title"],
        file_id=file_id,
        file_path=str(info.path),
        duration_seconds=info.duration_seconds,
        narrator=data.get("narrator") or None,
        source_filename=info.source_filename,
        mime_type=info.mime_type,
        file_size=info.file_size,
        is_free=int(data.get("price_stars", 0)) == 0,
        price_stars=int(data.get("price_stars", 0)),
        sample_seconds=60,
    )
    await add_audit(user_id, "audio_chapter_created", "audio_chapter", str(audio_id), None, source_name)
    await state.clear()
    await message.answer(
        "<b>Аудиоглава сохранена.</b>\n\n"
        f"Название: <b>{data['title']}</b>\n"
        f"Диктор: <b>{data.get('narrator') or 'не указан'}</b>\n"
        f"Длительность: <b>{format_duration(info.duration_seconds)}</b>\n"
        f"Размер: <b>{round(info.file_size / 1024 / 1024, 2)} МБ</b>\n"
        f"Цена: <b>{int(data.get('price_stars', 0))} Stars</b>",
        reply_markup=author_audio_menu(book_id),
    )


@router.message(AddAudioChapter.waiting_file, F.audio)
async def audio_add_audio_message(message: Message, state: FSMContext, bot: Bot) -> None:
    audio = message.audio
    filename = audio.file_name or f"audio_{message.message_id}.mp3"
    await _save_telegram_audio(message, bot, state, audio.file_id, filename, audio.file_size)


@router.message(AddAudioChapter.waiting_file, F.document)
async def audio_add_document_message(message: Message, state: FSMContext, bot: Bot) -> None:
    doc = message.document
    filename = doc.file_name or f"audio_{message.message_id}"
    await _save_telegram_audio(message, bot, state, doc.file_id, filename, doc.file_size)


@router.message(AddAudioChapter.waiting_file)
async def audio_add_wrong_message(message: Message) -> None:
    await message.answer("Нужно отправить аудиофайл: MP3, M4A, OGG или WAV.")


@router.callback_query(F.data.startswith("audio:zip:"))
async def audio_zip_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(ImportAudioZip.narrator)
    await call.message.edit_text("Введите имя диктора для аудиофайлов из ZIP. Если не нужно, напишите: нет")
    await call.answer()


@router.message(ImportAudioZip.narrator)
async def audio_zip_narrator(message: Message, state: FSMContext) -> None:
    narrator = (message.text or "").strip()
    if narrator.lower() in {"нет", "-", "не указано"}:
        narrator = ""
    await state.update_data(narrator=narrator[:120])
    await state.set_state(ImportAudioZip.price)
    await message.answer("Введите цену каждой аудиоглавы из ZIP. Можно 0, если аудио бесплатное.")


@router.message(ImportAudioZip.price)
async def audio_zip_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число. Например: 7")
        return
    await state.update_data(price_stars=int(raw))
    await state.set_state(ImportAudioZip.waiting_zip)
    await message.answer("Загрузите ZIP с аудиофайлами MP3/M4A/OGG/WAV. Бот сохранит их как аудиоглавы по порядку.")


@router.message(ImportAudioZip.waiting_zip, F.document)
async def audio_zip_file(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    ok, user_id = await _author_can_edit_book(message, book_id)
    if not ok:
        await message.answer("Книга не найдена или недоступна.")
        await state.clear()
        return
    doc = message.document
    original_name = doc.file_name or "audio.zip"
    if not original_name.lower().endswith(".zip"):
        await message.answer("Нужен ZIP-архив.")
        return
    if doc.file_size and doc.file_size > 250 * 1024 * 1024:
        await message.answer("ZIP слишком большой. Сейчас лимит 250 МБ.")
        return
    upload_dir = Path("storage/audio") / str(book_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    zip_path = upload_dir / f"audio_zip_{message.message_id}.zip"
    with zip_path.open("wb") as destination:
        await bot.download(doc.file_id, destination=destination)
    try:
        infos = extract_audio_zip(zip_path, upload_dir / f"zip_{message.message_id}")
    except AudioImportError as exc:
        await message.answer(f"Не удалось разобрать ZIP.\n\nПричина: {exc}")
        return
    saved = 0
    for info in infos:
        audio_id = await add_audio_chapter(
            book_id=book_id,
            title=info.title,
            file_id=None,
            file_path=str(info.path),
            duration_seconds=info.duration_seconds,
            narrator=data.get("narrator") or None,
            source_filename=info.source_filename,
            mime_type=info.mime_type,
            file_size=info.file_size,
            is_free=int(data.get("price_stars", 0)) == 0,
            price_stars=int(data.get("price_stars", 0)),
            sample_seconds=60,
        )
        await add_audit(user_id, "audio_chapter_imported_zip", "audio_chapter", str(audio_id), None, info.source_filename)
        saved += 1
    report = build_audio_import_report(infos)
    await state.clear()
    preview_lines = [f"• {item['title']} · {item['duration']} · {item['size_mb']} МБ" for item in report["preview"]]
    problems = "\n".join(f"⚠️ {p}" for p in report["problems"]) or "Явных проблем не найдено."
    await message.answer(
        "<b>ZIP с аудио сохранён.</b>\n\n"
        f"Файлов: <b>{saved}</b>\n"
        f"Общая длительность: <b>{report['total_duration']}</b>\n"
        f"Общий размер: <b>{report['total_size_mb']} МБ</b>\n\n"
        f"{chr(10).join(preview_lines)}\n\n"
        f"<b>Проверка:</b>\n{problems}",
        reply_markup=author_audio_menu(book_id),
    )


@router.message(ImportAudioZip.waiting_zip)
async def audio_zip_wrong_file(message: Message) -> None:
    await message.answer("Нужно отправить ZIP-архив документом.")


@router.callback_query(F.data.startswith("audio:list:"))
async def audio_list_handler(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    audios = await list_audio_chapters_for_book(book_id)
    if not audios:
        await call.message.edit_text("Аудиоглав пока нет.", reply_markup=author_audio_menu(book_id))
    else:
        await call.message.edit_text(f"<b>Список аудиоглав</b>\n\nВсего: <b>{len(audios)}</b>", reply_markup=author_audio_list_menu(book_id, audios))
    await call.answer()


@router.callback_query(F.data.startswith("audio:view:"))
async def audio_view_handler(call: CallbackQuery) -> None:
    audio_id = int(call.data.split(":")[-1])
    audio = await get_audio_chapter(audio_id)
    if not audio:
        await call.answer("Аудиоглава не найдена", show_alert=True)
        return
    ok, _ = await _author_can_edit_book(call, int(audio["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await call.message.edit_text(
        f"<b>{audio['number']}. {audio['title']}</b>\n\n"
        f"Книга: <b>{audio['book_title']}</b>\n"
        f"Диктор: <b>{audio['narrator'] or 'не указан'}</b>\n"
        f"Длительность: <b>{format_duration(audio['duration_seconds'])}</b>\n"
        f"Цена: <b>{0 if audio['is_free'] else audio['price_stars']} Stars</b>\n"
        f"Файл: <b>{audio['source_filename'] or 'не указан'}</b>\n"
        f"Статус: <b>{audio['status']}</b>",
        reply_markup=audio_view_menu(int(audio["book_id"]), audio_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("audio:delete:"))
async def audio_delete_handler(call: CallbackQuery) -> None:
    audio_id = int(call.data.split(":")[-1])
    audio = await get_audio_chapter(audio_id)
    if not audio:
        await call.answer("Аудиоглава не найдена", show_alert=True)
        return
    ok, user_id = await _author_can_edit_book(call, int(audio["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await set_audio_chapter_status(audio_id, "deleted")
    await add_audit(user_id, "audio_chapter_deleted", "audio_chapter", str(audio_id))
    await call.message.edit_text("Аудиоглава удалена из книги.", reply_markup=author_audio_menu(int(audio["book_id"])))
    await call.answer("Удалено")


@router.callback_query(F.data == "author:ads")
async def author_ads_handler(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "<b>📢 Продвижение</b>\n\n"
        "Здесь автор может продвигать книгу внутри платформы: в читалке похожих книг, витрине и аудио-разделе. "
        "Работают кампании, промокоды, отчёты и пополнение рекламного бюджета через Stars.",
        reply_markup=author_ads_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "ad:create")
async def ad_create_start(call: CallbackQuery, state: FSMContext) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    books = await list_books_for_author(user["id"])
    if not books:
        await call.message.edit_text("Сначала создайте книгу.", reply_markup=author_ads_menu())
        await call.answer()
        return
    await state.set_state(CreateAdCampaign.book_id)
    await call.message.edit_text("Выберите книгу для продвижения.", reply_markup=author_books_pick_menu(books, "ad:book"))
    await call.answer()


@router.callback_query(CreateAdCampaign.book_id, F.data.startswith("ad:book:"))
async def ad_create_book(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(CreateAdCampaign.placement)
    await call.message.edit_text(
        "Где показывать рекламу?\n\n"
        "Для рекламы внутри чтения лучше выбрать «Сверху и снизу главы». Подбор всё равно будет идти по жанрам и сюжетным тегам.",
        reply_markup=single_select_menu("adplace", AD_PLACEMENTS, back_callback="author:ads"),
    )
    await call.answer()


@router.callback_query(CreateAdCampaign.placement, F.data.startswith("single:adplace:"))
async def ad_create_placement(call: CallbackQuery, state: FSMContext) -> None:
    placement = call.data.split(":")[-1]
    await state.update_data(placement=placement)
    await state.set_state(CreateAdCampaign.budget)
    await call.message.edit_text(
        "Введите стартовый бюджет рекламы во внутренних показах.\n\n"
        "Позже кампанию можно пополнить через Stars из карточки кампании.\n"
        "Рекомендация для старта: 100–500."
    )
    await call.answer()


@router.message(CreateAdCampaign.budget)
async def ad_create_budget(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число. Например: 200")
        return
    budget = int(raw)
    if budget < 10 or budget > 100000:
        await message.answer("Бюджет должен быть от 10 до 100000 условных показов.")
        return
    data = await state.get_data()
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    book = await get_book(int(data["book_id"]))
    title = f"Продвижение: {book['title'] if book else data['book_id']}"
    campaign_id = await create_ad_campaign(user["id"], int(data["book_id"]), title, data.get("placement", "reader_both"), budget)
    await add_audit(user["id"], "ad_campaign_created", "ad_campaign", str(campaign_id), None, str(budget))
    await state.clear()
    await message.answer(
        "Рекламная кампания создана.\n\n"
        "Она будет участвовать в блоке похожих книг во время чтения, если совпадают жанры, теги или аудитория. Пополнить бюджет можно через Stars в карточке кампании.",
        reply_markup=author_ads_menu(),
    )


@router.callback_query(F.data == "ad:list")
async def ad_list_handler(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    campaigns = await list_author_ad_campaigns(user["id"])
    if not campaigns:
        await call.message.edit_text("Рекламных кампаний пока нет.", reply_markup=author_ads_menu())
    else:
        await call.message.edit_text("<b>📢 Мои кампании</b>", reply_markup=ad_campaigns_menu(campaigns))
    await call.answer()


@router.callback_query(F.data.startswith("ad:card:"))
async def ad_card_handler(call: CallbackQuery) -> None:
    campaign_id = int(call.data.split(":")[-1])
    campaign = await get_ad_campaign(campaign_id)
    if not campaign:
        await call.answer("Кампания не найдена", show_alert=True)
        return
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    books = await list_books_for_author(user["id"])
    if int(campaign["book_id"]) not in {int(book["id"]) for book in books}:
        await call.answer("Недоступно", show_alert=True)
        return
    report = await get_ad_campaign_report(campaign_id)
    left = report.get("left_units", 0)
    await call.message.edit_text(
        f"<b>📢 Рекламная кампания</b>\n\n"
        f"Книга: <b>{campaign['book_title']}</b>\n"
        f"Место: <b>{label_for('adplace', campaign['placement'])}</b>\n"
        f"Статус: <b>{campaign['status']}</b>\n"
        f"Бюджет: <b>{report.get('budget_units', 0)}</b> показов\n"
        f"Потрачено: <b>{report.get('spent_units', 0)}</b>\n"
        f"Остаток: <b>{left}</b>\n"
        f"Показы: <b>{report.get('impressions', 0)}</b>\n"
        f"Клики: <b>{report.get('clicks', 0)}</b>\n"
        f"Пополнено: <b>{report.get('stars_paid', 0)} Stars</b>\n\n"
        "Пополнение через Stars добавляет внутренние показы по текущему курсу платформы.",
        reply_markup=ad_campaign_card_menu(campaign_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ad:report:"))
async def ad_report_handler(call: CallbackQuery) -> None:
    campaign_id = int(call.data.split(":")[-1])
    campaign = await get_ad_campaign(campaign_id)
    report = await get_ad_campaign_report(campaign_id)
    if not campaign or not report:
        await call.answer("Кампания не найдена", show_alert=True)
        return
    await call.message.edit_text(
        f"<b>📊 Отчёт по рекламе</b>\n\n"
        f"Книга: <b>{campaign['book_title']}</b>\n"
        f"Показы: <b>{report['impressions']}</b>\n"
        f"Клики: <b>{report['clicks']}</b>\n"
        f"Потрачено единиц: <b>{report['spent_units']}</b>\n"
        f"Остаток единиц: <b>{report['left_units']}</b>\n"
        f"Оплачено рекламой: <b>{report['stars_paid']} Stars</b>",
        reply_markup=ad_campaign_card_menu(campaign_id),
    )
    await call.answer()


@router.callback_query(F.data == "promo:create")
async def promo_create_start(call: CallbackQuery, state: FSMContext) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    books = await list_books_for_author(user["id"])
    if not books:
        await call.message.edit_text("Сначала создайте книгу.", reply_markup=author_ads_menu())
        await call.answer()
        return
    await state.set_state(CreatePromoCode.book_id)
    await call.message.edit_text("Выберите книгу для промокода.", reply_markup=author_books_pick_menu(books, "promo:book"))
    await call.answer()


@router.callback_query(CreatePromoCode.book_id, F.data.startswith("promo:book:"))
async def promo_create_book(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(CreatePromoCode.code)
    await call.message.edit_text(
        "Введите промокод латиницей или цифрами.\n\n"
        "Пример: START50 или MYBOOK100."
    )
    await call.answer()


@router.message(CreatePromoCode.code)
async def promo_create_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    if len(code) < 3:
        await message.answer("Промокод слишком короткий.")
        return
    await state.update_data(code=code)
    await state.set_state(CreatePromoCode.discount)
    await message.answer("Выберите размер скидки.", reply_markup=single_select_menu("discount", PROMO_DISCOUNTS))


@router.callback_query(CreatePromoCode.discount, F.data.startswith("single:discount:"))
async def promo_create_discount(call: CallbackQuery, state: FSMContext) -> None:
    discount = int(call.data.split(":")[-1])
    await state.update_data(discount=discount)
    await state.set_state(CreatePromoCode.max_uses)
    await call.message.edit_text("Введите лимит использований промокода. Например: 100")
    await call.answer()


@router.message(CreatePromoCode.max_uses)
async def promo_create_max_uses(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число. Например: 100")
        return
    max_uses = int(raw)
    if max_uses < 1 or max_uses > 100000:
        await message.answer("Лимит должен быть от 1 до 100000.")
        return
    data = await state.get_data()
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    try:
        promo_id = await create_promo_code(user["id"], int(data["book_id"]), data["code"], int(data["discount"]), max_uses)
    except Exception as exc:
        await message.answer(f"Промокод не создан: {exc}", reply_markup=author_ads_menu())
        await state.clear()
        return
    await add_audit(user["id"], "promo_code_created", "promo_code", str(promo_id), None, data["code"])
    await state.clear()
    await message.answer("Промокод создан.", reply_markup=author_ads_menu())


@router.callback_query(F.data == "promo:list")
async def promo_list_handler(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    codes = await list_author_promo_codes(user["id"])
    if not codes:
        await call.message.edit_text("Промокодов пока нет.", reply_markup=author_ads_menu())
    else:
        await call.message.edit_text("<b>🎟 Промокоды</b>", reply_markup=promo_codes_menu(codes))
    await call.answer()



@router.callback_query(F.data.in_({"author:profile"}))
async def author_stubs(call: CallbackQuery) -> None:
    text_by_action = {
        "author:profile": "👤 Профиль автора",
    }
    title = text_by_action.get(call.data, "Кабинет автора")
    body = "Раздел подготовлен. Полную логику добавим на следующих этапах."
    await call.message.edit_text(f"<b>{title}</b>\n\n{body}", reply_markup=back_to_main())
    await call.answer()
