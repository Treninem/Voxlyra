import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db import (
    add_audit,
    create_ad_campaign,
    create_author_profile,
    update_author_profile,
    update_book_description,
    update_book_price,
    update_book_title,
    update_book_age_limit,
    update_book_writing_status,
    update_book_download,
    soft_delete_book,
    update_chapter_title,
    update_chapter_text,
    update_chapter_price,
    update_chapter_price_range,
    update_chapter_access_range,
    create_book,
    create_promo_code,
    add_manual_chapter,
    add_audio_chapter,
    book_belongs_to_author,
    count_chapters_for_book,
    count_audio_chapters_for_book,
    get_author_profile,
    get_author_dashboard_stats,
    get_author_finance_summary,
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
    get_author_promo_code,
    set_author_promo_status,
    submit_book_for_review,
    set_book_options,
    set_chapter_status,
    set_audio_chapter_status,
    upsert_imported_chapters,
    update_book_import_fingerprint,
    set_book_duplicate_override,
    upsert_user,
)
from app.keyboards import (
    age_menu,
    author_book_card_menu,
    author_books_menu,
    book_delete_confirm_menu,
    author_menu,
    author_profile_menu,
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
    chapter_delete_confirm_menu,
    cover_menu,
    pricing_menu,
    promo_codes_menu,
    promo_code_card_menu,
    writing_status_menu,
    yes_no_menu,
    single_select_menu,
    multi_select_menu,
    skip_back_menu,
    skip_use_menu,
    navigation_menu,
)
from app.services.book_parser import BookParseError, build_import_report, parse_book_file, split_plain_text_to_chapters
from app.services.duplicate_books import duplicate_warning_text, find_book_duplicates, sha256_file
from app.services.import_store import delete_import_preview, load_import_preview, save_import_preview
from app.services.notifications import discount_message, new_audio_message, new_chapter_message, notify_book_followers
from app.services.cover_storage import download_book_cover
from app.services.publication import finish_book_content_workflow, publish_book_and_channel
from app.services.audio_tools import AudioImportError, build_audio_import_report, extract_audio_zip, format_duration, inspect_audio_file
from app.services.pricing import recommend_book_price
from app.handlers.legal import send_next_required_document
from app.catalog_options import BOOK_TYPES, LANGUAGES, GENRES, TROPES, AUDIENCES, CONTENT_WARNINGS, AD_PLACEMENTS, PROMO_DISCOUNTS, label_for, labels_for

logger = logging.getLogger(__name__)

router = Router()


def _book_text_pricing_mode(book) -> str:
    if not book or int(book["price_stars"] or 0) <= 0:
        return "free"
    return "chapters" if str(book["pricing_type"] or "") == "chapters" else "whole_book"


def _chapter_access_label(chapter, mode: str) -> str:
    if mode == "free" or int(chapter["is_free"] or 0) == 1:
        return "Бесплатно"
    if mode == "chapters" and int(chapter["price_stars"] or 0) > 0:
        return f"{int(chapter['price_stars'])} Stars — покупка главы"
    return "После покупки всей книги"


def _chapter_access_keyboard(*, prefix: str, book_id: int, allow_chapter_price: bool, cancel_callback: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="🆓 Бесплатная глава", callback_data=f"{prefix}:free")
    builder.button(text="📘 После покупки всей книги", callback_data=f"{prefix}:book")
    if allow_chapter_price:
        builder.button(text="⭐ Поставить рекомендованную цену", callback_data=f"{prefix}:recommended")
    builder.button(text="❌ Отмена", callback_data=cancel_callback)
    builder.adjust(1)
    return builder.as_markup()


def _large_book_upload_markup(book_id: int):
    builder = InlineKeyboardBuilder()
    web_url = settings.WEBAPP_URL.strip().rstrip("/")
    if web_url:
        builder.button(
            text="📤 Загрузить крупный файл",
            web_app=WebAppInfo(url=f"{web_url}/author?book_id={int(book_id)}&upload=1"),
        )
        builder.button(text="🔄 Проверить результат", callback_data=f"chapter:upload_status:{int(book_id)}")
    builder.button(text="⬅️ К книге", callback_data=f"author:book:{int(book_id)}")
    builder.adjust(1)
    return builder.as_markup()


class AuthorRegister(StatesGroup):
    pen_name = State()
    bio = State()
    country = State()
    adult = State()


class EditAuthorProfile(StatesGroup):
    pen_name = State()
    bio = State()
    country = State()


class EditBookDetails(StatesGroup):
    title = State()
    description = State()
    price = State()
    chapter_sales = State()
    confirm_free = State()


class EditChapterDetails(StatesGroup):
    title = State()
    text = State()
    price = State()


class AddChapterManual(StatesGroup):
    title = State()
    text = State()
    price = State()


class BulkChapterPrice(StatesGroup):
    target = State()
    price = State()


class ImportChapters(StatesGroup):
    waiting_file = State()
    confirm = State()
    duplicate_confirm = State()


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
    chapter_sales = State()
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
    "subscription": "подписка",
}

PUBLICATION_RU = {
    "draft": "черновик",
    "review": "на проверке",
    "published": "опубликована",
    "hidden": "скрыта",
    "blocked": "заблокирована",
}



async def _notify_new_chapters(book_id: int, chapter_ids: list[int], actor_user_id: int, bot: Bot) -> None:
    if not chapter_ids:
        return
    book = await get_book(book_id)
    if not book or book["publication_status"] != "published":
        return
    if len(chapter_ids) == 1:
        chapter = await get_chapter(chapter_ids[0])
        if not chapter or chapter["status"] != "published":
            return
        text = new_chapter_message(book["title"], chapter["title"], chapter["number"])
        event_key = f"chapter:{chapter_ids[0]}:published"
    else:
        text = new_chapter_message(book["title"], count=len(chapter_ids))
        event_key = f"chapter-batch:{book_id}:{max(chapter_ids)}:{len(chapter_ids)}"
    result = await notify_book_followers(
        book_id=book_id, event_key=event_key, category="chapters", text=text, bot=bot
    )
    await add_audit(actor_user_id, "chapter_followers_notified", "book", str(book_id), None, str(result))


async def _notify_new_audio(book_id: int, audio_ids: list[int], actor_user_id: int, bot: Bot) -> None:
    if not audio_ids:
        return
    book = await get_book(book_id)
    if not book or book["publication_status"] != "published":
        return
    if len(audio_ids) == 1:
        audio = await get_audio_chapter(audio_ids[0])
        if not audio or audio["status"] != "published":
            return
        text = new_audio_message(book["title"], audio["title"], audio["number"])
        event_key = f"audio:{audio_ids[0]}:published"
    else:
        text = new_audio_message(book["title"], count=len(audio_ids))
        event_key = f"audio-batch:{book_id}:{max(audio_ids)}:{len(audio_ids)}"
    result = await notify_book_followers(
        book_id=book_id, event_key=event_key, category="audio", text=text, bot=bot
    )
    await add_audit(actor_user_id, "audio_followers_notified", "book", str(book_id), None, str(result))

@router.callback_query(F.data == "author:menu")
async def author_menu_handler(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if profile:
        stats = await get_author_dashboard_stats(user["id"])
        finance = await get_author_finance_summary(user["id"])
        status_label = {
            "draft": "новый автор",
            "active": "активен",
            "verified": "проверен",
            "blocked": "ограничен",
        }.get(str(profile["status"]), "активен")
        text = (
            "<b>✍️ Кабинет автора</b>\n\n"
            f"<b>{profile['pen_name']}</b> · {status_label}\n\n"
            f"📚 Книг: <b>{stats['books_total']}</b>\n"
            f"✅ Опубликовано: <b>{stats['books_published']}</b>\n"
            f"🕊 На проверке: <b>{stats['books_review']}</b>\n"
            f"📝 Глав: <b>{stats['chapters']}</b> · 🎧 Аудиоглав: <b>{stats['audio']}</b>\n\n"
            f"💫 Доступно к выводу: <b>{finance['available']} Stars</b>\n"
            f"⏳ В удержании: <b>{finance['held']} Stars</b>\n\n"
            "Создавайте книги, добавляйте главы и следите за доходом в одном месте."
        )
    else:
        text = (
            "<b>✍️ Стать автором</b>\n\n"
            "Создайте профиль один раз — псевдоним и основные данные сохранятся. "
            "После этого можно публиковать книги, аудиоверсии и получать доход от читателей."
        )
    await call.message.edit_text(text, reply_markup=author_menu(bool(profile)))
    await call.answer()


@router.callback_query(F.data == "author:cancel_flow")
async def author_cancel_flow(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data.get("book_id") or 0)
    await state.clear()
    if book_id:
        book = await get_book(book_id)
        if book:
            await call.message.edit_text(
                "Действие отменено. Данные не изменены.",
                reply_markup=author_book_card_menu(book_id, book["publication_status"]),
            )
            await call.answer("Отменено")
            return
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    await call.message.edit_text(
        "Действие отменено. Вы вернулись в кабинет автора.",
        reply_markup=author_menu(bool(profile)),
    )
    await call.answer("Отменено")


@router.callback_query(F.data == "author:register")
async def author_register_start(call: CallbackQuery, state: FSMContext) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if profile:
        await call.message.edit_text("Вы уже зарегистрированы как автор.", reply_markup=author_menu(True))
        await call.answer()
        return
    if await send_next_required_document(call.message, int(user["id"]), author=True):
        await call.answer("Сначала документы автора")
        return
    await state.set_state(AuthorRegister.pen_name)
    await call.message.edit_text("Введите ваш основной псевдоним автора. Это обязательное поле.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
    await call.answer()


@router.message(AuthorRegister.pen_name)
async def author_pen_name(message: Message, state: FSMContext) -> None:
    pen_name = message.text.strip() if message.text else ""
    if len(pen_name) < 2:
        await message.answer("Псевдоним слишком короткий. Введите от 2 символов.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    await state.update_data(pen_name=pen_name[:80])
    await state.set_state(AuthorRegister.bio)
    await message.answer("Введите короткое описание автора или нажмите «Пропустить». Его можно добавить позже.", reply_markup=skip_back_menu("author:skip:bio", cancel_callback="author:cancel_flow"))


@router.message(AuthorRegister.bio)
async def author_bio(message: Message, state: FSMContext) -> None:
    bio = message.text.strip() if message.text else ""
    await state.update_data(bio=bio[:1000])
    await state.set_state(AuthorRegister.country)
    await message.answer("Укажите страну или нажмите «Пропустить». Это можно заполнить позже в профиле автора.", reply_markup=skip_back_menu("author:skip:country", cancel_callback="author:cancel_flow"))


@router.callback_query(AuthorRegister.bio, F.data == "author:skip:bio")
async def author_bio_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(bio="")
    await state.set_state(AuthorRegister.country)
    await call.message.edit_text("Описание пропущено. Укажите страну или нажмите «Пропустить».", reply_markup=skip_back_menu("author:skip:country", cancel_callback="author:cancel_flow"))
    await call.answer("Пропущено")


@router.message(AuthorRegister.country)
async def author_country(message: Message, state: FSMContext) -> None:
    country = message.text.strip() if message.text else ""
    await state.update_data(country=country[:80])
    await state.set_state(AuthorRegister.adult)
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, мне есть 18", callback_data="author:adult:yes")
    kb.button(text="Нет", callback_data="author:adult:no")
    kb.button(text="❌ Отмена", callback_data="author:cancel_flow")
    kb.adjust(1)
    await message.answer("Подтвердите возраст автора.", reply_markup=kb.as_markup())


@router.callback_query(AuthorRegister.country, F.data == "author:skip:country")
async def author_country_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(country="")
    await state.set_state(AuthorRegister.adult)
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, мне есть 18", callback_data="author:adult:yes")
    kb.button(text="Нет", callback_data="author:adult:no")
    kb.button(text="❌ Отмена", callback_data="author:cancel_flow")
    kb.adjust(1)
    await call.message.edit_text("Страна пропущена. Подтвердите возраст автора.", reply_markup=kb.as_markup())
    await call.answer("Пропущено")


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
    if await send_next_required_document(call.message, int(user["id"]), author=True):
        await call.answer("Нужно обновить документы автора")
        return
    await state.set_state(AddBook.title)
    await call.message.edit_text("Введите название книги.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
    await call.answer()


@router.message(AddBook.title)
async def add_book_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое. Введите нормальное название книги.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    profile = await get_author_profile(user["id"])
    matches = await find_book_duplicates(
        title=title[:160],
        author_id=int(profile["id"]) if profile else None,
    )
    if matches:
        await state.update_data(pending_title=title[:160], duplicate_title_ack=False)
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Это другая книга", callback_data="book:duplicate_title:continue")
        kb.button(text="✏️ Изменить название", callback_data="book:duplicate_title:change")
        kb.button(text="❌ Отмена", callback_data="author:cancel_flow")
        kb.adjust(1)
        await message.answer(duplicate_warning_text(matches), reply_markup=kb.as_markup())
        return
    await state.update_data(title=title[:160], duplicate_title_ack=False)
    await state.set_state(AddBook.description)
    await message.answer("Введите описание книги или нажмите «Пропустить». Описание можно добавить/изменить позже в карточке книги.", reply_markup=skip_back_menu("book:skip:description", cancel_callback="author:cancel_flow"))


@router.callback_query(AddBook.title, F.data == "book:duplicate_title:continue")
async def add_book_duplicate_title_continue(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    title = str(data.get("pending_title") or "").strip()
    if not title:
        await call.answer("Введите название заново", show_alert=True)
        return
    await state.update_data(title=title, duplicate_title_ack=True)
    await state.set_state(AddBook.description)
    await call.message.edit_text(
        "Совпадение отмечено. Введите описание книги или нажмите «Пропустить».",
        reply_markup=skip_back_menu("book:skip:description"),
    )
    await call.answer("Продолжаем")


@router.callback_query(AddBook.title, F.data == "book:duplicate_title:change")
async def add_book_duplicate_title_change(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(pending_title="", duplicate_title_ack=False)
    await call.message.edit_text("Введите другое название книги.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
    await call.answer()


@router.message(AddBook.description)
async def add_book_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    await state.update_data(description=description[:4000])
    await state.set_state(AddBook.book_type)
    await message.answer(
        "Выберите тип книги.",
        reply_markup=single_select_menu("type", BOOK_TYPES, cancel_callback="author:cancel_flow"),
    )


@router.callback_query(AddBook.description, F.data == "book:skip:description")
async def add_book_description_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description="")
    await state.set_state(AddBook.book_type)
    await call.message.edit_text(
        "Описание пропущено. Выберите тип книги.",
        reply_markup=single_select_menu("type", BOOK_TYPES, cancel_callback="author:cancel_flow"),
    )
    await call.answer("Пропущено")


@router.callback_query(AddBook.book_type, F.data.startswith("single:type:"))
async def add_book_type(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[-1]
    await state.update_data(book_type=[code])
    await state.set_state(AddBook.language)
    await call.message.edit_text("Выберите язык книги.", reply_markup=single_select_menu("lang", LANGUAGES, cancel_callback="author:cancel_flow"))
    await call.answer()


@router.callback_query(AddBook.language, F.data.startswith("single:lang:"))
async def add_book_language(call: CallbackQuery, state: FSMContext) -> None:
    code = call.data.split(":")[-1]
    await state.update_data(language=[code], selected_g=[], selected_t=[], selected_a=[], selected_c=[])
    await state.set_state(AddBook.genres)
    await call.message.edit_text("Выберите жанры. Можно отметить несколько вариантов.", reply_markup=multi_select_menu("g", GENRES, set(), page=0, cancel_callback="author:cancel_flow"))
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
        await call.message.edit_reply_markup(reply_markup=multi_select_menu(prefix, choices, selected, page=page, cancel_callback="author:cancel_flow"))
        await call.answer("Выбор обновлён")
        return
    if action == "p" and len(parts) >= 4:
        page = int(parts[3])
        await state.update_data(**{page_key: page})
        await call.message.edit_reply_markup(reply_markup=multi_select_menu(prefix, choices, selected, page=page, cancel_callback="author:cancel_flow"))
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
                              next_markup=multi_select_menu("t", TROPES, set(), page=0, cancel_callback="author:cancel_flow"), min_required=1)


@router.callback_query(AddBook.tropes, F.data.startswith("sel:t:"))
async def add_book_tropes(call: CallbackQuery, state: FSMContext) -> None:
    await _handle_multiselect(call, state, prefix="t", choices=TROPES, state_key="selected_t",
                              next_state=AddBook.audience,
                              next_text="Выберите, кому книга больше подходит. Можно отметить несколько вариантов.",
                              next_markup=multi_select_menu("a", AUDIENCES, set(), page=0, cancel_callback="author:cancel_flow"), min_required=0)


@router.callback_query(AddBook.audience, F.data.startswith("sel:a:"))
async def add_book_audience(call: CallbackQuery, state: FSMContext) -> None:
    await _handle_multiselect(call, state, prefix="a", choices=AUDIENCES, state_key="selected_a",
                              next_state=AddBook.content_warnings,
                              next_text="Выберите предупреждения по содержанию. Если ничего особого нет, просто нажмите «Готово».",
                              next_markup=multi_select_menu("c", CONTENT_WARNINGS, set(), page=0, cancel_callback="author:cancel_flow"), min_required=0)


@router.callback_query(AddBook.content_warnings, F.data.startswith("sel:c:"))
async def add_book_content_warnings(call: CallbackQuery, state: FSMContext) -> None:
    await _handle_multiselect(call, state, prefix="c", choices=CONTENT_WARNINGS, state_key="selected_c",
                              next_state=AddBook.age_limit,
                              next_text="Выберите возрастное ограничение.",
                              next_markup=age_menu("book:age", cancel_callback="author:cancel_flow"), min_required=0)


@router.callback_query(AddBook.age_limit, F.data.startswith("book:age:"))
async def add_book_age(call: CallbackQuery, state: FSMContext) -> None:
    age = call.data.split(":")[-1]
    await state.update_data(age_limit=age)
    await state.set_state(AddBook.writing_status)
    await call.message.edit_text("Выберите статус книги.", reply_markup=writing_status_menu(cancel_callback="author:cancel_flow"))
    await call.answer()


@router.callback_query(AddBook.writing_status, F.data.startswith("book:status:"))
async def add_book_status(call: CallbackQuery, state: FSMContext) -> None:
    status = call.data.split(":")[-1]
    await state.update_data(writing_status=status)
    await state.set_state(AddBook.allow_download)
    await call.message.edit_text(
        "Разрешить скачивание книги после покупки или бесплатного доступа?\n\n"
        "Если запретить, читать можно будет только внутри платформы.",
        reply_markup=yes_no_menu("book:download", cancel_callback="author:cancel_flow"),
    )
    await call.answer()


@router.callback_query(AddBook.allow_download, F.data.startswith("book:download:"))
async def add_book_download(call: CallbackQuery, state: FSMContext) -> None:
    allow = call.data.endswith(":yes")
    await state.update_data(allow_download=allow)
    await state.set_state(AddBook.pricing_type)
    await call.message.edit_text(
        "<b>Условия доступа к книге</b>\n\n"
        "Бесплатная книга полностью открывает все главы. Если книга платная, после указания цены бот спросит, разрешать ли отдельную покупку глав.",
        reply_markup=pricing_menu(cancel_callback="author:cancel_flow"),
    )
    await call.answer()


@router.callback_query(AddBook.pricing_type, F.data.startswith("book:pricing:"))
async def add_book_pricing(call: CallbackQuery, state: FSMContext) -> None:
    choice = call.data.split(":")[-1]
    data = await state.get_data()
    if choice == "free":
        await state.update_data(pricing_type="free", price_stars=0)
        await state.set_state(AddBook.cover)
        await call.message.edit_text(
            "Книга будет полностью бесплатной. Все её главы тоже будут бесплатными, поэтому цены глав спрашиваться не будут.\n\n"
            "Теперь загрузите обложку изображением или нажмите «Пропустить».",
            reply_markup=cover_menu(cancel_callback="author:cancel_flow"),
        )
    else:
        recommended = recommend_book_price(description=data.get("description", ""), pricing_type="whole_book")
        await state.update_data(pricing_type="whole_book", recommended_price=recommended)
        await state.set_state(AddBook.price)
        await call.message.edit_text(
            f"Рекомендуемая цена всей книги: <b>{recommended} Stars</b>.\n\n"
            "Введите цену числом. После этого бот отдельно спросит, разрешать ли покупку отдельных глав.",
            reply_markup=skip_use_menu(
                "book:price:free", "book:price:recommended",
                "✅ Поставить рекомендованную цену", cancel_callback="author:cancel_flow",
            ),
        )
    await call.answer()


async def _ask_book_chapter_sales(message, state: FSMContext) -> None:
    await state.set_state(AddBook.chapter_sales)
    await message.answer(
        "Разрешить читателю покупать отдельные главы?\n\n"
        "• «Нет» — продаётся только вся книга; закрытые главы открываются после её покупки.\n"
        "• «Да» — книга продаётся целиком, а выбранным главам можно назначить отдельную цену.",
        reply_markup=yes_no_menu("book:chapter_sales", cancel_callback="author:cancel_flow"),
    )


@router.callback_query(AddBook.price, F.data == "book:price:recommended")
async def add_book_price_recommended(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(price_stars=max(1, int(data.get("recommended_price", 1))))
    await _ask_book_chapter_sales(call.message, state)
    await call.answer("Цена сохранена")


@router.callback_query(AddBook.price, F.data == "book:price:free")
async def add_book_price_free(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(pricing_type="free", price_stars=0)
    await state.set_state(AddBook.cover)
    await call.message.edit_text(
        "Книга будет полностью бесплатной, включая все главы. Загрузите обложку или нажмите «Пропустить».",
        reply_markup=cover_menu(cancel_callback="author:cancel_flow"),
    )
    await call.answer("Бесплатно")


@router.message(AddBook.price)
async def add_book_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом. Например: 120", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    price = int(raw)
    if price < 0 or price > 100000:
        await message.answer("Цена должна быть от 0 до 100 000 Stars.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    if price <= 0:
        await state.update_data(pricing_type="free", price_stars=0)
        await state.set_state(AddBook.cover)
        await message.answer(
            "Книга будет полностью бесплатной, включая все главы. Загрузите обложку или нажмите «Пропустить».",
            reply_markup=cover_menu(cancel_callback="author:cancel_flow"),
        )
        return
    await state.update_data(price_stars=price)
    await _ask_book_chapter_sales(message, state)


@router.callback_query(AddBook.chapter_sales, F.data.startswith("book:chapter_sales:"))
async def add_book_chapter_sales(call: CallbackQuery, state: FSMContext) -> None:
    enabled = call.data.endswith(":yes")
    await state.update_data(pricing_type="chapters" if enabled else "whole_book")
    await state.set_state(AddBook.cover)
    result_text = (
        "Продажа отдельных глав включена. Их цены можно будет назначить после создания книги."
        if enabled else
        "Будет продаваться только вся книга. Отдельные цены глав не запрашиваются."
    )
    await call.message.edit_text(
        result_text + "\n\nЗагрузите обложку изображением или нажмите «Пропустить».",
        reply_markup=cover_menu(cancel_callback="author:cancel_flow"),
    )
    await call.answer("Сохранено")


@router.message(AddBook.cover, F.photo)
async def add_book_cover_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(cover_file_id=file_id)
    await state.set_state(AddBook.confirm)
    await _show_book_confirm(message, state)


@router.message(AddBook.cover, F.document)
async def add_book_cover_document(message: Message, state: FSMContext) -> None:
    document = message.document
    filename = (document.file_name or "").lower()
    mime = (document.mime_type or "").lower()
    if not (mime.startswith("image/") or filename.endswith((".jpg", ".jpeg", ".png", ".webp"))):
        await message.answer(
            "Для обложки отправьте изображение JPG, PNG или WEBP.",
            reply_markup=cover_menu(cancel_callback="author:cancel_flow"),
        )
        return
    await state.update_data(cover_file_id=document.file_id)
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
        f"Название: <b>{data.get('title', '')}</b>\n"
        f"Тип: <b>{', '.join(labels_for('type', data.get('book_type', [])))}</b>\n"
        f"Язык: <b>{', '.join(labels_for('lang', data.get('language', [])))}</b>\n"
        f"Жанры: <b>{', '.join(labels_for('g', data.get('selected_g', []))[:6])}</b>\n"
        f"Теги: <b>{', '.join(labels_for('t', data.get('selected_t', []))[:6])}</b>\n"
        f"Аудитория: <b>{', '.join(labels_for('a', data.get('selected_a', []))[:4])}</b>\n"
        f"Предупреждения: <b>{', '.join(labels_for('c', data.get('selected_c', []))[:4])}</b>\n"
        f"Возраст: <b>{data.get('age_limit', '16+')}</b>\n"
        f"Статус: <b>{STATUS_RU.get(data.get('writing_status', 'writing'), data.get('writing_status', 'writing'))}</b>\n"
        f"Скачивание: <b>{'разрешено' if data.get('allow_download') else 'запрещено'}</b>\n"
        f"Цена всей книги: <b>{data.get('price_stars', 0)} Stars</b>\n"
        "Цены отдельных глав задаются после создания книги.\n"
        f"Обложка: <b>{'загружена' if data.get('cover_file_id') else 'нет'}</b>\n\n"
        "Сохранить черновик?"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Сохранить", callback_data="book:confirm:yes")
    kb.button(text="❌ Отмена", callback_data="book:confirm:no")
    kb.adjust(1)
    await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(AddBook.confirm, F.data.startswith("book:confirm:"))
async def add_book_confirm(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
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
        description=data.get("description", ""),
        age_limit=data.get("age_limit", "16+"),
        writing_status=data.get("writing_status", "writing"),
        allow_download=bool(data.get("allow_download")),
        pricing_type=data.get("pricing_type", "free"),
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
    if data.get("duplicate_title_ack"):
        await set_book_duplicate_override(book_id, True)
    cover_file_id = data.get("cover_file_id")
    if cover_file_id:
        try:
            await download_book_cover(bot, book_id, str(cover_file_id))
        except Exception:
            logger.exception("Could not save cover for newly created book_id=%s", book_id)
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
        f"Цена всей книги: <b>{book['price_stars']} Stars</b>\n"
        f"Цены отдельных глав задаются в разделе «Главы».\n\n"
        f"{book['description'] or ''}"
    )
    await call.message.edit_text(text[:4096], reply_markup=author_book_card_menu(book_id, book["publication_status"]))
    await call.answer()


@router.callback_query(F.data.startswith("book:edit_description:"))
async def book_edit_description_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(EditBookDetails.description)
    await call.message.edit_text("Введите новое описание книги или нажмите «Очистить». Описание можно менять в любое время до публикации и после неё.", reply_markup=skip_back_menu("book:edit_desc_clear", f"author:book:{book_id}", "Очистить"))
    await call.answer()


@router.message(EditBookDetails.description)
async def book_edit_description_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    ok = await update_book_description(book_id, user["id"], (message.text or "").strip()[:4000])
    await state.clear()
    await message.answer("Описание книги обновлено." if ok else "Книга не найдена или недоступна.", reply_markup=author_menu(True))


@router.callback_query(EditBookDetails.description, F.data == "book:edit_desc_clear")
async def book_edit_description_clear(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    ok = await update_book_description(book_id, user["id"], "")
    await state.clear()
    await call.message.edit_text("Описание очищено." if ok else "Книга не найдена или недоступна.", reply_markup=author_book_card_menu(book_id, "draft"))
    await call.answer("Готово")


@router.callback_query(F.data.startswith("book:edit_price:"))
async def book_edit_price_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(EditBookDetails.price)
    await call.message.edit_text(
        "<b>Цена всей книги</b>\n\n"
        "Введите цену всей книги в Stars. Значение 0 сделает книгу и все главы полностью бесплатными. "
        "При положительной цене бот отдельно спросит, разрешать ли покупку отдельных глав.",
        reply_markup=skip_back_menu("book:edit_price_free", f"author:book:{book_id}", "Сделать всю книгу бесплатной"),
    )
    await call.answer()


@router.message(EditBookDetails.price)
async def book_edit_price_save(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом. Например: 120", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    price = int(raw)
    if price < 0 or price > 100000:
        await message.answer("Цена должна быть от 0 до 100 000 Stars.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    if price <= 0:
        await state.set_state(EditBookDetails.confirm_free)
        await message.answer(
            "Сделать книгу полностью бесплатной?\n\n"
            "Все существующие и будущие главы станут бесплатными. Продажа отдельных глав и текстовых пакетов будет отключена.",
            reply_markup=yes_no_menu("book:confirm_free", cancel_callback="author:cancel_flow"),
        )
        return
    await state.update_data(price_stars=price)
    await state.set_state(EditBookDetails.chapter_sales)
    await message.answer(
        "Разрешить покупку отдельных глав?\n\n"
        "«Нет» — только покупка всей книги.\n"
        "«Да» — вся книга плюс выбранные главы по отдельности.",
        reply_markup=yes_no_menu("book:edit_chapter_sales", cancel_callback="author:cancel_flow"),
    )


@router.callback_query(EditBookDetails.chapter_sales, F.data.startswith("book:edit_chapter_sales:"))
async def book_edit_chapter_sales(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    price = int(data["price_stars"])
    mode = "chapters" if call.data.endswith(":yes") else "whole_book"
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    ok = await update_book_price(book_id, user["id"], mode, price)
    await state.clear()
    text = (
        f"Цена всей книги: <b>{price} Stars</b>. "
        + ("Продажа отдельных глав включена." if mode == "chapters" else "Продаётся только вся книга.")
    ) if ok else "Книга не найдена или недоступна."
    await call.message.edit_text(text, reply_markup=author_book_card_menu(book_id, "draft"))
    await call.answer("Сохранено" if ok else "Недоступно")


@router.callback_query(EditBookDetails.price, F.data == "book:edit_price_free")
async def book_edit_price_free_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditBookDetails.confirm_free)
    await call.message.edit_text(
        "Сделать книгу полностью бесплатной?\n\n"
        "Все главы станут бесплатными. Продажа отдельных глав и текстовых пакетов отключится. "
        "Старые цены сохранятся только как скрытый черновик и сами не вернутся.",
        reply_markup=yes_no_menu("book:confirm_free", cancel_callback="author:cancel_flow"),
    )
    await call.answer()


@router.callback_query(EditBookDetails.confirm_free, F.data == "book:confirm_free:no")
async def book_edit_price_free_cancel(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    await state.clear()
    await call.message.edit_text("Цена не изменена.", reply_markup=author_book_card_menu(book_id, "draft"))
    await call.answer("Отменено")


@router.callback_query(EditBookDetails.confirm_free, F.data == "book:confirm_free:yes")
async def book_edit_price_free_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    ok = await update_book_price(book_id, user["id"], "free", 0)
    await state.clear()
    await call.message.edit_text(
        "Книга и все её главы теперь бесплатны. Продажа отдельных глав отключена." if ok else "Книга не найдена или недоступна.",
        reply_markup=author_book_card_menu(book_id, "draft"),
    )
    await call.answer("Готово" if ok else "Недоступно")


@router.callback_query(F.data.startswith("author:submit_book:"))
async def author_submit_book(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    if not await book_belongs_to_author(book_id, int(user["id"])):
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    if await count_chapters_for_book(book_id) < 1:
        await call.answer("Перед отправкой добавьте хотя бы одну главу", show_alert=True)
        return
    workflow = await finish_book_content_workflow(
        bot=call.bot,
        book_id=book_id,
        actor_user_id=int(user["id"]),
        actor_telegram_id=int(call.from_user.id),
        source="telegram_submit",
    )
    await add_audit(user["id"], "book_submitted", "book", str(book_id), None, workflow.workflow_status)
    if workflow.workflow_status == "published":
        text = "<b>Книга опубликована.</b> Она появилась в каталоге.\n\n" + workflow.channel_message
    else:
        text = (
            "<b>Книга передана на проверку.</b>\n\n"
            "Владелец, администраторы и модераторы книг получили уведомление. "
            "Если решение задержится, бот напомнит им повторно."
        )
    await call.message.edit_text(text, reply_markup=author_menu(True))
    await call.answer("Готово")



async def _author_can_edit_book(call_or_message, book_id: int) -> tuple[bool, int]:
    tg = call_or_message.from_user
    user = await upsert_user(tg.id, tg.username, tg.full_name)
    ok = await book_belongs_to_author(book_id, user["id"])
    return ok, user["id"]

@router.callback_query(F.data.startswith("book:edit_title:"))
async def book_edit_title_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    await state.update_data(book_id=book_id)
    await state.set_state(EditBookDetails.title)
    await call.message.edit_text(
        f"<b>Название книги</b>\n\nСейчас: <b>{book['title'] if book else ''}</b>\n\nВведите новое название.",
        reply_markup=skip_back_menu(f"book:edit_cancel:{book_id}", None, "Оставить как есть"),
    )
    await call.answer()


@router.message(EditBookDetails.title)
async def book_edit_title_save(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое. Введите от 2 символов.")
        return
    data = await state.get_data()
    book_id = int(data["book_id"])
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    ok = await update_book_title(book_id, user["id"], title)
    await add_audit(user["id"], "book_title_updated", "book", str(book_id), None, title)
    await state.clear()
    await message.answer("Название обновлено." if ok else "Книга не найдена или недоступна.", reply_markup=author_book_card_menu(book_id, (await get_book(book_id))["publication_status"] if await get_book(book_id) else "draft"))




@router.callback_query(F.data.startswith("book:edit_cancel:"))
async def book_edit_cancel(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    await state.clear()
    book = await get_book(book_id)
    await call.message.edit_text("Изменения не внесены.", reply_markup=author_book_card_menu(book_id, book["publication_status"] if book else "draft"))
    await call.answer()


@router.callback_query(F.data.startswith("book:edit_age:"))
async def book_edit_age_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await call.message.edit_text("Выберите новое возрастное ограничение.", reply_markup=age_menu("book:edit_age_set", cancel_callback=f"book:edit_cancel:{book_id}"))
    await call.answer()


@router.callback_query(F.data.startswith("book:edit_age_set:"))
async def book_edit_age_save(call: CallbackQuery, state: FSMContext) -> None:
    age = call.data.split(":")[-1]
    data = await state.get_data()
    book_id = int(data.get("book_id", 0))
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    ok = await update_book_age_limit(book_id, user["id"], age)
    await add_audit(user["id"], "book_age_updated", "book", str(book_id), None, age)
    await state.clear()
    book = await get_book(book_id)
    await call.message.edit_text("Возрастное ограничение обновлено." if ok else "Книга не найдена или недоступна.", reply_markup=author_book_card_menu(book_id, book["publication_status"] if book else "draft"))
    await call.answer("Готово")


@router.callback_query(F.data.startswith("book:edit_status:"))
async def book_edit_status_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await call.message.edit_text("Выберите состояние книги для читателей.", reply_markup=writing_status_menu(cancel_callback=f"book:edit_cancel:{book_id}"))
    await call.answer()


@router.callback_query(F.data.startswith("book:status:"))
async def book_status_dispatch(call: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if current != AddBook.writing_status.state:
        status = call.data.split(":")[-1]
        data = await state.get_data()
        book_id = int(data.get("book_id", 0))
        user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
        ok = await update_book_writing_status(book_id, user["id"], status)
        await add_audit(user["id"], "book_status_updated", "book", str(book_id), None, status)
        await state.clear()
        book = await get_book(book_id)
        await call.message.edit_text("Статус книги обновлён." if ok else "Книга не найдена или недоступна.", reply_markup=author_book_card_menu(book_id, book["publication_status"] if book else "draft"))
        await call.answer("Готово")
        return
    await add_book_status(call, state)


@router.callback_query(F.data.startswith("book:edit_download:"))
async def book_edit_download_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await call.message.edit_text("Разрешить скачивание этой книги?", reply_markup=yes_no_menu("book:edit_download_set", cancel_callback=f"book:edit_cancel:{book_id}"))
    await call.answer()


@router.callback_query(F.data.startswith("book:edit_download_set:"))
async def book_edit_download_save(call: CallbackQuery, state: FSMContext) -> None:
    allow = call.data.endswith(":yes")
    data = await state.get_data()
    book_id = int(data.get("book_id", 0))
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    ok = await update_book_download(book_id, user["id"], allow)
    await add_audit(user["id"], "book_download_updated", "book", str(book_id), None, "yes" if allow else "no")
    await state.clear()
    book = await get_book(book_id)
    await call.message.edit_text("Настройка скачивания обновлена." if ok else "Книга не найдена или недоступна.", reply_markup=author_book_card_menu(book_id, book["publication_status"] if book else "draft"))
    await call.answer("Готово")


@router.callback_query(F.data.startswith("book:delete_ask:"))
async def book_delete_ask(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    await call.message.edit_text(
        f"<b>Удалить книгу?</b>\n\n{book['title'] if book else ''}\n\nКнига исчезнет из кабинета автора и каталога. История покупок и финансовые записи сохранятся.",
        reply_markup=book_delete_confirm_menu(book_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("book:delete_confirm:"))
async def book_delete_confirm(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    ok = await soft_delete_book(book_id, user["id"])
    await add_audit(user["id"], "book_deleted_by_author", "book", str(book_id))
    await call.message.edit_text("Книга удалена." if ok else "Книга не найдена или недоступна.", reply_markup=author_menu(True))
    await call.answer("Удалено" if ok else "Недоступно")


@router.callback_query(F.data.startswith("author:chapters:"))
async def author_chapters_menu_handler(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    mode = _book_text_pricing_mode(book)
    chapters_count = await count_chapters_for_book(book_id)
    pricing_note = {
        "free": "Книга бесплатная: все главы открыты, настройки цен скрыты.",
        "whole_book": "Книга продаётся только целиком: главы могут быть бесплатными ознакомительными или открываться после покупки книги.",
        "chapters": "Книга продаётся целиком и по главам: для одной главы или диапазона можно выбрать бесплатный доступ, доступ после покупки книги либо отдельную цену.",
    }[mode]
    await call.message.edit_text(
        f"<b>Главы книги</b>\n\n"
        f"Книга: <b>{book['title'] if book else book_id}</b>\n"
        f"Сейчас глав: <b>{chapters_count}</b>\n\n"
        f"{pricing_note}\n\n"
        "Можно вставить главу текстом или загрузить файл. Бот сам попробует разбить книгу на главы.",
        reply_markup=author_chapters_menu(book_id, mode),
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
    await call.message.edit_text("Введите название главы или нажмите «Пропустить», чтобы бот поставил номер автоматически.", reply_markup=skip_back_menu("chapter:title:auto", cancel_callback="author:cancel_flow"))
    await call.answer()


@router.callback_query(AddChapterManual.title, F.data == "chapter:title:auto")
async def chapter_add_manual_title_auto(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    count = await count_chapters_for_book(book_id)
    await state.update_data(title=f"Глава {count + 1}")
    await state.set_state(AddChapterManual.text)
    await call.message.edit_text("Название поставлено автоматически. Теперь вставьте текст главы одним сообщением.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
    await call.answer("Готово")


@router.message(AddChapterManual.title)
async def chapter_add_manual_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название главы слишком короткое.")
        return
    await state.update_data(title=title[:160])
    await state.set_state(AddChapterManual.text)
    await message.answer("Теперь вставьте текст главы одним сообщением.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))


@router.message(AddChapterManual.text)
async def chapter_add_manual_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 100:
        await message.answer("Текст главы слишком короткий. Вставьте полный текст главы.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    data = await state.get_data()
    book_id = int(data["book_id"])
    book = await get_book(book_id)
    mode = _book_text_pricing_mode(book)
    recommended = 3
    await state.update_data(text=text[:300000], recommended_price=recommended, pricing_mode=mode)

    if mode == "free":
        await _finish_manual_chapter(message, state, "free", 0)
        return

    await state.set_state(AddChapterManual.price)
    if mode == "whole_book":
        await message.answer(
            "Выберите доступ к новой главе. В этой книге отдельная продажа глав отключена.",
            reply_markup=_chapter_access_keyboard(
                prefix="chapter:add_access", book_id=book_id, allow_chapter_price=False,
                cancel_callback="author:cancel_flow",
            ),
        )
        return

    await message.answer(
        f"Выберите доступ к новой главе. Если продавать её отдельно, рекомендуемая цена — <b>{recommended} Stars</b>.\n\n"
        "Цена всей книги при этом не меняется. Можно также ввести другую цену числом.",
        reply_markup=_chapter_access_keyboard(
            prefix="chapter:add_access", book_id=book_id, allow_chapter_price=True,
            cancel_callback="author:cancel_flow",
        ),
    )


async def _finish_manual_chapter(message_or_call, state: FSMContext, access_mode: str, price: int = 0) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    book = await get_book(book_id)
    mode = _book_text_pricing_mode(book)
    access = str(access_mode or "free")
    active_price = max(0, int(price or 0))

    if mode == "free":
        access, active_price = "free", 0
    elif access == "chapter" and mode != "chapters":
        target_message = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call
        await target_message.answer("Отдельная продажа глав для этой книги выключена.", reply_markup=author_chapters_menu(book_id, mode))
        await state.clear()
        return
    elif access == "chapter" and active_price <= 0:
        target_message = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call
        await target_message.answer("Для отдельной продажи укажите цену больше 0 Stars.")
        return
    elif access not in {"free", "book", "chapter"}:
        access, active_price = "free", 0

    chapter_id = await add_manual_chapter(
        book_id,
        data.get("title") or "Глава",
        data["text"],
        is_free=access == "free",
        price_stars=active_price if access == "chapter" else 0,
    )
    await state.clear()
    tg = message_or_call.from_user
    user = await upsert_user(tg.id, tg.username, tg.full_name)
    book = await get_book(book_id)
    if book and book["publication_status"] == "published":
        await set_chapter_status(chapter_id, "published")
    await add_audit(user["id"], "chapter_created_manual", "chapter", str(chapter_id), None, f"access={access}; price={active_price}")
    bot = message_or_call.bot
    await _notify_new_chapters(book_id, [chapter_id], int(user["id"]), bot)
    target_message = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call
    status_text = {
        "free": "Глава сохранена как бесплатная.",
        "book": "Глава сохранена с доступом после покупки всей книги.",
        "chapter": f"Глава сохранена с отдельной ценой {active_price} Stars.",
    }[access]
    if book and book["publication_status"] == "published":
        status_text = status_text.replace("сохранена", "опубликована", 1)
    await target_message.answer(status_text, reply_markup=author_chapters_menu(book_id, _book_text_pricing_mode(book)))


@router.callback_query(AddChapterManual.price, F.data == "chapter:add_access:recommended")
async def chapter_add_manual_price_recommended(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _finish_manual_chapter(call, state, "chapter", int(data.get("recommended_price", 3)))
    await call.answer("Сохранено")


@router.callback_query(AddChapterManual.price, F.data == "chapter:add_access:free")
async def chapter_add_manual_price_free(call: CallbackQuery, state: FSMContext) -> None:
    await _finish_manual_chapter(call, state, "free", 0)
    await call.answer("Бесплатная глава")


@router.callback_query(AddChapterManual.price, F.data == "chapter:add_access:book")
async def chapter_add_manual_access_book(call: CallbackQuery, state: FSMContext) -> None:
    await _finish_manual_chapter(call, state, "book", 0)
    await call.answer("Доступ после покупки книги")


@router.message(AddChapterManual.price)
async def chapter_add_manual_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом, например 3, либо выберите вариант кнопкой.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    price = int(raw)
    if price < 1 or price > 100000:
        await message.answer("Для отдельной продажи цена должна быть от 1 до 100 000 Stars.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    await _finish_manual_chapter(message, state, "chapter", price)


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
        "Крупные файлы удобнее загружать через кабинет автора — там загрузка идёт частями.\n\n"
        "После сохранения вернитесь сюда и нажмите «Проверить результат». Владелец публикует книгу сразу, обычный автор отправляет её на проверку.",
        reply_markup=_large_book_upload_markup(book_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("chapter:upload_status:"))
async def chapter_upload_status(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, user_id = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    chapters = await count_chapters_for_book(book_id)
    if chapters < 1:
        text = (
            "Файл ещё не сохранён в книгу. В Mini App выберите файл, нажмите «Загрузить и проверить», "
            "а затем «Сохранить главы»."
        )
        await call.message.edit_text(text, reply_markup=_large_book_upload_markup(book_id))
        await call.answer("Файл ещё не сохранён")
        return

    # Восстанавливает цепочку, даже если Mini App был закрыт сразу после импорта.
    workflow = await finish_book_content_workflow(
        bot=call.bot,
        book_id=book_id,
        actor_user_id=int(user_id),
        actor_telegram_id=int(call.from_user.id),
        source="telegram_large_upload_status",
    )
    book = await get_book(book_id)
    status = PUBLICATION_RU.get(book["publication_status"], book["publication_status"]) if book else "неизвестно"
    text = f"В книге сохранено глав: <b>{chapters}</b>. Статус: <b>{status}</b>."
    if workflow.workflow_status == "published" or (book and book["publication_status"] == "published"):
        text += "\n\n<b>Книга опубликована и доступна в каталоге.</b>\n" + workflow.channel_message
    elif workflow.workflow_status == "review" or (book and book["publication_status"] == "review"):
        text += "\n\nКнига отправлена на проверку. После одобрения она появится в каталоге и канале."
    elif workflow.workflow_status == "duplicate":
        text += "\n\nПубликация остановлена: найдена возможная копия книги. Откройте карточку книги и подтвердите, что это другая книга."
    else:
        text += "\n\nГлавы сохранены. Откройте карточку книги, чтобы продолжить."
    await state.clear()
    await call.message.edit_text(
        text,
        reply_markup=author_book_card_menu(book_id, book["publication_status"] if book else "draft"),
    )
    await call.answer("Проверено")


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
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await message.answer(
            "Крупную книгу загрузите через кабинет автора. Файл будет передан частями и не упрётся в ограничение загрузки через чат.",
            reply_markup=_large_book_upload_markup(book_id),
        )
        return

    upload_dir = Path("storage/books") / str(book_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_path = upload_dir / f"upload_{message.message_id}{ext}"
    try:
        with safe_path.open("wb") as destination:
            await bot.download(doc.file_id, destination=destination)
    except TelegramBadRequest:
        safe_path.unlink(missing_ok=True)
        await message.answer(
            "Telegram не передал файл боту. Откройте кабинет автора и загрузите книгу там — крупные файлы поддерживаются частями.",
            reply_markup=_large_book_upload_markup(book_id),
        )
        return

    try:
        chapters = parse_book_file(safe_path, original_name, temp_dir=Path("storage/temp") / f"zip_{book_id}_{message.message_id}")
    except BookParseError as exc:
        await message.answer(f"Не удалось разобрать файл.\n\nПричина: {exc}")
        return
    except Exception:
        logger.exception("Unexpected book import error")
        await message.answer("Не удалось обработать файл. Проверьте его целостность или попробуйте другой формат.")
        return

    source_hash = sha256_file(safe_path)
    book = await get_book(book_id)
    duplicate_matches = await find_book_duplicates(
        title=book["title"] if book else original_name,
        author_id=int(book["author_id"]) if book and book["author_id"] is not None else None,
        exclude_book_id=book_id,
        source_file_hash=source_hash,
    )
    report = build_import_report(chapters)
    preview_path = save_import_preview(chapters)
    await state.update_data(
        preview_path=preview_path,
        original_name=original_name,
        source_file_hash=source_hash,
        duplicate_matches=[item.to_dict() for item in duplicate_matches],
        duplicate_ack=not bool(duplicate_matches),
    )
    await state.set_state(ImportChapters.confirm)

    preview_lines = []
    for item in report["preview"]:
        preview_lines.append(f"{item['number']}. {item['title']} · {item['chars']} зн.")
    problems = "\n".join(f"⚠️ {p}" for p in report["problems"]) or "Явных проблем не найдено."

    book = await get_book(book_id)
    pricing_mode = _book_text_pricing_mode(book)
    first_free = 999999 if pricing_mode == "free" else 3
    default_price = 0
    await state.update_data(first_free=first_free, default_price=default_price, pricing_mode=pricing_mode)
    if pricing_mode == "free":
        pricing_preview = "Все импортированные главы будут бесплатными. Цена глав не требуется."
    elif pricing_mode == "chapters":
        pricing_preview = (
            "Первые 3 главы будут бесплатными ознакомительными, остальные — доступны после покупки всей книги. "
            "Отдельные цены можно назначить затем одной главе или диапазону."
        )
    else:
        pricing_preview = (
            "Первые 3 главы будут бесплатными ознакомительными, остальные — доступны после покупки всей книги. "
            "Отдельная продажа глав выключена."
        )

    await add_audit(user_id, "book_file_parsed", "book", str(book_id), None, original_name)
    await message.answer(
        "<b>Предпросмотр импорта</b>\n\n"
        f"Файл: <b>{original_name}</b>\n"
        f"Найдено глав: <b>{report['chapters_count']}</b>\n"
        f"Всего знаков: <b>{report['total_chars']}</b>\n"
        f"{pricing_preview}\n\n"
        "<b>Первые главы:</b>\n"
        f"{chr(10).join(preview_lines) if preview_lines else 'нет'}\n\n"
        "<b>Проверка:</b>\n"
        f"{problems}\n\n"
        "Сохранить эти главы в книгу? Главы с такими же номерами будут обновлены."
        + ("\n\n" + duplicate_warning_text(duplicate_matches) if duplicate_matches else ""),
        reply_markup=chapter_import_confirm_menu(book_id, duplicate_warning=bool(duplicate_matches)),
    )


@router.message(ImportChapters.waiting_file)
async def chapter_upload_not_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data.get("book_id") or 0)
    await message.answer(
        "Нужно отправить файл документом: TXT, DOCX, FB2, EPUB, PDF или ZIP.",
        reply_markup=_large_book_upload_markup(book_id) if book_id else navigation_menu(cancel_callback="author:cancel_flow"),
    )


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
    if data.get("duplicate_matches") and not data.get("duplicate_ack"):
        await state.set_state(ImportChapters.duplicate_confirm)
        matches_text = "\n".join(
            f"• «{item.get('title', 'Книга')}» — {item.get('reason', 'похожа')}"
            for item in data.get("duplicate_matches", [])[:6]
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Всё равно сохранить", callback_data=f"chapter:duplicate_continue:{book_id}")
        kb.button(text="❌ Отменить импорт", callback_data=f"chapter:import_cancel:{book_id}")
        kb.adjust(1)
        await call.message.edit_text(
            "<b>Подтвердите возможную копию</b>\n\n" + matches_text +
            "\n\nПродолжайте только если это новая редакция или действительно другая книга.",
            reply_markup=kb.as_markup(),
        )
        await call.answer("Нужно подтверждение", show_alert=True)
        return
    if not chapters:
        await call.answer("Данные импорта не найдены. Загрузите файл заново.", show_alert=True)
        await state.clear()
        return
    import_result = await upsert_imported_chapters(
        book_id,
        chapters,
        first_free=int(data.get("first_free", 3)),
        default_price_stars=int(data.get("default_price", 0)),
        return_published_ids=True,
    )
    saved = int(import_result["saved"])
    published_ids = [int(item) for item in import_result["published_ids"]]
    await update_book_import_fingerprint(
        book_id,
        filename=str(data.get("original_name") or "book"),
        source_file_hash=str(data.get("source_file_hash") or ""),
        duplicate_override=bool(data.get("duplicate_ack")),
    )
    await add_audit(user_id, "chapters_imported", "book", str(book_id), None, str(saved))
    await _notify_new_chapters(book_id, published_ids, int(user_id), call.bot)

    workflow = await finish_book_content_workflow(
        bot=call.bot,
        book_id=book_id,
        actor_user_id=int(user_id),
        actor_telegram_id=int(call.from_user.id),
        source="telegram_file_import",
    )
    current_book = await get_book(book_id)
    reply_markup = author_menu(True) if workflow.workflow_status in {"published", "review"} else author_chapters_menu(book_id, _book_text_pricing_mode(current_book))
    if workflow.workflow_status == "published":
        publication_note = "<b>Книга опубликована.</b> Она появилась в каталоге."
        if workflow.channel_message:
            publication_note += "\n\n" + workflow.channel_message
    elif workflow.workflow_status == "review":
        publication_note = (
            "<b>Книга передана на проверку.</b>\n\n"
            "Владелец, администраторы и модераторы книг получили уведомление. "
            "Если решение задержится, бот напомнит им повторно."
        )
    else:
        publication_note = "Главы сохранены. Откройте карточку книги, чтобы продолжить."

    delete_import_preview(data.get("preview_path"))
    await state.clear()
    await call.message.edit_text(
        f"Главы сохранены: <b>{saved}</b>.\n\n{publication_note}",
        reply_markup=reply_markup,
    )
    await call.answer("Сохранено")


@router.callback_query(ImportChapters.duplicate_confirm, F.data.startswith("chapter:duplicate_continue:"))
async def chapter_duplicate_continue(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    await state.update_data(duplicate_ack=True)
    await set_book_duplicate_override(book_id, True)
    await state.set_state(ImportChapters.confirm)
    await call.message.edit_text(
        "Совпадение подтверждено. Теперь сохраните главы.",
        reply_markup=chapter_import_confirm_menu(book_id),
    )
    await call.answer("Подтверждено")


@router.callback_query(ImportChapters.confirm, F.data.startswith("chapter:import_cancel:"))
async def chapter_import_cancel(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    delete_import_preview(data.get("preview_path"))
    book_id = int(call.data.split(":")[-1])
    await state.clear()
    book = await get_book(book_id)
    await call.message.edit_text("Импорт отменён. Файл не сохранён в книгу.", reply_markup=author_chapters_menu(book_id, _book_text_pricing_mode(book)))
    await call.answer()


@router.callback_query(F.data.startswith("chapter:bulk_price:"))
async def chapter_bulk_price_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    mode = _book_text_pricing_mode(book)
    if mode == "free":
        await call.answer("Книга бесплатная: все главы уже открыты бесплатно.", show_alert=True)
        return
    await state.update_data(book_id=book_id, pricing_mode=mode)
    await state.set_state(BulkChapterPrice.target)
    title = "Доступ одной главы или диапазона" if mode == "whole_book" else "Доступ и цена одной главы или диапазона"
    await call.message.edit_text(
        f"<b>{title}</b>\n\n"
        "Введите номер одной главы, например <b>7</b>, либо диапазон, например <b>7-25</b>.\n\n"
        "Цена всей книги при этом не изменится.",
        reply_markup=navigation_menu(back_callback=f"author:chapters:{book_id}", cancel_callback="author:cancel_flow"),
    )
    await call.answer()


@router.message(BulkChapterPrice.target)
async def chapter_bulk_price_target(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace("—", "-").replace("–", "-").replace(" ", "")
    parts = raw.split("-", 1)
    if not parts[0].isdigit() or (len(parts) == 2 and not parts[1].isdigit()):
        await message.answer("Введите номер главы, например 7, или диапазон, например 7-25.")
        return
    start_number = int(parts[0])
    end_number = int(parts[1]) if len(parts) == 2 else start_number
    if start_number < 1 or end_number < 1 or abs(end_number - start_number) > 100000:
        await message.answer("Проверьте номера глав. Номера должны начинаться с 1.")
        return
    if start_number > end_number:
        start_number, end_number = end_number, start_number
    data = await state.get_data()
    mode = str(data.get("pricing_mode") or "whole_book")
    await state.update_data(start_number=start_number, end_number=end_number)
    await state.set_state(BulkChapterPrice.price)
    target = f"глав {start_number}–{end_number}" if start_number != end_number else f"главы {start_number}"
    builder = InlineKeyboardBuilder()
    builder.button(text="🆓 Сделать бесплатными", callback_data="chapter:bulk_access:free")
    builder.button(text="📘 После покупки всей книги", callback_data="chapter:bulk_access:book")
    if mode == "chapters":
        builder.button(text="❌ Отмена", callback_data="author:cancel_flow")
        builder.adjust(1)
        text = (
            f"Выберите доступ для <b>{target}</b>.\n\n"
            "Чтобы продавать выбранные главы отдельно, введите цену числом от 1 до 100 000 Stars. "
            "Цена всей книги останется без изменений."
        )
    else:
        builder.button(text="❌ Отмена", callback_data="author:cancel_flow")
        builder.adjust(1)
        text = (
            f"Выберите доступ для <b>{target}</b>.\n\n"
            "Отдельная продажа глав выключена: можно оставить бесплатный ознакомительный доступ либо открыть главы после покупки всей книги."
        )
    await message.answer(text, reply_markup=builder.as_markup())


async def _apply_bulk_chapter_access(message_or_call, state: FSMContext, access_mode: str, price: int = 0) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    start_number = int(data["start_number"])
    end_number = int(data["end_number"])
    tg = message_or_call.from_user
    user = await upsert_user(tg.id, tg.username, tg.full_name)
    result = await update_chapter_access_range(
        book_id, user["id"], start_number, end_number, access_mode, price
    )
    await state.clear()
    updated = int(result.get("updated") or 0)
    reason = str(result.get("reason") or "")
    if not updated:
        text = {
            "book_is_free": "Книга полностью бесплатна. Настройки доступа к главам не нужны.",
            "chapter_sales_disabled": "Отдельная продажа глав для этой книги выключена.",
            "price_required": "Для отдельной продажи укажите цену больше 0 Stars.",
            "chapters_not_found": "В указанном диапазоне главы не найдены.",
            "not_found": "Книга не найдена или недоступна.",
        }.get(reason, "Доступ к главам не изменён.")
    elif access_mode == "free":
        text = f"Готово: <b>{updated}</b> глав открыты бесплатно. Цена всей книги не изменилась."
    elif access_mode == "book":
        text = f"Готово: <b>{updated}</b> глав доступны после покупки всей книги."
    else:
        text = f"Готово: для <b>{updated}</b> глав установлена отдельная цена <b>{price} Stars</b>. Цена всей книги не изменилась."
    await add_audit(
        user["id"], "chapter_access_range_updated", "book", str(book_id), None,
        f"{start_number}-{end_number}:{access_mode}={price}; updated={updated}; reason={reason}",
    )
    book = await get_book(book_id)
    markup = author_chapters_menu(book_id, _book_text_pricing_mode(book))
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.edit_text(text, reply_markup=markup)
        await message_or_call.answer("Готово" if updated else "Не изменено")
    else:
        await message_or_call.answer(text, reply_markup=markup)


@router.message(BulkChapterPrice.price)
async def chapter_bulk_price_save(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом от 1 до 100 000 Stars или выберите вариант кнопкой.")
        return
    price = int(raw)
    if price < 1 or price > 100000:
        await message.answer("Для отдельной продажи цена должна быть от 1 до 100 000 Stars.")
        return
    data = await state.get_data()
    if str(data.get("pricing_mode") or "") != "chapters":
        await message.answer("Отдельная продажа глав для этой книги выключена. Выберите один из вариантов кнопкой.")
        return
    await _apply_bulk_chapter_access(message, state, "chapter", price)


@router.callback_query(BulkChapterPrice.price, F.data == "chapter:bulk_access:free")
async def chapter_bulk_access_free(call: CallbackQuery, state: FSMContext) -> None:
    await _apply_bulk_chapter_access(call, state, "free", 0)


@router.callback_query(BulkChapterPrice.price, F.data == "chapter:bulk_access:book")
async def chapter_bulk_access_book(call: CallbackQuery, state: FSMContext) -> None:
    await _apply_bulk_chapter_access(call, state, "book", 0)


@router.callback_query(F.data.startswith("chapter:list:"))
async def chapter_list_handler(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    book = await get_book(book_id)
    mode = _book_text_pricing_mode(book)
    chapters = await list_chapters_for_book(book_id)
    if not chapters:
        await call.message.edit_text("Глав пока нет.", reply_markup=author_chapters_menu(book_id, mode))
    else:
        await call.message.edit_text(
            f"<b>Список глав</b>\n\nВсего: <b>{len(chapters)}</b>",
            reply_markup=author_chapter_list_menu(book_id, chapters, mode),
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
    mode = _book_text_pricing_mode(chapter)
    await call.message.edit_text(
        f"<b>{chapter['number']}. {chapter['title']}</b>\n\n"
        f"Статус: <b>{chapter['status']}</b>\n"
        f"Доступ: <b>{_chapter_access_label(chapter, mode)}</b>\n"
        f"Знаков: <b>{len(text)}</b>\n\n"
        f"{text[:1600]}{'...' if len(text) > 1600 else ''}",
        reply_markup=chapter_view_menu(int(chapter["book_id"]), chapter_id, mode),
    )
    await call.answer()




@router.callback_query(F.data.startswith("chapter:edit_title:"))
async def chapter_edit_title_start(call: CallbackQuery, state: FSMContext) -> None:
    chapter_id = int(call.data.split(":")[-1])
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    ok, _ = await _author_can_edit_book(call, int(chapter["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await state.update_data(chapter_id=chapter_id, book_id=int(chapter["book_id"]))
    await state.set_state(EditChapterDetails.title)
    await call.message.edit_text(
        f"<b>Название главы</b>\n\nСейчас: <b>{chapter['title']}</b>\n\nВведите новое название.",
        reply_markup=skip_back_menu(f"chapter:edit_cancel:{chapter_id}", None, "Оставить как есть"),
    )
    await call.answer()


@router.message(EditChapterDetails.title)
async def chapter_edit_title_save(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое. Введите от 2 символов.")
        return
    data = await state.get_data()
    chapter_id = int(data["chapter_id"])
    book_id = int(data["book_id"])
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    ok = await update_chapter_title(chapter_id, user["id"], title)
    await add_audit(user["id"], "chapter_title_updated", "chapter", str(chapter_id), None, title)
    await state.clear()
    book = await get_book(book_id)
    await message.answer("Название главы обновлено." if ok else "Глава не найдена или недоступна.", reply_markup=author_chapters_menu(book_id, _book_text_pricing_mode(book)))


@router.callback_query(F.data.startswith("chapter:edit_text:"))
async def chapter_edit_text_start(call: CallbackQuery, state: FSMContext) -> None:
    chapter_id = int(call.data.split(":")[-1])
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    ok, _ = await _author_can_edit_book(call, int(chapter["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await state.update_data(chapter_id=chapter_id, book_id=int(chapter["book_id"]))
    await state.set_state(EditChapterDetails.text)
    await call.message.edit_text(
        "Вставьте новый полный текст главы одним сообщением. Старый текст заменится после сохранения.",
        reply_markup=skip_back_menu(f"chapter:edit_cancel:{chapter_id}", None, "Не менять"),
    )
    await call.answer()


@router.message(EditChapterDetails.text)
async def chapter_edit_text_save(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 100:
        await message.answer("Текст слишком короткий. Вставьте полный текст главы.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    data = await state.get_data()
    chapter_id = int(data["chapter_id"])
    book_id = int(data["book_id"])
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    ok = await update_chapter_text(chapter_id, user["id"], text[:300000])
    await add_audit(user["id"], "chapter_text_updated", "chapter", str(chapter_id), None, f"{len(text)} chars")
    await state.clear()
    book = await get_book(book_id)
    await message.answer("Текст главы обновлён." if ok else "Глава не найдена или недоступна.", reply_markup=author_chapters_menu(book_id, _book_text_pricing_mode(book)))


@router.callback_query(F.data.startswith("chapter:edit_price:"))
async def chapter_edit_price_start(call: CallbackQuery, state: FSMContext) -> None:
    chapter_id = int(call.data.split(":")[-1])
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    ok, _ = await _author_can_edit_book(call, int(chapter["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    mode = _book_text_pricing_mode(chapter)
    if mode == "free":
        await call.answer("Книга полностью бесплатна. Все главы уже открыты.", show_alert=True)
        return
    await state.update_data(chapter_id=chapter_id, book_id=int(chapter["book_id"]), pricing_mode=mode)
    await state.set_state(EditChapterDetails.price)
    builder = InlineKeyboardBuilder()
    builder.button(text="🆓 Бесплатная глава", callback_data="chapter:edit_access:free")
    builder.button(text="📘 После покупки всей книги", callback_data="chapter:edit_access:book")
    builder.button(text="❌ Отмена", callback_data=f"chapter:view:{chapter_id}")
    builder.adjust(1)
    if mode == "chapters":
        prompt = (
            "Выберите доступ для этой главы. Чтобы продавать её отдельно, введите цену числом от 1 до 100 000 Stars.\n\n"
            "Цена всей книги не изменится."
        )
    else:
        prompt = (
            "Выберите доступ для этой главы. Отдельная продажа глав выключена: глава может быть бесплатной "
            "либо открываться после покупки всей книги."
        )
    await call.message.edit_text(prompt, reply_markup=builder.as_markup())
    await call.answer()


async def _apply_single_chapter_access(message_or_call, state: FSMContext, access_mode: str, price: int = 0) -> None:
    data = await state.get_data()
    chapter_id = int(data["chapter_id"])
    book_id = int(data["book_id"])
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await state.clear()
        target = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call
        await target.answer("Глава не найдена.")
        return
    user = await upsert_user(
        message_or_call.from_user.id, message_or_call.from_user.username, message_or_call.from_user.full_name
    )
    result = await update_chapter_access_range(
        book_id, user["id"], int(chapter["number"]), int(chapter["number"]), access_mode, price
    )
    await state.clear()
    ok = bool(result.get("updated"))
    reason = str(result.get("reason") or "")
    if ok and access_mode == "free":
        text = "Глава теперь бесплатная."
    elif ok and access_mode == "book":
        text = "Глава теперь открывается после покупки всей книги."
    elif ok:
        text = f"Для главы установлена отдельная цена {price} Stars."
    else:
        text = {
            "book_is_free": "Книга полностью бесплатна. Все главы уже открыты.",
            "chapter_sales_disabled": "Отдельная продажа глав для этой книги выключена.",
            "price_required": "Укажите цену больше 0 Stars.",
        }.get(reason, "Не удалось изменить доступ к главе.")
    await add_audit(user["id"], "chapter_access_updated", "chapter", str(chapter_id), None, f"{access_mode}={price}; reason={reason}")
    book = await get_book(book_id)
    markup = author_chapters_menu(book_id, _book_text_pricing_mode(book))
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.edit_text(text, reply_markup=markup)
        await message_or_call.answer("Готово" if ok else "Не изменено")
    else:
        await message_or_call.answer(text, reply_markup=markup)


@router.message(EditChapterDetails.price)
async def chapter_edit_price_save(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом от 1 до 100 000 Stars либо выберите вариант кнопкой.")
        return
    price = int(raw)
    if price < 1 or price > 100000:
        await message.answer("Для отдельной продажи цена должна быть от 1 до 100 000 Stars.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    data = await state.get_data()
    if str(data.get("pricing_mode") or "") != "chapters":
        await message.answer("Отдельная продажа глав для этой книги выключена. Выберите вариант кнопкой.")
        return
    await _apply_single_chapter_access(message, state, "chapter", price)


@router.callback_query(EditChapterDetails.price, F.data == "chapter:edit_access:free")
async def chapter_edit_access_free(call: CallbackQuery, state: FSMContext) -> None:
    await _apply_single_chapter_access(call, state, "free", 0)


@router.callback_query(EditChapterDetails.price, F.data == "chapter:edit_access:book")
async def chapter_edit_access_book(call: CallbackQuery, state: FSMContext) -> None:
    await _apply_single_chapter_access(call, state, "book", 0)


@router.callback_query(F.data.startswith("chapter:edit_cancel:"))
async def chapter_edit_cancel(call: CallbackQuery, state: FSMContext) -> None:
    chapter_id = int(call.data.split(":")[-1])
    chapter = await get_chapter(chapter_id)
    book_id = int(chapter["book_id"]) if chapter else 0
    await state.clear()
    await call.message.edit_text("Изменения не внесены.", reply_markup=chapter_view_menu(book_id, chapter_id, _book_text_pricing_mode(chapter)))
    await call.answer()


@router.callback_query(F.data.startswith("chapter:delete_ask:"))
async def chapter_delete_ask(call: CallbackQuery) -> None:
    chapter_id = int(call.data.split(":")[-1])
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    ok, _ = await _author_can_edit_book(call, int(chapter["book_id"]))
    if not ok:
        await call.answer("Недоступно", show_alert=True)
        return
    await call.message.edit_text(
        f"<b>Удалить главу?</b>\n\n{chapter['number']}. {chapter['title']}\n\nГлава исчезнет из книги. История покупок сохранится.",
        reply_markup=chapter_delete_confirm_menu(int(chapter["book_id"]), chapter_id),
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
    await call.message.edit_text("Введите название аудиоглавы или нажмите «Пропустить», чтобы бот поставил номер автоматически.", reply_markup=skip_back_menu("audio:title:auto", cancel_callback="author:cancel_flow"))
    await call.answer()


@router.callback_query(AddAudioChapter.title, F.data == "audio:title:auto")
async def audio_add_title_auto(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    book_id = int(data["book_id"])
    count = await count_audio_chapters_for_book(book_id)
    await state.update_data(title=f"Аудиоглава {count + 1}")
    await state.set_state(AddAudioChapter.narrator)
    await call.message.edit_text("Название поставлено автоматически. Введите имя диктора или нажмите «Пропустить».", reply_markup=skip_back_menu("audio:narrator:skip", cancel_callback="author:cancel_flow"))
    await call.answer("Готово")


@router.message(AddAudioChapter.title)
async def audio_add_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    await state.update_data(title=title[:160])
    await state.set_state(AddAudioChapter.narrator)
    await message.answer("Введите имя диктора или нажмите «Пропустить». Его можно добавить позже.", reply_markup=skip_back_menu("audio:narrator:skip", cancel_callback="author:cancel_flow"))


@router.callback_query(AddAudioChapter.narrator, F.data == "audio:narrator:skip")
async def audio_add_narrator_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(narrator="")
    data = await state.get_data()
    book = await get_book(int(data["book_id"]))
    recommended = 0 if book and book["pricing_type"] == "free" else max(5, int((book["price_stars"] or 3) * 2))
    await state.update_data(recommended_price=recommended)
    await state.set_state(AddAudioChapter.price)
    await call.message.edit_text(
        f"Рекомендуемая цена аудиоглавы: <b>{recommended} Stars</b>.\n\n"
        "Введите цену числом, поставьте рекомендованную или сделайте аудио бесплатным.",
        reply_markup=skip_use_menu("audio:price:free", "audio:price:recommended", "✅ Поставить рекомендованную цену", cancel_callback="author:cancel_flow"),
    )
    await call.answer("Пропущено")


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
        "Введите цену числом, поставьте рекомендованную или сделайте аудио бесплатным.",
        reply_markup=skip_use_menu("audio:price:free", "audio:price:recommended", "✅ Поставить рекомендованную цену", cancel_callback="author:cancel_flow"),
    )


async def _audio_wait_file(target, state: FSMContext, price: int) -> None:
    await state.update_data(price_stars=price)
    await state.set_state(AddAudioChapter.waiting_file)
    text = (
        "Загрузите реальный аудиофайл документом или аудио.\n\n"
        "Поддерживаются MP3, M4A, OGG и WAV. После загрузки аудиоплеер Mini App будет играть именно этот файл."
    )
    markup = navigation_menu(cancel_callback="author:cancel_flow")
    if hasattr(target, "message"):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.callback_query(AddAudioChapter.price, F.data == "audio:price:recommended")
async def audio_add_price_recommended(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _audio_wait_file(call, state, int(data.get("recommended_price", 0)))
    await call.answer("Сохранено")


@router.callback_query(AddAudioChapter.price, F.data == "audio:price:free")
async def audio_add_price_free(call: CallbackQuery, state: FSMContext) -> None:
    await _audio_wait_file(call, state, 0)
    await call.answer("Бесплатно")


@router.message(AddAudioChapter.price)
async def audio_add_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите цену числом. Например: 7", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    price = int(raw)
    if price > 100000:
        await message.answer("Цена выглядит слишком большой.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    await _audio_wait_file(message, state, price)


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
    book = await get_book(book_id)
    if book and book["publication_status"] == "published":
        await set_audio_chapter_status(audio_id, "published")
    await add_audit(user_id, "audio_chapter_created", "audio_chapter", str(audio_id), None, source_name)
    await _notify_new_audio(book_id, [audio_id], int(user_id), bot)
    await state.clear()
    await message.answer(
        "<b>Аудиоглава сохранена.</b>\n\n"
        f"Название: <b>{data.get('title', '')}</b>\n"
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
    await message.answer("Нужно отправить аудиофайл: MP3, M4A, OGG или WAV.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))


@router.callback_query(F.data.startswith("audio:zip:"))
async def audio_zip_start(call: CallbackQuery, state: FSMContext) -> None:
    book_id = int(call.data.split(":")[-1])
    ok, _ = await _author_can_edit_book(call, book_id)
    if not ok:
        await call.answer("Книга не найдена или недоступна", show_alert=True)
        return
    await state.update_data(book_id=book_id)
    await state.set_state(ImportAudioZip.narrator)
    await call.message.edit_text("Введите имя диктора для аудиофайлов из ZIP. Если не нужно, нажмите «Пропустить».", reply_markup=skip_back_menu("audiozip:narrator:skip", cancel_callback="author:cancel_flow"))
    await call.answer()


@router.callback_query(ImportAudioZip.narrator, F.data == "audiozip:narrator:skip")
async def audio_zip_narrator_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(narrator="")
    await state.set_state(ImportAudioZip.price)
    await call.message.edit_text(
        "Введите цену каждой аудиоглавы числом или сделайте аудио бесплатным.",
        reply_markup=skip_use_menu("audiozip:price:free", None, "✅ Бесплатно", cancel_callback="author:cancel_flow"),
    )
    await call.answer("Пропущено")


@router.message(ImportAudioZip.narrator)
async def audio_zip_narrator(message: Message, state: FSMContext) -> None:
    narrator = (message.text or "").strip()
    if narrator.lower() in {"нет", "-", "не указано"}:
        narrator = ""
    await state.update_data(narrator=narrator[:120])
    await state.set_state(ImportAudioZip.price)
    await message.answer("Введите цену каждой аудиоглавы из ZIP. Можно 0, если аудио бесплатное.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))


@router.message(ImportAudioZip.price)
async def audio_zip_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число. Например: 7")
        return
    await state.update_data(price_stars=int(raw))
    await state.set_state(ImportAudioZip.waiting_zip)
    await message.answer("Загрузите ZIP с аудиофайлами MP3/M4A/OGG/WAV. Бот сохранит их как аудиоглавы по порядку.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))


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
    published_audio_ids: list[int] = []
    book = await get_book(book_id)
    publish_now = bool(book and book["publication_status"] == "published")
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
        if publish_now:
            await set_audio_chapter_status(audio_id, "published")
            published_audio_ids.append(audio_id)
        await add_audit(user_id, "audio_chapter_imported_zip", "audio_chapter", str(audio_id), None, info.source_filename)
        saved += 1
    await _notify_new_audio(book_id, published_audio_ids, int(user_id), bot)
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
    await message.answer("Нужно отправить ZIP-архив документом.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))


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
        reply_markup=single_select_menu("adplace", AD_PLACEMENTS, back_callback="author:ads", cancel_callback="author:cancel_flow"),
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
        "Рекомендация для старта: 100–500.",
        reply_markup=navigation_menu(cancel_callback="author:cancel_flow"),
    )
    await call.answer()


@router.message(CreateAdCampaign.budget)
async def ad_create_budget(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число. Например: 200", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    budget = int(raw)
    if budget < 10 or budget > 100000:
        await message.answer("Бюджет должен быть от 10 до 100000 условных показов.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
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
        "Пример: START50 или MYBOOK100.",
        reply_markup=navigation_menu(cancel_callback="author:cancel_flow"),
    )
    await call.answer()


@router.message(CreatePromoCode.code)
async def promo_create_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    if len(code) < 3:
        await message.answer("Промокод слишком короткий.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    await state.update_data(code=code)
    await state.set_state(CreatePromoCode.discount)
    await message.answer("Выберите размер скидки.", reply_markup=single_select_menu("discount", PROMO_DISCOUNTS, cancel_callback="author:cancel_flow"))


@router.callback_query(CreatePromoCode.discount, F.data.startswith("single:discount:"))
async def promo_create_discount(call: CallbackQuery, state: FSMContext) -> None:
    discount = int(call.data.split(":")[-1])
    await state.update_data(discount=discount)
    await state.set_state(CreatePromoCode.max_uses)
    await call.message.edit_text("Введите лимит использований промокода. Например: 100", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
    await call.answer()


@router.message(CreatePromoCode.max_uses)
async def promo_create_max_uses(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число. Например: 100", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    max_uses = int(raw)
    if max_uses < 1 or max_uses > 100000:
        await message.answer("Лимит должен быть от 1 до 100000.", reply_markup=navigation_menu(cancel_callback="author:cancel_flow"))
        return
    data = await state.get_data()
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    try:
        promo_id = await create_promo_code(user["id"], int(data["book_id"]), data["code"], int(data["discount"]), max_uses)
    except Exception:
        logger.exception("Promo code creation failed")
        await message.answer("Не удалось создать промокод. Проверьте код и попробуйте ещё раз.", reply_markup=author_ads_menu())
        await state.clear()
        return
    await add_audit(user["id"], "promo_code_created", "promo_code", str(promo_id), None, data["code"])
    promo = await get_author_promo_code(user["id"], promo_id)
    book = await get_book(int(data["book_id"]))
    if promo and book and book["publication_status"] == "published":
        result = await notify_book_followers(
            book_id=int(data["book_id"]),
            event_key=f"discount:{promo_id}:created",
            category="discounts",
            text=discount_message(book["title"], promo["discount_percent"], promo["code"]),
            bot=message.bot,
        )
        await add_audit(user["id"], "discount_followers_notified", "promo_code", str(promo_id), None, str(result))
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



@router.callback_query(F.data.startswith("promo:card:"))
async def promo_card_handler(call: CallbackQuery) -> None:
    promo_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    promo = await get_author_promo_code(user["id"], promo_id)
    if not promo:
        await call.answer("Промокод не найден", show_alert=True)
        return
    status_label = "активен" if promo["status"] == "active" else "приостановлен"
    left = max(0, int(promo["max_uses"] or 0) - int(promo["used_count"] or 0))
    await call.message.edit_text(
        "<b>🎟 Промокод</b>\n\n"
        f"Код: <code>{promo['code']}</code>\n"
        f"Книга: <b>{promo['book_title']}</b>\n"
        f"Скидка: <b>{promo['discount_percent']}%</b>\n"
        f"Использовано: <b>{promo['used_count']}</b>\n"
        f"Осталось: <b>{left}</b>\n"
        f"Статус: <b>{status_label}</b>",
        reply_markup=promo_code_card_menu(promo_id, promo["status"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("promo:toggle:"))
async def promo_toggle_handler(call: CallbackQuery) -> None:
    promo_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    promo = await get_author_promo_code(user["id"], promo_id)
    if not promo:
        await call.answer("Промокод не найден", show_alert=True)
        return
    new_status = "paused" if promo["status"] == "active" else "active"
    await set_author_promo_status(user["id"], promo_id, new_status)
    await call.answer("Промокод приостановлен" if new_status == "paused" else "Промокод снова активен")
    promo = await get_author_promo_code(user["id"], promo_id)
    status_label = "активен" if promo["status"] == "active" else "приостановлен"
    left = max(0, int(promo["max_uses"] or 0) - int(promo["used_count"] or 0))
    await call.message.edit_text(
        "<b>🎟 Промокод</b>\n\n"
        f"Код: <code>{promo['code']}</code>\n"
        f"Книга: <b>{promo['book_title']}</b>\n"
        f"Скидка: <b>{promo['discount_percent']}%</b>\n"
        f"Использовано: <b>{promo['used_count']}</b>\n"
        f"Осталось: <b>{left}</b>\n"
        f"Статус: <b>{status_label}</b>",
        reply_markup=promo_code_card_menu(promo_id, promo["status"]),
    )


@router.callback_query(F.data == "author:profile")
async def author_profile_view(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    profile = await get_author_profile(user["id"])
    if not profile:
        await call.message.edit_text("Профиль автора ещё не создан.", reply_markup=author_menu(False))
    else:
        await call.message.edit_text(
            "<b>👤 Профиль автора</b>\n\n"
            f"Псевдоним: <b>{profile['pen_name']}</b>\n"
            f"Описание: <b>{profile['bio'] or 'не указано'}</b>\n"
            f"Страна: <b>{profile['country'] or 'не указана'}</b>\n"
            f"18+: <b>{'да' if profile['is_adult'] else 'нет'}</b>\n"
            f"Статус: <b>{profile['status']}</b>\n\n"
            "Выберите, что изменить. Данные обновятся сразу и будут использоваться во всех новых книгах.",
            reply_markup=author_profile_menu(),
        )
    await call.answer()


async def _update_profile_field(call_or_message, *, pen_name=None, bio=None, country=None, is_adult=None) -> bool:
    tg = call_or_message.from_user
    user = await upsert_user(tg.id, tg.username, tg.full_name)
    profile = await get_author_profile(user["id"])
    if not profile:
        return False
    await update_author_profile(
        user_id=user["id"],
        pen_name=pen_name if pen_name is not None else profile["pen_name"],
        bio=bio if bio is not None else (profile["bio"] or ""),
        country=country if country is not None else (profile["country"] or ""),
        is_adult=bool(is_adult) if is_adult is not None else bool(profile["is_adult"]),
    )
    return True


@router.callback_query(F.data == "profile:edit:pen_name")
async def profile_edit_pen_name_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditAuthorProfile.pen_name)
    await call.message.edit_text("Введите новый псевдоним автора.", reply_markup=skip_back_menu("profile:cancel_edit", "author:profile", "Отмена"))
    await call.answer()


@router.message(EditAuthorProfile.pen_name)
async def profile_edit_pen_name_save(message: Message, state: FSMContext) -> None:
    pen_name = (message.text or "").strip()
    if len(pen_name) < 2:
        await message.answer("Псевдоним слишком короткий. Введите от 2 символов.", reply_markup=navigation_menu(cancel_callback="profile:cancel_edit"))
        return
    ok = await _update_profile_field(message, pen_name=pen_name[:80])
    await state.clear()
    await message.answer("Псевдоним обновлён." if ok else "Профиль автора не найден.", reply_markup=author_menu(ok))


@router.callback_query(F.data == "profile:edit:bio")
async def profile_edit_bio_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditAuthorProfile.bio)
    await call.message.edit_text("Введите новое описание автора или нажмите «Очистить».", reply_markup=skip_back_menu("profile:clear_bio", "author:profile", "Очистить"))
    await call.answer()


@router.message(EditAuthorProfile.bio)
async def profile_edit_bio_save(message: Message, state: FSMContext) -> None:
    ok = await _update_profile_field(message, bio=(message.text or "").strip()[:1000])
    await state.clear()
    await message.answer("Описание автора обновлено." if ok else "Профиль автора не найден.", reply_markup=author_menu(ok))


@router.callback_query(EditAuthorProfile.bio, F.data == "profile:clear_bio")
async def profile_edit_bio_clear(call: CallbackQuery, state: FSMContext) -> None:
    ok = await _update_profile_field(call, bio="")
    await state.clear()
    await call.message.edit_text("Описание очищено." if ok else "Профиль автора не найден.", reply_markup=author_menu(ok))
    await call.answer()


@router.callback_query(F.data == "profile:edit:country")
async def profile_edit_country_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditAuthorProfile.country)
    await call.message.edit_text("Введите страну или нажмите «Очистить».", reply_markup=skip_back_menu("profile:clear_country", "author:profile", "Очистить"))
    await call.answer()


@router.message(EditAuthorProfile.country)
async def profile_edit_country_save(message: Message, state: FSMContext) -> None:
    ok = await _update_profile_field(message, country=(message.text or "").strip()[:80])
    await state.clear()
    await message.answer("Страна обновлена." if ok else "Профиль автора не найден.", reply_markup=author_menu(ok))


@router.callback_query(EditAuthorProfile.country, F.data == "profile:clear_country")
async def profile_edit_country_clear(call: CallbackQuery, state: FSMContext) -> None:
    ok = await _update_profile_field(call, country="")
    await state.clear()
    await call.message.edit_text("Страна очищена." if ok else "Профиль автора не найден.", reply_markup=author_menu(ok))
    await call.answer()


@router.callback_query(F.data == "profile:edit:adult")
async def profile_edit_adult_start(call: CallbackQuery) -> None:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, мне есть 18", callback_data="profile:set_adult:yes")
    kb.button(text="Нет", callback_data="profile:set_adult:no")
    kb.button(text="⬅️ Назад", callback_data="author:profile")
    kb.adjust(1)
    await call.message.edit_text("Подтвердите возраст автора.", reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("profile:set_adult:"))
async def profile_edit_adult_save(call: CallbackQuery) -> None:
    ok = await _update_profile_field(call, is_adult=call.data.endswith(":yes"))
    await call.message.edit_text("Возрастной статус обновлён." if ok else "Профиль автора не найден.", reply_markup=author_menu(ok))
    await call.answer("Сохранено")


@router.callback_query(F.data == "profile:cancel_edit")
async def profile_edit_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Изменение отменено.", reply_markup=author_profile_menu())
    await call.answer()
